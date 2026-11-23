"""Real-library selection + curl HTTP/2 static control, never a UDM transaction."""
import hashlib,json,os,pathlib,subprocess,sys,time
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'open5gs-udm-runtime'))
from runtime import docker, cleanup
from verify_artifact import IMAGE, digest

PORTS={'A':7777,'B':0,'C':0,'D':80,'E':8888,'F':7777}

def admit(case, data):
    if data != {'case':case,'status':'PASS','port':PORTS[case],
                'prior_port':7777 if case=='E' else 0,'kind':'actual-library-driver'}:
        raise ValueError('driver result mismatch')
    return data

def probe(network, port):
    # Constant non-subscriber path, no credentials, no SBI request bodies.
    raw=docker('run','--rm','--network',network,'--cpus','0.5','--memory','128m',
        '--memory-swap','128m','--pids-limit','16','--cap-drop','ALL','--read-only',
        '--security-opt','no-new-privileges','--log-driver','none',
        '--entrypoint','/usr/bin/curl',IMAGE,'--noproxy','*','--silent','--show-error',
        '--http2-prior-knowledge','--max-time','3','--max-filesize','1024',
        '--write-out','\n%{json}','http://172.29.249.2:'+str(port)+'/control.txt')
    if len(raw)>8192:raise ValueError('probe output limit')
    body,meta=raw.split(b'\n',1);m=json.loads(meta)
    if body!=b'non-subscriber-static-control' or m['http_code']!=200 or m['http_version']!='2' or m['remote_port']!=port or m['remote_ip']!='172.29.249.2':
        raise ValueError('HTTP2 static fixture mismatch')
    return {k:m[k] for k in ('remote_ip','remote_port','http_code','http_version','time_total')}

def run(out):
    if os.environ.get('GITHUB_ACTIONS')!='true' or os.environ.get('RUNNER_ENVIRONMENT')!='github-hosted' or os.environ.get('GITHUB_REF')!='refs/heads/experimental/open5gs-udm-sbi-20261123':
        raise RuntimeError('hosted experimental runner required')
    identity=os.environ['GITHUB_RUN_ID']+'-'+os.environ['GITHUB_RUN_ATTEMPT']
    if not all(c in '0123456789-' for c in identity):raise ValueError('run identity')
    network='l3-udm-driver-'+identity;name=network+'-fixture';created=False;names=[]
    report={'daemon_A_F':'BLOCKED: no subscriber context authorized','tests':{},
        'scope':'actual preserved SBI-library selection with manually constructed metadata; curl wire control against static nghttpd, not daemon NRF/UDR',
        'image':IMAGE,'subscriber_context_used':False}
    code=1;start=time.monotonic()
    try:
        binary=out/'selection-driver'
        expected=(out/'driver-sha256.txt').read_text().split()[0]
        if digest(binary)!=expected:raise ValueError('driver checksum')
        report['driver_sha256']=expected
        docker('network','create','--internal','--subnet','172.29.249.0/24',network);created=True
        if not json.loads(docker('network','inspect',network))[0]['Internal']:raise ValueError('network isolation')
        fixture=out/'fixture';fixture.mkdir();(fixture/'control.txt').write_text('non-subscriber-static-control')
        names.append(name)
        script=pathlib.Path(__file__).parent/'fixture.sh'
        docker('run','-d','--name',name,'--network',network,'--ip','172.29.249.2',
            '--cpus','0.5','--memory','128m','--memory-swap','128m','--pids-limit','16',
            '--cap-drop','ALL','--cap-add','NET_BIND_SERVICE','--security-opt','no-new-privileges',
            '--read-only','--tmpfs','/tmp:rw,noexec,nosuid,size=8m','--log-driver','none',
            '-v',str(fixture.resolve())+':/fixture:ro','-v',str(script.resolve())+':/fixture.sh:ro',
            '--entrypoint','/bin/sh','l3-udm-driver-toolchain','/fixture.sh')
        # Bounded fixture readiness only; cases themselves never retried.
        for attempt in range(10):
            try:probe(network,7777);break
            except Exception:
                if attempt==9:raise
                time.sleep(.1)
        for case in 'ABCDEF':
            raw=docker('run','--rm','--network','none','--cpus','0.5','--memory','128m','--memory-swap','128m',
                '--pids-limit','16','--cap-drop','ALL','--security-opt','no-new-privileges','--read-only',
                '--log-driver','none','-v',str(binary.resolve())+':/driver:ro','--entrypoint','/driver',IMAGE,case)
            if len(raw)>1024:raise ValueError('driver output limit')
            data=admit(case,json.loads(raw));data['daemon_status']='BLOCKED'
            data['metadata']='manual fixture, not NRF discovery'
            if data['port']: data['curl_static_http2_control']=probe(network,data['port'])
            else:data['curl_static_http2_control']='NOT SENT: library returned NULL'
            if case=='D':data['limitation']='Matching wrong-port client remains port 80; not repaired'
            if case=='E':data['limitation']='manual metadata replacement only; daemon cache/NRF refresh untested'
            report['tests'][case]=data
        code=0
    except Exception as e:
        report['error']=str(e) if isinstance(e,(RuntimeError,ValueError)) else type(e).__name__
    finally:
        failures=cleanup(names,network if created else None)
        report['cleanup']={'status':'FAIL' if failures else 'PASS','remaining':failures}
        report['wall_seconds']=round(time.monotonic()-start,3)
        report['driver_result']='PASS' if code==0 and not failures else 'FAIL'
        report['deployment_decision']='NO-GO: daemon A-F untested; wrong matching client unresolved'
        (out/'driver-results.json').write_text(json.dumps(report,indent=2)+'\n')
    return code if not failures else 1

if __name__=='__main__':sys.exit(run(pathlib.Path(sys.argv[1])))
