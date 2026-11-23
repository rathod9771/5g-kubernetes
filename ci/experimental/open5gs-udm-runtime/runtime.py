"""Disposable real UDM/NRF startup. Never a subscriber or NAS test."""
import json, os, pathlib, subprocess, sys, time
from verify_artifact import IMAGE

PREFIX = 'l3-udm-runtime-'
BLOCKER = ('BLOCKED: pinned UDM UDR-request routes require a UE/session context; '
           'this experiment forbids subscriber identifiers. No daemon selection claim.')

def command(args, timeout=15):
    p = subprocess.run(args, capture_output=True, timeout=timeout)
    if len(p.stdout)+len(p.stderr) > 1024*1024: raise RuntimeError('diagnostic size exceeded')
    if p.returncode: raise RuntimeError('command failed: '+args[0]+' exit '+str(p.returncode))
    return p.stdout

def docker(*args, timeout=15): return command(['docker', *args], timeout)

def config(nf, address, nrf=None):
    text = ('logger:\n  file:\n    path: /tmp/'+nf+'.log\n  level: info\n'
            'global:\n  max:\n    ue: 16\n'+nf+':\n  sbi:\n    server:\n'
            '      - address: '+address+'\n        port: 7777\n')
    if nrf: text += '    client:\n      nrf:\n        - uri: http://'+nrf+':7777\n'
    return text

def admit_probe(meta, status):
    if meta.get('http_version') != '2' or meta.get('http_code') != status:
        raise RuntimeError('HTTP/2 or status mismatch')
    if not 0 <= meta.get('time_total', -1) <= 5: raise RuntimeError('probe duration invalid')
    return {k:meta[k] for k in ('http_version','http_code','remote_ip','remote_port','time_total')}

def cleanup(names, network):
    failures = []
    for name in reversed(names):
        try: docker('rm','-f',name)
        except Exception: failures.append(name)
    if network:
        try: docker('network','rm',network)
        except Exception: failures.append(network)
    return failures

def run(out):
    if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
        raise RuntimeError('GitHub-hosted runner required')
    if os.environ.get('GITHUB_REF') != 'refs/heads/experimental/open5gs-udm-sbi-20261123':
        raise RuntimeError('branch mismatch')
    runid = os.environ['GITHUB_RUN_ID']+'-'+os.environ['GITHUB_RUN_ATTEMPT']
    if not all(c in '0123456789-' for c in runid): raise RuntimeError('invalid run identity')
    network = PREFIX+runid; names=[]; created=False
    report={'classification':'NO-GO','tests':{x:{'status':'BLOCKED','reason':BLOCKER} for x in 'ABCDE'},
            'F':{'status':'BLOCKED','reason':'NRF registration pending; not all unrelated service-selection operations'},
            'subscriber_data':False,'mocks':False,'production_access':False}
    start=time.monotonic()
    try:
        # No ports published; Docker internal bridge blocks external routing.
        docker('network','create','--internal','--subnet','172.29.248.0/24',network); created=True
        net=json.loads(docker('network','inspect',network))[0]
        if not net['Internal']: raise RuntimeError('network is not internal')
        report['network']={'internal':True,'id':net['Id']}
        info=json.loads(docker('image','inspect',IMAGE))[0]
        if info['Id'] != IMAGE: raise RuntimeError('loaded image mismatch')
        # Validate staged binary bytes in the loaded image; execute only hash utility here.
        # Actual library filename is obtained from reviewed artifact rather than guessed.
        pins=json.loads((pathlib.Path(__file__).parent/'artifact-pins.json').read_text())
        files=[n for n in pins if n.startswith('root/opt/open5gs/')]
        args=['run','--rm','--network','none','--cpus','0.5','--memory','128m','--pids-limit','32','--cap-drop','ALL',
              '--security-opt','no-new-privileges','--entrypoint','/usr/bin/sha256sum',IMAGE]
        actual=docker(*args,*['/'+n.removeprefix('root/') for n in files]).decode().splitlines()
        expected={n.removeprefix('root/'):pins[n] for n in files}
        for line in actual:
            value, name=line.split(maxsplit=1)
            if expected.pop(name.lstrip('/'), None) != value: raise RuntimeError('loaded binary mismatch')
        if expected: raise RuntimeError('incomplete loaded binary inventory')
        report['loaded_binary_integrity']='PASS'
        configs=out/'configs';configs.mkdir();configs.chmod(0o755)
        for nf,address in [('nrf','172.29.248.2'),('udm','172.29.248.3')]:
            path=configs/(nf+'.yaml');path.write_text(config(nf,address,'172.29.248.2' if nf=='udm' else None));path.chmod(0o644)
            name=network+'-'+nf
            # Track before start so cleanup also handles failed docker run.
            names.append(name)
            docker('run','-d','--name',name,'--network',network,'--ip',address,
                   '--cpus','0.5','--memory','512m','--memory-swap','512m','--pids-limit','64',
                   '--cap-drop','ALL','--security-opt','no-new-privileges','--read-only',
                   '--tmpfs','/tmp:rw,nosuid,noexec,size=32m','--log-driver','json-file',
                   '--log-opt','max-size=256k','--log-opt','max-file=1',
                   '-v',str(path.resolve())+':/config.yaml:ro','--entrypoint','/opt/open5gs/bin/open5gs-'+nf+'d',
                   IMAGE,'-c','/config.yaml')
            # Bounded readiness: real HTTP/2 listener, no subscriber URI/body.
            ready=False
            for _ in range(15):
                state=json.loads(docker('inspect',name))[0]['State']
                if not state['Running'] or state.get('OOMKilled'): raise RuntimeError(nf+' stopped during startup; exit='+str(state['ExitCode'])+' OOM='+str(state.get('OOMKilled')))
                probe=subprocess.run(['docker','exec',name,'/usr/bin/curl','--noproxy','*','--silent','--show-error',
                    '--http2-prior-knowledge','--max-time','2','-o','/dev/null','-w','%{json}',
                    'http://'+address+':7777/'+('nnrf-nfm/v1/nf-instances' if nf=='nrf' else 'nudm-sdm/v2')],capture_output=True,timeout=5)
                if probe.returncode == 0:
                    meta=json.loads(probe.stdout); report[nf+'_listener']=admit_probe(meta,200 if nf=='nrf' else 404);ready=True;break
                time.sleep(0.5)
            if not ready: raise RuntimeError(nf+' listener readiness deadline')
        # Query real NRF; retain only UDM registration/service projection, no raw bodies.
        registered=False
        for _ in range(10):
            body=docker('exec',names[0],'/usr/bin/curl','--noproxy','*','--fail','--silent','--show-error',
                '--http2-prior-knowledge','--max-time','2',
                'http://172.29.248.2:7777/nnrf-disc/v1/nf-instances?target-nf-type=UDM&requester-nf-type=AMF')
            data=json.loads(body)
            instances=[n for n in data.get('nfInstances',[]) if n.get('nfType')=='UDM' and n.get('nfStatus')=='REGISTERED']
            if instances:
                report['registration']=[{'nfType':n['nfType'],'nfStatus':n['nfStatus'],
                    'services':[{'name':s.get('serviceName'),'status':s.get('nfServiceStatus')} for s in n.get('nfServices',[])]} for n in instances]
                registered=True;break
            time.sleep(0.5)
        if not registered: raise RuntimeError('real NRF did not discover registered UDM')
        report['startup']='PASS'
        report['F']={'status':'PARTIAL','actual':'real UDM-to-NRF registration and HTTP/2 discovery PASS',
                     'limitation':'unrelated NF-level fallback selection not exercised by daemon'}
        report['resource_samples']=[]
        for name in names:
            inspect=json.loads(docker('inspect',name))[0]
            if not inspect['State']['Running'] or inspect['RestartCount'] or inspect['State'].get('OOMKilled'):
                raise RuntimeError('unexpected runtime failure')
            report['resource_samples'].append({'name':name,'limits':{k:inspect['HostConfig'][k] for k in ('Memory','MemorySwap','NanoCpus','PidsLimit')},
                'stats':json.loads(docker('stats','--no-stream','--format','{{json .}}',name))})
        # No raw daemon logs retained. Counts of known markers only; content never printed.
        report['diagnostics']=[]
        for name in names:
            p=subprocess.run(['docker','logs',name],capture_output=True,timeout=5)
            raw=p.stdout+p.stderr
            if p.returncode or len(raw)>=256*1024: raise RuntimeError('daemon diagnostics incomplete')
            report['diagnostics'].append({'name':name,'bytes':len(raw),'markers':{
                key:raw.count(key.encode()) for key in ('NF registered','nghttp2_server()','ERROR','FATAL')}})
    except Exception as e:
        report['error']=str(e) if isinstance(e,RuntimeError) else type(e).__name__
    finally:
        report['tests']['F']=report.pop('F')
        # Preserve sanitized partial diagnostics even when initialization fails.
        report['partial_diagnostics']=[]
        for name in names:
            try:
                p=subprocess.run(['docker','logs',name],capture_output=True,timeout=5)
                raw=p.stdout+p.stderr
                if len(raw)>=256*1024: raise RuntimeError('diagnostics truncated')
                report['partial_diagnostics'].append({'name':name,'exit':p.returncode,'bytes':len(raw),
                    'markers':{k:raw.count(k.encode()) for k in ('ERROR','FATAL','Cannot','Assertion','NF registered','nghttp2_server()')}})
            except Exception as e: report['partial_diagnostics'].append({'name':name,'error':type(e).__name__})
        failures=cleanup(names,network if created else None)
        report['cleanup']={'status':'FAIL' if failures else 'PASS','remaining':failures}
        report['wall_seconds']=round(time.monotonic()-start,3)
        (out/'runtime-results.json').write_text(json.dumps(report,indent=2)+'\n')
    # Job intentionally fails admission when required daemon tests are blocked.
    return 1

if __name__=='__main__': sys.exit(run(pathlib.Path(sys.argv[1])))
