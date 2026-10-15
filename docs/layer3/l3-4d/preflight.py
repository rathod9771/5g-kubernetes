"""Approval-only read-only trial preflight; never invoked during L3-4A preparation."""

def main():
    import os,sys,json,time,hashlib,shutil,sqlite3,urllib.request,subprocess,yaml,contextlib
    from pathlib import Path
    ROOT=Path('/home/rclab/5g-kubernetes');sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'ran-selector')]
    from runtime_config import load_config
    from runtime_render import check_installed_service
    from state_schema import validate_state
    from scenario_registry import load_registry
    from osm_catalog import Catalog,parse_response
    from ran_health import established
    snapshot=ROOT/'.runtime/dashboard-service.json';os.environ['P0_RUNTIME_CONFIG']=str(snapshot)
    cfg=load_config();check_installed_service('dashboard',cfg,Path('/etc/systemd/system/ran-selector.service'))
    state=validate_state(yaml.safe_load(Path(cfg['ACTIVE_STATE_PATH']).read_text()),load_registry())
    expected={'ran':'2c128db3-e27e-4dbe-8854-17751ba17680','core':'1b1cb300-b571-43f2-a567-6540895d7d0f'}
    assert state['osm']['active_instance_id']==expected['ran'] and state['osm']['core_instance_id']==expected['core']
    assert not state['osm'].get('pending_instance') and not state['osm'].get('experimental_recovery')
    assert subprocess.run(['systemctl','is-active','layer3-watcher.service'],capture_output=True,text=True).stdout.strip()=='inactive'
    from assurance.kubernetes import KubernetesReader
    resources_all=KubernetesReader({},'',timeout=10).run_json(['kubectl','--kubeconfig',cfg['OSM_KUBECONFIG_PATH'],'--request-timeout=5s','--chunk-size=0','get','pods,services','-A','-o','json'])
    Path('/tmp/l34d-activation-resources-before.json').write_text(json.dumps(resources_all))
    resources=resources_all['items'];ns=state['context']['namespace']
    selected=[p for p in resources if p['kind']=='Pod' and p['metadata']['namespace']==ns and (p['metadata'].get('labels',{}).get('osm.etsi.org/ns-id')==expected['ran'] or p['metadata']['name'].startswith('open5gs'))]
    core=[p for p in selected if p['metadata']['name'].startswith('open5gs')];assert len(core)==13
    for role in ('cu','du','fog'):
     pods=[p for p in selected if p['metadata'].get('labels',{}).get('component')==role];assert len(pods)==1
    for p in selected:
     assert p['status']['phase']=='Running' and any(c['type']=='Ready' and c['status']=='True' for c in p['status']['conditions'])
     assert all(c.get('ready') for c in p['status']['containerStatuses'])
    from assurance.osm import ReadOnlyOSM
    cat=ReadOnlyOSM(cfg,state);cat.paths.add('/nslcm/v1/ns_lcm_op_occs')
    out={'observed_at':time.time(),'namespace':ns,'state_hash':hashlib.sha256(Path(cfg['ACTIVE_STATE_PATH']).read_bytes()).hexdigest(),'protected_ns':{},'pods':{},'watcher':'inactive','service_configuration_verified':True,'protocols':{}}
    for role,ident in expected.items():
     data=cat._get('/nslcm/v1/ns_instances/'+ident);assert data.get('nsState')=='READY' and data.get('currentOperation')=='IDLE' and data.get('operational-status')=='running'
     out['protected_ns'][role]={k:data.get(k) for k in ('_id','nsState','currentOperation','currentOperationID','operational-status')}
    ops=cat._get('/nslcm/v1/ns_lcm_op_occs');own=[o for o in ops if o.get('nsInstanceId') in expected.values()]
    assert all(o.get('operationState') not in ('PROCESSING','STARTING','ROLLING_BACK') for o in own)
    out['operations']=[{k:o.get(k) for k in ('_id','nsInstanceId','operationState','lcmOperationType','startTime')} for o in own]
    for p in selected:
     m=p['metadata'];out['pods'][m['name']]={'uid':m['uid'],'spec_sha256':hashlib.sha256(json.dumps(p['spec'],sort_keys=True).encode()).hexdigest(),'ready':True,'containers':[{k:c.get(k) for k in ('name','restartCount','containerID','imageID')} for c in p['status']['containerStatuses']]}
    for role in ('cu','du'):
     pod=next(p for p in selected if p['metadata'].get('labels',{}).get('component')==role)
     result=subprocess.run(['kubectl','--kubeconfig',cfg['OSM_KUBECONFIG_PATH'],'--request-timeout=5s','exec','-n',ns,pod['metadata']['name'],'--','cat','/proc/net/sctp/assocs'],capture_output=True,text=True,timeout=8,check=True)
     f1=established(result.stdout,38472);ng=established(result.stdout,38412,True) if role=='cu' else None
     assert f1 and (ng if role=='cu' else True)
     out['protocols'][role]={'f1_sctp':f1,'ngap_sctp':ng,'e2_sctp':established(result.stdout,36421),'f1_setup':'NOT_REVALIDATED','ng_setup':'NOT_REVALIDATED'}
    svc=next(p for p in resources if p['kind']=='Service' and p['metadata']['namespace']=='monitoring' and p['metadata']['name']=='kube-prometheus-stack-alertmanager')
    out['alertmanager_url']='http://'+svc['spec']['clusterIP']+':9093';out['dashboard_url']=cfg['DASHBOARD_URL']
    # Snapshot the currently served dashboard, plus every local reviewed source and private config.
    (ROOT/'.runtime/l3-4d-preflight').mkdir(mode=0o700,exist_ok=True)
    backup=ROOT/'.runtime/l3-4d-preflight'/('backup-'+str(int(time.time())));backup.mkdir(mode=0o700)
    files=[ROOT/'ran-selector/backend.py',ROOT/'ran-selector/index.html',ROOT/'ran-selector/assurance.js',ROOT/'ran-selector/monitoring.py',ROOT/'ran-selector/osm_client.py',ROOT/'ran-selector/embed_policy.py',ROOT/'config/assurance-policies.json',ROOT/'scripts/service_entry.py',ROOT/'scripts/runtime_config.py',ROOT/'scripts/osm_tls.py',snapshot,Path(cfg['OSM_CA_CERT_PATH'])]
    files+=list((ROOT/'scripts/assurance').glob('*.py'))
    out['source_hashes']={}
    for p in files:
     dest=backup/p.relative_to(ROOT);dest.parent.mkdir(parents=True,exist_ok=True,mode=0o700);shutil.copy2(p,dest);dest.chmod(0o600);out['source_hashes'][str(p.relative_to(ROOT))]=hashlib.sha256(p.read_bytes()).hexdigest()
    shutil.copy2('/etc/systemd/system/ran-selector.service',backup/'ran-selector.service');(backup/'ran-selector.service').chmod(0o600)
    journal=Path(cfg['RUNTIME_DIR'])/'assurance.sqlite'
    if journal.exists():
     with contextlib.closing(sqlite3.connect(journal.resolve().as_uri()+'?mode=ro',uri=True,timeout=.2)) as src,contextlib.closing(sqlite3.connect(backup/'assurance.sqlite')) as dest:src.backup(dest)
     (backup/'assurance.sqlite').chmod(0o600)
     out['journal_backup']=True
    else:out['journal_backup']=False
    out['backup_dir']=str(backup)
    for path in ('/','/api/config','/api/scenarios','/api/status','/api/pods'):
     with urllib.request.urlopen(cfg['DASHBOARD_URL'].rstrip('/')+path,timeout=15) as r:content=r.read(2*1024*1024);status=r.status
     assert status==200
     if path=='/':(backup/'served-before.html').write_bytes(content)
     else:
      data=json.loads(content)
      if path=='/api/status':assert data.get('active')==state['active'] and not data.get('error')
     out.setdefault('baseline_apis',{})[path]={'http_status':status,'sha256':hashlib.sha256(content).hexdigest()}
    from assurance.catalog import load_catalog
    from assurance.policy import ProductionExecutor
    catalog=load_catalog();assert len(catalog)==44
    try:ProductionExecutor().execute()
    except PermissionError:out['executor']='DISABLED'
    else:raise AssertionError('Executor not disabled')
    (ROOT/'.runtime/l3-4d-preflight/preflight.json').write_text(json.dumps(out,indent=2));(ROOT/'.runtime/l3-4d-preflight/preflight.json').chmod(0o600)
    print('Preflight passed:',len(core),'core pods; CU/DU/fog Ready; NS READY/IDLE; F1/NGAP transport established. Backup:',backup)

if __name__=="__main__":main()
