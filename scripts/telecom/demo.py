"""Reproducible offline HTTP demo; creates only a fresh private output directory."""
import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import uuid
from unittest.mock import patch
from flask import Flask
from .api import register
from .contract import identity
from .service import Service, report_html

ROOT=Path(__file__).resolve().parents[2]


def run(output, *, helm='helm', clock=None):
    output=Path(output)
    output.mkdir(mode=0o700,parents=False,exist_ok=False)
    runtime=output/'runtime';runtime.mkdir(mode=0o700)
    auth=[];tokens={}
    for actor,roles in [('researcher',['researcher','builder','reader']),('reviewer',['reviewer','reader']),
                        ('operator',['operator','publisher','verifier','reader'])]:
        token='offline-demo-only-'+actor+'-'+'x'*32
        tokens[actor]=token
        auth.append({'subject':actor,'tenant':'research-lab','roles':roles,'sha256':hashlib.sha256(token.encode()).hexdigest()})
    auth_path=output/'sandbox-identities.json';auth_path.write_text(json.dumps(auth));auth_path.chmod(0o600)
    app=Flask('telecom-offline-demo');register(app);client=app.test_client()
    env={'TELECOM_RUNTIME_DIR':str(runtime),'TELECOM_WORKSPACE':str(ROOT),'TELECOM_AUTH_FILE':str(auth_path),'HELM_BIN':helm}
    calls=[]
    def post(actor,path,data):
        response=client.post('/api/telecom/'+path,json=data,headers={'Authorization':'Bearer '+tokens[actor]})
        calls.append({'path':path,'actor':actor,'status':response.status_code})
        if response.status_code!=200:
            raise RuntimeError('Offline demo stage failed: '+path+' '+json.dumps(response.json))
        return response.json
    with patch.dict(os.environ,env), patch('urllib.request.OpenerDirector.open',side_effect=AssertionError('Network forbidden in offline demo')):
        releases={}
        for name in ('ran','core'):
            manifest=json.loads((ROOT/'examples/telecom'/f'{name}.json').read_text())
            w=post('researcher','workloads',{'manifest':manifest,'event_id':'register-'+name})
            r=post('researcher','releases',{'workload_id':w['id'],'chart':f'examples/telecom/{name}-chart','event_id':'release-'+name})
            approval=post('reviewer','approvals',{'release_id':r['id'],'action':'catalog','target':{'project':'research-lab','mode':'SIMULATED'},'event_id':'approve-catalog-'+name})
            post('operator','catalog-simulations',{'release_id':r['id'],'approval_id':approval['id'],'event_id':'catalog-'+name})
            target={'ns_id':str(uuid.uuid5(uuid.NAMESPACE_URL,'offline-'+name)),'cluster_uid':str(uuid.uuid5(uuid.NAMESPACE_URL,'offline-cluster')),
                    'namespace_uid':str(uuid.uuid5(uuid.NAMESPACE_URL,'offline-namespace')),'project_id':str(uuid.uuid5(uuid.NAMESPACE_URL,'offline-project')),
                    'tenant':'research-lab','mode':'SIMULATED'}
            approval=post('reviewer','approvals',{'release_id':r['id'],'action':'update','target':target,'event_id':'approve-update-'+name})
            releases[name]=post('operator','update-simulations',{'release_id':r['id'],'approval_id':approval['id'],'event_id':'update-'+name})
        import time
        at=time.time()
        proof={'outcome':'PASS','provenance':'SIMULATED','observed_at':at,'evidence_digest':'sha256:'+identity('offline fixture')}
        matrix=post('operator','interoperability',{'ran_release':releases['ran']['id'],'core_release':releases['core']['id'],
             'tests':{name:proof for name in ('F1','NGAP','registration','pdu_session','user_plane')},'metrics':{'up':proof},'event_id':'protocol-evidence'})
        analysis=post('operator','failure-analysis',{'release_id':releases['ran']['id'],'alarm_type':'DUUnavailable','event_id':'synthetic-incident'})
        assert analysis['incident_ids'], 'Synthetic Layer 3 incident identity is required'
        report=post('operator','qualification-reports',{'matrix_id':matrix['id'],'event_id':'report'})
        for name in ('workloads','capabilities','releases','interoperability','failure-analysis','qualification-reports'):
            response=client.get('/api/telecom/'+name,headers={'Authorization':'Bearer '+tokens['operator']})
            assert response.status_code==200
        journal=Service(runtime,ROOT,helm=helm).journal
        assert journal.verify_audit()
        summary={'schema_version':1,'mode':'SIMULATED_ONLY','production_calls':0,'http_stages':calls,
                 'release_ids':{k:v['id'] for k,v in releases.items()},'matrix_id':matrix['id'],
                 'incident_ids':analysis['incident_ids'],'audit_integrity':True,'production_qualified':False}
        (output/'qualification.json').write_text(json.dumps(report,indent=2)+'\n')
        (output/'qualification.html').write_text(report_html(report))
        (output/'demo-results.json').write_text(json.dumps(summary,indent=2)+'\n')
        # Export exactly the package bytes that passed the real repository validators.
        for name,r in releases.items():
            full=journal.get(__import__('telecom.security',fromlist=['Principal']).Principal('operator','research-lab',frozenset({'operator'})),'releases',r['id'])
            for filename,data in full['package']['artifacts'].items():
                (output/filename).write_bytes(bytes.fromhex(data))
        return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--helm',default='helm')
    args=parser.parse_args()
    print(json.dumps(run(args.output,helm=args.helm),indent=2))

if __name__=='__main__':main()
