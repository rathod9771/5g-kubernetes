"""Opt-in finite observe-only trial. Never installs a watcher, timer or service."""
import argparse
import json
import os
from pathlib import Path
import resource
import signal
import sys
import threading
import time
from .kubernetes import KubernetesReader, KubernetesReadError
from .locking import exclusive
from .store import Store, JournalBusy
import sqlite3


def collection_error(telemetry):
    required=('kubernetes','nodes','kubernetes_warnings','prometheus','prometheus_targets','alertmanager','alert_groups')
    return (any(telemetry.get(key,{}).get('provenance')!='LIVE' for key in required)
            or not telemetry.get('osm')
            or any(v.get('provenance')!='LIVE' for v in telemetry.get('osm',{}).values()))


class Trial:
    def __init__(self, runtime, evidence, endpoint, *, interval=60, duration=600,
                 max_cycles=10, stop=None, clock=time.monotonic, wall=time.time,
                 execute=None, wait=None, stop_on_source_error=False):
        if interval not in (60,90,120) or not 1<=duration<=600 or not 1<=max_cycles<=10:
            raise ValueError('Trial must be bounded: interval 60/90/120, <=600s, <=10 cycles')
        self.stop_on_source_error=stop_on_source_error
        self.runtime,self.evidence=Path(runtime),Path(evidence)
        self.endpoint=endpoint;self.interval,self.duration,self.max_cycles=interval,duration,max_cycles
        self.stop=stop or threading.Event();self.clock,self.wall=clock,wall
        self.execute=execute or self._execute;self.wait=wait or self.stop.wait
    def _execute(self, remaining):
        runner=KubernetesReader({},'',timeout=min(90,remaining),max_rss=512*1024*1024)
        before=resource.getrusage(resource.RUSAGE_CHILDREN);started=self.clock()
        try:
            telemetry=runner.run_json([sys.executable,'-m','assurance.cli','--observe-once',
                '--alertmanager-url',self.endpoint],stop_event=self.stop)
            return telemetry
        finally:
            after=resource.getrusage(resource.RUSAGE_CHILDREN)
            self.process_stats={'wall_seconds':self.clock()-started,
                'cpu_seconds':after.ru_utime+after.ru_stime-before.ru_utime-before.ru_stime,
                'child_rusage_running_max_rss_kib':after.ru_maxrss,**runner.stats}
    def run(self):
        if resource.getrlimit(resource.RLIMIT_AS)[0]!=resource.RLIM_INFINITY:
            raise ValueError('Finite inherited RLIMIT_AS: stop without changing limits')
        with exclusive(self.runtime/'.l3-periodic.lock'):
            # Exclusive trial directory prevents accidental reuse/restart of this trial.
            self.evidence.mkdir(mode=0o700,parents=False,exist_ok=False)
            store=Store(self.runtime/'assurance.sqlite')
            def audit(kind,value):
                with store.transaction() as db:store.record(db,self.wall(),kind,self.evidence.name,value)
            def stopping():
                nonlocal reason
                if (self.evidence/'STOP').exists():self.stop.set()
                try:
                    with store.read() as db:
                        control=store.get(db,'control','governance',None)
                except JournalBusy:
                    reason='journal_unavailable';self.stop.set();return True
                if control is None:
                    reason='journal_unavailable';self.stop.set()
                elif control.get('emergency_stop') or control.get('mode')!='OBSERVE_ONLY':self.stop.set()
                return self.stop.is_set()
            start=self.clock();deadline=start+self.duration;due=start;failures=0;cycles=[];reason='duration'
            audit('periodic_started',{'mode':'OBSERVE_ONLY','interval':self.interval,
                  'duration':self.duration,'max_cycles':self.max_cycles,'executor':'DISABLED'})
            try:
                while self.clock()<deadline and len(cycles)<self.max_cycles and not stopping():
                    while self.clock()<min(due,deadline) and not stopping():
                        self.wait(min(1,due-self.clock(),deadline-self.clock()))
                    while (self.evidence/'PAUSE').exists() and self.clock()<deadline and not stopping():
                        self.wait(min(1,deadline-self.clock()))
                    if self.clock()>=deadline or stopping():break
                    cycle_start=self.clock();remaining=deadline-cycle_start
                    self.process_stats={};status='LIVE';fatal=False;telemetry={};failure=None
                    try:
                        telemetry=self.execute(remaining)
                        if collection_error(telemetry):
                            status='SOURCE_UNAVAILABLE';fatal=self.stop_on_source_error
                    except KubernetesReadError as error:
                        # Timeout/RSS/output/CPU/process violations terminate the trial.
                        from .diagnostics import diagnostic
                        failure=error.diagnostic or diagnostic(error,'bounded_process')
                        status='BOUNDED_PROCESS_FAILED';fatal=True
                    except Exception as error:
                        from .diagnostics import diagnostic
                        failure=diagnostic(error,'collection')
                        status='COLLECTION_FAILED';fatal=True
                    row={'cycle':len(cycles)+1,'at':self.wall(),'status':status,
                         'mode':'OBSERVE_ONLY','process':self.process_stats,'diagnostic':failure,
                         'sources':{k:{'provenance':telemetry.get(k,{}).get('provenance','UNAVAILABLE'),
                                       'observed_at':telemetry.get(k,{}).get('observed_at')}
                             for k in ('kubernetes','nodes','prometheus','alertmanager','kpm')},
                         'osm_sources':{k:{'provenance':v.get('provenance'), 'observed_at':v.get('observed_at')}
                                        for k,v in telemetry.get('osm',{}).items()},
                         'requests':{k:telemetry.get(k,{}) for k in
                                     ('kubernetes_request_stats','request_stats','osm_request_stats')},
                         'cpu_pressure':telemetry.get('cpu_pressure',{}),
                         'services':telemetry.get('services',{}),
                         'protected_restart_counts':{p['name']:p['restarts'] for p in
                             telemetry.get('kubernetes',{}).get('value') or []
                             if p['name'].startswith('open5gs') or p.get('labels',{}).get('osm.etsi.org/ns-id')
                                == '2c128db3-e27e-4dbe-8854-17751ba17680'}}
                    cycles.append(row);audit('periodic_cycle',row)
                    if fatal or self.stop.is_set():reason='shutdown' if self.stop.is_set() else 'process_failure';break
                    failures=failures+1 if status!='LIVE' else 0
                    delay=min(240,self.interval*2**min(failures,2))
                    # Start-to-start scheduling, no catch-up loop after a slow cycle.
                    due=max(cycle_start+delay,self.clock()+1)
                if self.stop.is_set() and reason!='journal_unavailable':reason='shutdown'
                elif len(cycles)>=self.max_cycles and reason=='duration':reason='max_cycles'
            finally:
                result={'mode':'OBSERVE_ONLY','reason':reason,'cycles':cycles,
                        'elapsed_seconds':self.clock()-start,'executor':'DISABLED','live_auto_enabled':False}
                try:audit('periodic_stopped',{'reason':reason,'cycles':len(cycles)})
                except sqlite3.OperationalError:
                    result['stop_audit_status']='UNAVAILABLE: journal write contention; local result preserved'
                target=self.evidence/'result.json'
                fd=os.open(target,os.O_CREAT|os.O_WRONLY|os.O_EXCL,0o600)
                with os.fdopen(fd,'w') as stream:
                    json.dump(result,stream,indent=2);stream.flush();os.fsync(stream.fileno())
            return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--alertmanager-url',required=True)
    parser.add_argument('--evidence-dir',type=Path,required=True)
    parser.add_argument('--interval',type=int,choices=(60,90,120),default=60)
    parser.add_argument('--duration',type=int,default=600)
    parser.add_argument('--max-cycles',type=int,default=10)
    parser.add_argument('--stop-on-source-error',action='store_true')
    args=parser.parse_args()
    from runtime_config import load_config
    cfg=load_config(require=('kubernetes','osm'))
    stop=threading.Event()
    for signum in (signal.SIGINT,signal.SIGTERM):signal.signal(signum,lambda *_:stop.set())
    # CPU clocks reset in each exec child; the supervisor's own CPU is capped too.
    resource.setrlimit(resource.RLIMIT_CPU,(30,30))
    result=Trial(cfg['RUNTIME_DIR'],args.evidence_dir,args.alertmanager_url,
        interval=args.interval,duration=args.duration,max_cycles=args.max_cycles,stop=stop,stop_on_source_error=args.stop_on_source_error).run()
    print(json.dumps(result,indent=2))
    if result['reason'] in ('process_failure','journal_unavailable'):sys.exit(1)

if __name__=='__main__':main()
