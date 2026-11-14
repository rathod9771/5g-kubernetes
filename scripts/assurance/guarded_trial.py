"""Run exactly one approved trial; read-only independent guards and host measurements."""

def main():
    import os,sys,json,time,sqlite3,subprocess,signal,resource,hashlib
    from pathlib import Path
    import argparse
    ROOT=Path(__file__).resolve().parents[2]
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preflight-dir',type=Path,default=ROOT/'.runtime/l3-4a-preflight')
    parser.add_argument('--trial-dir',type=Path,default=ROOT/'.runtime/l3-4a-trial')
    parser.add_argument('--duration',type=int,default=600)
    parser.add_argument('--max-cycles',type=int,default=10)
    cli_args=parser.parse_args()
    if not 1<=cli_args.duration<=600 or not 1<=cli_args.max_cycles<=10:parser.error('Bounded trial limits exceeded')
    folder=cli_args.preflight_dir
    trial_dir=cli_args.trial_dir
    sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'ran-selector')]
    os.environ['P0_RUNTIME_CONFIG']=str(ROOT/'.runtime/dashboard-service.json')
    from runtime_config import load_config
    from assurance.osm import ReadOnlyOSM
    from ran_health import established
    from .memory import tree_memory,process_memory,cgroup_memory
    from .diagnostics import diagnostic
    cfg=load_config();pre=json.loads((folder/'preflight.json').read_text());ns=pre['namespace'];ids={k:v['_id'] for k,v in pre['protected_ns'].items()}
    assert not (trial_dir).exists()
    # Independent guard reuses one verified TLS/token session; no auth retries.
    osm=ReadOnlyOSM(cfg,{'osm':{'active_instance_id':ids['ran'],'core_instance_id':ids['core']}})
    osm.paths.add('/nslcm/v1/ns_lcm_op_occs')
    guard_counts={'kubernetes_list_commands':0,'sctp_read_commands':0,'osm_gets':0,'osm_authentication':0}
    def query(path):
     osm.stats['gets']=0
     value=osm._get(path);guard_counts['osm_gets']+=1;guard_counts['osm_authentication']=osm.stats['authentication'];return value

    def read_cpu():
     values=[int(v) for v in Path('/proc/stat').read_text().splitlines()[0].split()[1:9]]
     return sum(values),values[3]+values[4]
    def pressure():
     line=Path('/proc/pressure/cpu').read_text().splitlines()[0].split()
     return {k:float(v) for k,v in (part.split('=') for part in line[1:])}
    def journal():
     from assurance.guard import read_guard
     return read_guard(ROOT/'.runtime/assurance.sqlite',trial_name=trial_dir.name)
    def protected_check():
     result=subprocess.run(['kubectl','--kubeconfig',cfg['OSM_KUBECONFIG_PATH'],'--request-timeout=5s','get','pods,services','-n',ns,'-o','json'],capture_output=True,timeout=12,check=True)
     guard_counts['kubernetes_list_commands']+=1;items=json.loads(result.stdout)['items'];pods={p['metadata']['name']:p for p in items if p['kind']=='Pod'}
     for name,old in pre['pods'].items():
      pod=pods[name];assert pod['metadata']['uid']==old['uid'],'Protected pod replaced'
      assert pod['status']['phase']=='Running' and all(c['ready'] for c in pod['status']['containerStatuses']),'Protected workload not Ready'
      assert any(c['type']=='Ready' and c['status']=='True' for c in pod['status']['conditions'])
      assert [{k:c.get(k) for k in ('name','restartCount','containerID','imageID')} for c in pod['status']['containerStatuses']]==old['containers'],'Protected container changed/restarted'
     for role in ('cu','du'):
      pod=next(p for p in pods.values() if p['metadata'].get('labels',{}).get('component')==role and p['metadata'].get('labels',{}).get('osm.etsi.org/ns-id')==ids['ran'])
      r=subprocess.run(['kubectl','--kubeconfig',cfg['OSM_KUBECONFIG_PATH'],'--request-timeout=5s','exec','-n',ns,pod['metadata']['name'],'--','cat','/proc/net/sctp/assocs'],capture_output=True,text=True,timeout=8,check=True)
      guard_counts['sctp_read_commands']+=1
      assert established(r.stdout,38472) and (established(r.stdout,38412,True) if role=='cu' else True),'F1/NGAP association lost'
     for ident in ids.values():
      obj=query('/nslcm/v1/ns_instances/'+ident)
      assert obj.get('nsState')=='READY' and obj.get('currentOperation')=='IDLE' and obj.get('operational-status')=='running','Independent protected NS check failed'
     ops=query('/nslcm/v1/ns_lcm_op_occs')
     own=[{k:o.get(k) for k in ('_id','nsInstanceId','operationState','lcmOperationType','startTime')} for o in ops if o.get('nsInstanceId') in ids.values()]
     assert sorted(own,key=lambda x:x['_id'])==sorted(pre['operations'],key=lambda x:x['_id']),'OSM operation history changed/conflict'
     return {'at':time.time(),'protected_pods':16,'f1_ngap_established':True,'osm_history_unchanged':True}

    marker_fd=os.open(folder/'guard-started.json',os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(marker_fd,'w') as marker:
        json.dump({'started_at':time.time(),'trials_authorized':1},marker);marker.flush();os.fsync(marker.fileno())
    log=open(folder/'trial-output.log','wb');os.chmod(log.name,0o600)
    command=['/usr/bin/timeout','--signal=TERM','--kill-after=5s',str(cli_args.duration+15)+'s',str(ROOT/'ran-selector/venv/bin/python3'),'-m','assurance.periodic','--stop-on-source-error','--alertmanager-url',pre['alertmanager_url'],'--interval','60','--duration',str(cli_args.duration),'--max-cycles',str(cli_args.max_cycles),'--evidence-dir',str(trial_dir)]
    env=dict(os.environ,PYTHONPATH=str(ROOT/'scripts'))
    guardian_baseline=process_memory(os.getpid(),pss=True)
    legacy_baseline=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    memory_samples=[];owned_identities={}
    start=time.monotonic();before=resource.getrusage(resource.RUSAGE_SELF);prev_cpu=read_cpu();samples=[];checks=[];seen=0;failure=None;seen_pids={};overlap_peak=0
    process=subprocess.Popen(command,env=env,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    (folder/'trial-process.json').write_text(json.dumps({'timeout_pid':process.pid,'command':command,'started_at':time.time()},indent=2))
    next_check=start
    unavailable_since=None;guard_diagnostic=None
    # Cancellation must use the same bounded child shutdown as guard failures.
    def cancel(signum, frame):
     raise KeyboardInterrupt('Guardian cancellation requested')
    previous_handlers={sig:signal.signal(sig,cancel) for sig in (signal.SIGTERM,signal.SIGINT)}
    try:
     while process.poll() is None:
      now=time.monotonic();total,idle=read_cpu();dt=total-prev_cpu[0];util=100*(dt-(idle-prev_cpu[1]))/dt if dt else None;prev_cpu=(total,idle)
      samples.append({'at':time.time(),'host_cpu_utilization_percent':util,'cpu_psi_some':pressure()})
      owned=tree_memory(process.pid,owned_identities)
      self_memory=process_memory(os.getpid(),pss=True)
      assert self_memory['rss_bytes']<=512*1024*1024 and (self_memory['image_hwm_bytes'] or 0)<=512*1024*1024,'Guardian image RSS budget exceeded'
      memory_samples.append({'at':time.time(),'guardian':self_memory,
          'trial_tree_rss_bytes':owned['rss_sum_bytes'],'trial_process_count':len(owned['processes']),
          'cgroup':cgroup_memory(os.getpid()),'sample_seconds':owned['sample_seconds']})
      live=[]
      for member in owned['processes']:
       try:
        # Bounded argv prefix, intended tree only; no global /proc scan.
        with Path('/proc/%s/cmdline' % member['pid']).open('rb') as stream:process_argv=stream.read(4096).split(b'\0')
        seen_pids[str(member['pid'])]=member['birth']
        if b'assurance.cli' in process_argv:live.append(member['pid'])
       except (FileNotFoundError,ProcessLookupError):pass
      overlap_peak=max(overlap_peak,len(live));assert len(live)<=1,'Overlapping assurance collectors'
      # These independent checks run before DB access, even during journal contention.
      if now>=next_check:
       checks.append(protected_check());next_check=now+60
      result=journal()
      pause=trial_dir/'PAUSE'
      if result['status']!='AVAILABLE':
       if pause.parent.exists():pause.touch(mode=0o600,exist_ok=True)
       if unavailable_since is None:unavailable_since=now
       if now-unavailable_since>=5:raise RuntimeError('Journal visibility unavailable beyond 5s; no health inferred')
       time.sleep(.5)
       continue
      unavailable_since=None
      if pause.exists():pause.unlink()
      telemetry=result['values']['telemetry'] or {};control=result['values']['control'] or {};cycles=result['values'].get('cycles',[])
      assert len(cycles)<=10 and now-start<cli_args.duration+15,'Approved trial bounds exceeded'
      assert control.get('mode')=='OBSERVE_ONLY' and not control.get('emergency_stop'),'Governance changed/stop requested'
      if telemetry.get('observed_at',0)>pre['observed_at']:
       for role in ('cu','du','fog','core'):
        health=telemetry.get('services',{}).get(role,{})
        assert health.get('status')!='DEGRADED','Protected service degraded'
       for source in telemetry.get('osm',{}).values():
        if source.get('provenance')=='LIVE':
         v=source['value'];assert v.get('nsState')=='READY' and v.get('currentOperation')=='IDLE' and v.get('operational-status')=='running','Protected NS unhealthy or lifecycle conflict'
      if len(cycles)>seen:
       seen=len(cycles);last=cycles[-1];print('Cycle',seen,last['status'],'wall',round(last['process'].get('wall_seconds',0),3),'CPU',round(last['process'].get('cpu_seconds',0),3),flush=True)
      time.sleep(5)
    except (Exception,KeyboardInterrupt) as error:
     guard_diagnostic=diagnostic(error,'guardian')
     failure=guard_diagnostic['exception_class']+': '+guard_diagnostic['message']
     print('Stopping trial:',failure,flush=True)
     process.send_signal(signal.SIGTERM)
    finally:
     for sig,handler in previous_handlers.items():signal.signal(sig,handler)
     try:process.wait(timeout=20)
     except subprocess.TimeoutExpired:
      process.kill();process.wait();failure=failure or 'Trial stop timeout'
     log.close();after=resource.getrusage(resource.RUSAGE_SELF)
     final_memory=process_memory(os.getpid())
     if final_memory['rss_bytes']>512*1024*1024 or (final_memory['image_hwm_bytes'] or 0)>512*1024*1024:
      failure=failure or 'Guardian current-image memory budget exceeded'

     result={'elapsed_seconds':time.monotonic()-start,'exit_code':process.returncode,'guard_failure':failure,'guard_diagnostic':guard_diagnostic,'host_samples':samples,'protected_checks':checks,'guard_request_counts':guard_counts,'peak_simultaneous_collectors':overlap_peak,'observed_process_ids':seen_pids,'guard_cpu_seconds':after.ru_utime+after.ru_stime-before.ru_utime-before.ru_stime,'guardian_image_hwm_bytes':final_memory['image_hwm_bytes'],
             'guardian_sampled_peak_rss_bytes':max((m['guardian']['rss_bytes'] for m in memory_samples),default=guardian_baseline['rss_bytes']),
             'guardian_baseline':guardian_baseline,'memory_samples':memory_samples,
             'legacy_lifetime_self_maxrss_kib':after.ru_maxrss,'legacy_lifetime_baseline_kib':legacy_baseline}
     (folder/'runtime-guard.json').write_text(json.dumps(result,indent=2));(folder/'runtime-guard.json').chmod(0o600)
     print('Trial exited:',process.returncode,'guard failure:',failure,flush=True)
     if failure or process.returncode:sys.exit(1)

if __name__=="__main__":
    try:main()
    except Exception as error:
        import json,sys
        from .diagnostics import diagnostic
        print(json.dumps(diagnostic(error,'guardian_startup')),file=sys.stderr)
        sys.exit(1)
