"""Actual UDM transactions; secret material only in host /dev/shm and daemon tmpfs."""
import datetime,json,os,pathlib,secrets,shutil,signal,subprocess,sys,tempfile,time,uuid
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'open5gs-udm-runtime'))
from runtime import docker,cleanup,config
# Bind solely to the freshly built candidate, never the original image pin.
IMAGE=json.loads((pathlib.Path(sys.argv[1])/'candidate.json').read_text())['image_id']
if not IMAGE.startswith('sha256:') or len(IMAGE)!=71:raise ValueError('candidate identity')
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'open5gs-udm-integration'))
from sbi_fixture import profile,UDR_ID

ROOT=pathlib.Path(__file__).parent

def save(path,value):
    tmp=path.with_suffix('.pending');tmp.write_text(json.dumps(value));tmp.replace(path)

def state_of(name):
    s=json.loads(docker('inspect',name))[0]
    return {'running':s['State']['Running'],'exit':s['State']['ExitCode'],
            'oom_killed':s['State']['OOMKilled'],'restarts':s['RestartCount'],'pid':s['State']['Pid']}

def curl_cfg(path,url,body=None):
    # Private config avoids secret URLs or data in process argv and GitHub logs.
    lines=['url = '+json.dumps(url),'http2-prior-knowledge','silent','show-error',
           'max-time = 12','output = /dev/null','write-out = "%{json}"','noproxy = "*"']
    if body is not None:lines+=['request = POST','header = "Content-Type: application/json"','data = '+json.dumps(json.dumps(body))]
    path.write_text('\n'.join(lines)+'\n');path.chmod(0o600)

def request(network,private,cfg):
    p=subprocess.run(['docker','run','--rm','--name',network+'-originator-'+cfg.stem,'--network',network,'--user',str(os.getuid())+':'+str(os.getgid()),
        '--cpus','0.5','--memory','128m','--memory-swap','128m','--pids-limit','16','--cap-drop','ALL',
        '--read-only','--security-opt','no-new-privileges','--log-driver','none',
        '-v',str(private)+':/private:rw','--entrypoint','/usr/bin/curl',IMAGE,'--config','/private/'+cfg.name,'--max-filesize','65536','--output','/private/response.json','--dump-header','/private/headers.txt'],
        capture_output=True,timeout=16)
    if len(p.stdout)>16384 or len(p.stderr)>4096:raise RuntimeError('originator output limit')
    try:m=json.loads(p.stdout)
    except Exception:m={}
    response=private/'response.json'
    headers=private/'headers.txt'
    count=None
    if headers.exists():
        headers.chmod(0o600);h=headers.read_bytes()
        if len(h)>16384:raise RuntimeError('header limit')
        count=sum(line.startswith(b'HTTP/') for line in h.splitlines());headers.unlink()
    safe={'response_count':count}
    if response.exists():
        response.chmod(0o600)
        raw=response.read_bytes()
        if len(raw)>65536:raise RuntimeError('response limit')
        if m.get('http_code')==200 and cfg.stem=='attempt':
            body=json.loads(raw);v=body.get('authenticationVector',{})
            safe={'authentication_result_valid':body.get('authType')=='5G_AKA' and v.get('avType')=='5G_HE_AKA' and all(isinstance(v.get(k),str) and len(v[k])==n and all(c in '0123456789abcdefABCDEF' for c in v[k]) for k,n in [('rand',32),('autn',32),('xresStar',32),('kausf',64)])}
        if m.get('http_code')==503:
            body=json.loads(raw)
            safe={'problem_status':body.get('status'),
                  'generic_title':body.get('title')=='Required UDR service unavailable',
                  'only_generic_fields':set(body)<= {'status','title'}}
        response.unlink()
    # curl %{json} includes url_effective and other sensitive fields: NEVER persist it.
    return {**safe,'curl_exit':p.returncode,**{k:m.get(k) for k in ('http_code','http_version','remote_ip','remote_port','time_total')}}

def events(name):
    raw=docker('exec',name,'/bin/cat','/tmp/events.json')
    if len(raw)>200000:raise RuntimeError('sanitized event limit')
    data=json.loads(raw)
    if not data.get('complete') or data.get('errors'):raise RuntimeError('fixture evidence incomplete')
    return data['events']

def start(name,network,ip,image,entry,args,mounts):
    if image=='l3-udm-stage8-fixture':mounts=[*mounts,(ROOT/'sbi_fixture.py','/fixture/sbi_fixture.py')]
    docker('run','-d','--name',name,'--network',network,'--ip',ip,'--cpus','0.5',
        '--memory','512m','--memory-swap','512m','--pids-limit','64',
        '--cap-drop','ALL','--cap-add','NET_BIND_SERVICE','--read-only','--security-opt','no-new-privileges',
        '--tmpfs','/tmp:rw,noexec,nosuid,size=32m','--log-driver','none',
        *sum((['-v',str(a)+':'+b+':ro'] for a,b in mounts),[]),
        '--entrypoint',entry,image,*args)

def listener_ready(network,private,name,real_nrf=False):
    # Non-subscriber GETs only. Never retry a subscriber trial.
    cfg=private/'ready.conf'
    curl_cfg(cfg,'http://172.29.250.'+('2:9000/nnrf-nfm/v1/nf-instances' if real_nrf else '3:7777/nudm-sdm/v2'))
    for _ in range(12):
        if not state_of(name)['running']:raise RuntimeError('daemon exited during startup')
        r=request(network,private,cfg)
        if r['curl_exit']==0 and r['http_version']=='2' and r['http_code']==(200 if real_nrf else 404):return {**r,'readiness_probes':_+1}
        time.sleep(.15)
    raise RuntimeError('listener readiness deadline')

def trial(case,out,runid):
    network='l3-udm-stage8-'+runid+'-'+case.lower();names=[];created=False
    private=pathlib.Path(tempfile.mkdtemp(prefix='l3-stage8-'+runid+'-'+case.lower()+'-',dir='/dev/shm'));private.chmod(0o700)
    control=private/'control';control.mkdir(mode=0o755)
    state={'case':case,'port':80 if case=='D' else 7777};save(control/'state.json',state);(control/'state.json').chmod(0o644)
    result={'case':case,'status':'BLOCKED','execution':'actual-UDM-daemon',
            'NRF':'actual Open5GS' if case=='F' else 'controlled HTTP2 fixture',
            'UDR':'not required' if case=='F' else 'HTTP2 synthetic subscription fixture (A); bounded 404 fixture (D/E); no HP data',
            'subscriber_attempts':0,'precondition':'fresh network, fresh UDM process and context; no prior cache'}
    result['started_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat()
    save(out/('case-'+case+'.json'),result)
    started=time.monotonic()
    try:
        docker('network','create','--internal','--subnet','172.29.250.0/24',network);created=True
        if not json.loads(docker('network','inspect',network))[0]['Internal']:raise RuntimeError('isolation failed')
        nf=network+'-nf';names.append(nf)
        if case=='F':
            p=control/'nrf.yaml';p.write_text(config('nrf','172.29.250.2').replace('port: 7777','port: 9000'));p.chmod(0o644)
            start(nf,network,'172.29.250.2',IMAGE,'/opt/open5gs/bin/open5gs-nrfd',['-c','/config.yaml'],[(p,'/config.yaml')])
            result['nrf_startup']=listener_ready(network,private,nf,True)
        else:
            start(nf,network,'172.29.250.2','l3-udm-stage8-fixture','/usr/bin/python3',
                ['-B','/fixture/sbi_fixture.py'],[(control,'/control')])
            # Fixture readiness: wait only for sanitized state file, no subscriber requests.
            for n in range(15):
                p=subprocess.run(['docker','exec',nf,'/usr/bin/test','-s','/tmp/events.json'],capture_output=True,timeout=3)
                if p.returncode==0:break
                if n==14:raise RuntimeError('fixture initialization deadline')
                time.sleep(.1)
        udm=network+'-udm';names.append(udm)
        p=control/'udm.yaml';p.write_text(config('udm','172.29.250.3','172.29.250.2').replace('http://172.29.250.2:7777','http://172.29.250.2:9000'));p.chmod(0o644)
        start(udm,network,'172.29.250.3',IMAGE,'/bin/sh',['-c','umask 077; /opt/open5gs/bin/open5gs-udmd -c /config.yaml & p=$!; echo "$p" > /tmp/app.pid; wait "$p"; r=$?; echo "$r" > /tmp/app.exit; sleep 20'],[(p,'/config.yaml')])
        result['startup']=listener_ready(network,private,udm);result['initial_process']=state_of(udm)
        if case=='F':
            # Actual NRF confirms real UDM registration and advertised service identities.
            raw=docker('exec',nf,'/usr/bin/curl','--noproxy','*','--silent','--show-error','--max-time','3',
                '--http2-prior-knowledge','http://172.29.250.2:9000/nnrf-disc/v1/nf-instances?target-nf-type=UDM&requester-nf-type=AMF')
            d=json.loads(raw);instances=[x for x in d.get('nfInstances',[]) if x.get('nfType')=='UDM' and x.get('nfStatus')=='REGISTERED']
            result['registration']=[{'type':x['nfType'],'status':x['nfStatus'],
                'services':[s.get('serviceName') for s in x.get('nfServices',[])]} for x in instances]
            result['registration_check']='PASS' if instances else 'FAIL'
            result['status']='PARTIAL' if instances else 'FAIL'
            result['scope']='actual UDM NNrf registration with real NRF only; other unrelated operations untested'
        else:
            # Generate fresh fabricated identifier and unused test-only key context.
            # All values remain only in tmpfs; none is serialized to evidence.
            identity='imsi-99970'+''.join(secrets.choice('0123456789') for _ in range(10))
            auth=private/'test-auth.json';save(auth,{'key':secrets.token_hex(16),'opc':secrets.token_hex(16)});auth.chmod(0o600)
            result['synthetic_auth']={'generated':True,'used_for_vector_generation':case=='A','reason':'A uses fixture-generated ephemeral synthetic context; other cases stop before vector generation'}
            cfg=private/'attempt.conf';curl_cfg(cfg,'http://172.29.250.3:7777/nudm-ueau/v1/'+identity+'/security-information/generate-auth-data',
                {'servingNetworkName':'5G:mnc070.mcc999.3gppnetwork.org','ausfInstanceId':str(uuid.uuid4())})
            # Wait for registration/subscriptions, using sanitized metadata only.
            for n in range(15):
                ev=events(nf)
                if any(e['operation']=='nf-register' for e in ev):break
                if n==14:raise RuntimeError('UDM registration missing')
                time.sleep(.1)
            result['attempt_1']=request(network,private,cfg);result['subscriber_attempts']=1
            ev=events(nf);transactions=[e for e in ev if e['operation']=='authentication-subscription' and e['source_nf']=='UDM']
            result['transactions_before_update']=transactions
            if case=='E':
                state['port']=8888;save(control/'state.json',state);(control/'state.json').chmod(0o644)
                notification={'event':'NF_REGISTERED','nfInstanceUri':'http://172.29.250.2:9000/nnrf-nfm/v1/nf-instances/'+UDR_ID,'nfProfile':profile('E',8888)}
                # Explicit protocol notification from the NRF fixture's network identity.
                p=control/'notify.json';save(p,notification);p.chmod(0o644)
                raw=docker('exec',nf,'/usr/bin/curl','--noproxy','*','--silent','--show-error','--http2-prior-knowledge',
                    '--max-time','3','-o','/dev/null','-w','%{http_code}','-X','POST','-H','Content-Type: application/json',
                    '--data-binary','@/control/notify.json','http://172.29.250.3:7777/nnrf-nfm/v1/nf-status-notify')
                result['notification_status']=int(raw);result['refresh_mechanism']='explicit NRF-fixture NF_REGISTERED profile-update notification; no restart'
                result['attempt_2']=request(network,private,cfg);result['subscriber_attempts']=2
                result['transactions_after_update']=[e for e in events(nf) if e['operation']=='authentication-subscription' and e['source_nf']=='UDM'][len(transactions):]
            result['events']=events(nf);result['final_process']=state_of(udm)
            ports=[e['destination_port'] for e in transactions]
            no_crash=result['final_process']['running'] and not result['final_process']['oom_killed']
            if case=='A':ok=ports==[7777] and result['attempt_1']['http_code']==200 and result['attempt_1'].get('authentication_result_valid')
            elif case=='D':ok=ports==[80] and result['attempt_1']['http_code']==404
            elif case in ('B','C'):
                a=result['attempt_1']
                ok=not ports and any(e['operation']=='discovery' for e in ev) and a['curl_exit']==0 and a['http_code']==503 and a['time_total']<2 and a.get('problem_status')==503 and a.get('generic_title') and a.get('only_generic_fields') and a.get('response_count')==1
                result['negative_scope']='complete bounded listener observation; discovery fixture returned missing/incompatible profile; no port-80 UDR request'
            else:ok=ports==[7777] and [e['destination_port'] for e in result['transactions_after_update']]==[8888] and result['notification_status']==204 and result['attempt_2']['http_code']==404 and result['initial_process']['pid']==result['final_process']['pid']
            result['status']='PASS' if ok and no_crash else 'FAIL'
            if case=='D':result['limitation']='matching incorrect service still selects port 80; expected known limitation, not a correction'
    except Exception as e:
        result['status']='FAIL';result['error_class']=type(e).__name__
        # Never persist arbitrary exception text: it could contain synthetic material.
    finally:
        if names:
            try:
                result['udm_state_at_cleanup']=state_of(names[-1])
                # Counts only; raw daemon logs may contain the fabricated identifier.
                raw=docker('exec',names[-1],'/bin/cat','/tmp/udm.log')
                result['daemon_log_markers']={s:raw.count(s.encode()) for s in ('ERROR','FATAL','No Client','Not found','NF registered','HTTP response error','Cannot find client','NF discover','CLIENT_WAIT')}
            except Exception:result['daemon_log_markers']='UNAVAILABLE'
        # Gracefully stop only this disposable daemon, retain tmpfs long enough
        # for allowlisted shutdown diagnostics, then remove the experiment.
        try:
            target=network+'-udm'
            docker('exec',target,'/bin/sh','-c','kill -TERM "$(cat /tmp/app.pid)"')
            exit_code=None
            for _ in range(30):
                q=subprocess.run(['docker','exec',target,'/bin/cat','/tmp/app.exit'],capture_output=True,timeout=3)
                if q.returncode==0:
                    exit_code=int(q.stdout.strip());break
                time.sleep(.1)
            raw=docker('exec',target,'/bin/cat','/tmp/udm.log')
            leaks=raw.count(b'pool') if b'were not released' in raw else 0
            result['graceful_shutdown']={'exit':exit_code,'pool_leak_marker':b'were not released' in raw,'fatal_count':raw.count(b'FATAL')}
            if exit_code!=0 or leaks or b'FATAL' in raw:result['status']='FAIL'
        except Exception as e:
            result['graceful_shutdown']={'error_class':type(e).__name__};result['status']='FAIL'
        originators=[]
        for suffix in ('ready','attempt'):
            n=network+'-originator-'+suffix
            p=subprocess.run(['docker','container','inspect',n],capture_output=True,timeout=5)
            if p.returncode==0:originators.append(n)
        failures=cleanup(names+originators,network if created else None)
        shutil.rmtree(private)
        result['cleanup']={'docker':'PASS' if not failures else 'FAIL','tmpfs_secret_directory_absent':not private.exists()}
        if failures:result['status']='FAIL'
        result['wall_seconds']=round(time.monotonic()-started,3)
        save(out/('case-'+case+'.json'),result)
    return result

def main(out):
    if os.environ.get('GITHUB_ACTIONS')!='true' or os.environ.get('RUNNER_ENVIRONMENT')!='github-hosted' or os.environ.get('GITHUB_REF')!='refs/heads/experimental/open5gs-udm-sbi-20261123':raise RuntimeError('hosted experimental runner required')
    identity=os.environ['GITHUB_RUN_ID']+'-'+os.environ['GITHUB_RUN_ATTEMPT']
    if not all(c in '0123456789-' for c in identity):raise ValueError('run identity')
    results=[]
    for case in 'ABCDEF':
        r=trial(case,out,identity);results.append(r)
        # Preserve all independent cases even if a negative case exposes a failure;
        # fresh containers/networks isolate state. No case retry is permitted.
    summary={'actual_daemon':True,'cases':{r['case']:r['status'] for r in results},
        'subscriber_attempts':sum(r['subscriber_attempts'] for r in results),
        'decision':'NO-GO: bounded mocked-UDR errors are not full NAS compatibility; matching wrong client remains unresolved',
        'privacy':'no generated identifier, key, vector, raw URI or body retained'}
    save(out/'integration-summary.json',summary)
    return 0 if all(r['status']=='PASS' or (r['case']=='F' and r['status']=='PARTIAL') for r in results) else 1
if __name__=='__main__':sys.exit(main(pathlib.Path(sys.argv[1])))
