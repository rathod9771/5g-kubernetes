"""Identity-checked bounded /proc accounting. RSS sum is a conservative watchdog metric."""
from pathlib import Path
import time

class MemoryMeasurementError(RuntimeError):pass


def birth(pid,proc=Path('/proc')):
    return (proc/str(pid)/'stat').read_text().rsplit(')',1)[1].split()[19]


def process_memory(pid,*,proc=Path('/proc'),pss=False):
    base=proc/str(pid);first=birth(pid,proc)
    lines=(base/'status').read_text().splitlines();fields={}
    for line in lines:
        if line.startswith(('VmRSS:','VmHWM:','VmSize:')):
            parts=line.split()
            if len(parts)!=3 or parts[2]!='kB':raise MemoryMeasurementError('Malformed process memory units')
            value=int(parts[1])*1024
            if value<0:raise MemoryMeasurementError('Negative process memory measurement')
            fields[parts[0][:-1]]=value
    exiting_without_memory=False
    state=next((line.split()[1] for line in lines if line.startswith('State:')),None)
    stat=(base/'stat').read_text().rsplit(')',1)[1].split()
    if stat[0] in ('Z','X'):state=stat[0]
    if 'VmRSS' not in fields and state not in ('Z','X'):
        # A fast exit may remove memory fields between status/stat reads.
        second=(base/'status').read_text().splitlines()
        rss_line=next((line for line in second if line.startswith('VmRSS:')),None)
        state=next((line.split()[1] for line in second if line.startswith('State:')),state)
        if rss_line is not None:fields['VmRSS']=int(rss_line.split()[1])*1024
        elif state not in ('Z','X'):
            # Linux can clear mm/RSS during do_exit before publishing zombie state.
            # PF_EXITING (sched.h 0x4) proves teardown; missing RSS for any live
            # process without that flag still fails closed. No wait/retry or quota change.
            latest=(base/'stat').read_text().rsplit(')',1)[1].split()
            exiting_without_memory=bool(int(latest[6]) & 0x00000004)
            if not exiting_without_memory:raise MemoryMeasurementError('RSS unavailable for live process')
    result={'pid':int(pid),'ppid':int(stat[1]),'birth':first,'state':state,'rss_bytes':fields.get('VmRSS',0),
            'image_hwm_bytes':fields.get('VmHWM'),'virtual_bytes':fields.get('VmSize'),
            'rollup_rss_bytes':None,'pss_bytes':None,'pss_status':'NOT_SAMPLED',
            'measurement_status':'EXITING_NO_ADDRESS_SPACE' if exiting_without_memory else 'MEASURED'}
    if pss and state!='Z':
        try:
            rows=(base/'smaps_rollup').read_text().splitlines()
            result['pss_bytes']=int(next(l.split()[1] for l in rows if l.startswith('Pss:')))*1024
            result['rollup_rss_bytes']=next((int(l.split()[1])*1024 for l in rows if l.startswith('Rss:')),None)
            result['pss_status']='AVAILABLE'
        except (OSError,StopIteration,ValueError):result['pss_status']='UNAVAILABLE'
    if birth(pid,proc)!=first:raise MemoryMeasurementError('PID changed during memory sample')
    return result


def tree_memory(pid,tracked=None,*,proc=Path('/proc'),pss=False,max_processes=32,max_tasks=128):
    tracked=tracked if tracked is not None else {};pending=[(int(pid),None)];seen=set();rows=[];tasks=0;exited=[]
    started=time.monotonic()
    while pending:
        current,parent=pending.pop()
        if current in seen:continue
        seen.add(current)
        if len(seen)>max_processes:raise MemoryMeasurementError('Process-tree budget exceeded')
        try:
            row=process_memory(current,proc=proc,pss=pss)
            if parent is not None and row['ppid']!=parent:
                raise MemoryMeasurementError('Descendant parent changed; refusing unrelated process')
            previous=tracked.get(current)
            if previous is not None and previous!=row['birth']:
                raise MemoryMeasurementError('PID reuse detected; refusing new identity')
            tracked[current]=row['birth']
            task_dirs=list((proc/str(current)/'task').iterdir())
            tasks+=len(task_dirs)
            if tasks>max_tasks:raise MemoryMeasurementError('Process-thread traversal budget exceeded')
            children=[]
            for task in task_dirs:
                try:children.extend(int(child) for child in (task/'children').read_text().split())
                except (FileNotFoundError,ProcessLookupError):pass
            if birth(current,proc)!=row['birth']:raise MemoryMeasurementError('PID changed during traversal')
            rows.append(row);pending.extend((child,current) for child in children)
        except (FileNotFoundError,ProcessLookupError):exited.append(current)
        except (OSError,ValueError,IndexError) as error:
            raise MemoryMeasurementError('Process memory unavailable; watchdog fails closed') from None
    if len(tracked)>256:raise MemoryMeasurementError('Retained process identity budget exceeded')
    return {'rss_sum_bytes':sum(r['rss_bytes'] for r in rows),
            'pss_sum_bytes':sum(r['pss_bytes'] for r in rows) if rows and all(r['pss_bytes'] is not None for r in rows) else None,
            'processes':rows,'exited_during_sample':exited,'sample_seconds':time.monotonic()-started,
            'semantics':'simultaneous RSS sum; shared mappings counted per process'}


def cgroup_memory(pid,root=Path('/sys/fs/cgroup'),proc=Path('/proc')):
    """Read-only context; never claimed exclusive to this application or reset."""
    try:
        line=next(l for l in (proc/str(pid)/'cgroup').read_text().splitlines() if l.startswith('0::'))
        relative=line.split('::',1)[1].lstrip('/');path=root/relative
        if '..' in Path(relative).parts:raise ValueError('Invalid cgroup path')
        values={}
        for name in ('memory.current','memory.peak'):
            try:values[name]=int((path/name).read_text().strip())
            except (OSError,ValueError):values[name]=None
        return {'status':'AVAILABLE' if values['memory.current'] is not None else 'UNAVAILABLE',
                **values,'scope':'existing cgroup including unrelated members and descendants; peak never reset'}
    except (OSError,ValueError,StopIteration):return {'status':'UNAVAILABLE','reason':'cgroup v2 memory files not accessible'}
