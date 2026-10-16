"""Offline security and workflow checks; production transport is never allowed."""
import copy
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import uuid
from flask import Flask

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from telecom.contract import validate, identity, confined
from telecom.security import Principal, Denied, authenticate, install_legacy_guard
from telecom.package import prepare, source_evidence
from telecom.service import Service, report_html, SimulatedCatalog
from telecom.api import register
from telecom.demo import run
from assurance.locking import exclusive

HELM=os.environ.get('HELM_BIN') or ('/usr/local/bin/helm.real' if Path('/usr/local/bin/helm.real').exists() else 'helm')


def manifest(name='ran'):
    return json.loads((ROOT/'examples/telecom'/f'{name}.json').read_text())


class ContractTests(unittest.TestCase):
    def test_versioned_roles_and_provider_evidence(self):
        for name in ('ran','core'):self.assertEqual(validate(manifest(name))['schema_version'],1)
        m=manifest();m['roles']=['x-experiment'];validate(m)

    def test_all_source_types(self):
        # A disposable local repo, not the project's own history: this must pass
        # identically under a shallow CI checkout, which has no ancestor objects.
        with tempfile.TemporaryDirectory() as tmp:
            workspace=Path(tmp);repo=workspace/'repo';repo.mkdir()
            subprocess.run(['git','init','--template=',str(repo)],check=True,capture_output=True)
            (repo/'file.txt').write_text('fixture')
            subprocess.run(['git','-C',str(repo),'add','.'],check=True,capture_output=True)
            subprocess.run(['git','-C',str(repo),'-c','user.name=Offline','-c','user.email=offline@example.invalid',
                             'commit','-m','fixture'],check=True,capture_output=True)
            sha=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD']).decode().strip()
            sources=[{'kind':'github','url':'https://github.com/example/nf','commit':'a'*40},
                     {'kind':'local-git','path':'repo','commit':sha},
                     {'kind':'helm','url':'https://charts.example.org/nf.tgz','digest':'sha256:'+'a'*64}]
            for source in sources:
                m=manifest();m['source']=source;validate(m)
            evidence=source_evidence(sources[1],workspace)
            self.assertEqual(evidence['commit'],sources[1]['commit']);self.assertFalse(evidence['working_tree_included'])
            self.assertEqual(source_evidence(sources[0],workspace)['fetch'],'NOT_RUN')

    def test_invalid_capabilities(self):
        changes=[('schema_version',2),('roles',['unknown']),('plmn','001'),('tac',True),
                 ('slices',[{'sst':1,'sd':'zzzzzz'}]),('dnns',['bad space']),('version','bad/name'),
                 ('resources',{'cpu_millicores':0,'memory_mib':64}),('monitoring',{'required_metrics':['bad{label}'],'optional_metrics':[]})]
        for key,value in changes:
            with self.subTest(key=key):
                m=manifest();m[key]=value
                with self.assertRaises((ValueError,TypeError)):validate(m)
        m=manifest();m['interfaces']['F1-C']['transport']='TCP'
        with self.assertRaises(ValueError):validate(m)
        m=manifest();m['interfaces']['N2']['endpoint']='999.1.1.1'
        with self.assertRaises(ValueError):validate(m)
        m=manifest();del m['interfaces']['N3']
        with self.assertRaises(ValueError):validate(m)

    def test_no_credentials_or_floating_images(self):
        for change in ({'password':'private-value'}, {'platform_verified':True}):
            m=manifest();m.update(change)
            with self.assertRaises(ValueError):validate(m)
        m=manifest();m['image']['digest']='latest'
        with self.assertRaises(ValueError):validate(m)
        m=manifest();m['source']={'kind':'helm','url':'https://user:password@charts.example.org/nf','digest':'sha256:'+'a'*64}
        with self.assertRaises(ValueError):validate(m)

    def test_workspace_escape_and_symlink(self):
        with self.assertRaises(ValueError):confined(ROOT,'/etc')
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'link';p.symlink_to('/etc')
            with self.assertRaises(ValueError):confined(t,p)

    def test_hardware_is_separate(self):
        m=manifest();m['hardware']['devices']=['dev/uhd']
        with self.assertRaises(ValueError):validate(m)
        m['hardware']['mode']='hardware';validate(m)


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.packages={name:prepare(ROOT,f'examples/telecom/{name}-chart',manifest(name),HELM) for name in ('ran','core')}

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.runtime=Path(self.temp.name)/'runtime';self.now=[2000000000.]
        self.service=Service(self.runtime,ROOT,helm=HELM,clock=lambda:self.now[0])
        self.researcher=Principal('researcher','lab',frozenset({'researcher','builder'}))
        self.reviewer=Principal('reviewer','lab',frozenset({'reviewer'}))
        self.operator=Principal('operator','lab',frozenset({'operator','publisher','verifier'}))
        self.foreign=Principal('operator','foreign',self.operator.roles)
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch('telecom.service.prepare',side_effect=lambda root,path,m,helm:copy.deepcopy(self.packages['ran' if 'cu' in m['roles'] else 'core'])))
        self.stack.enter_context(mock.patch('urllib.request.OpenerDirector.open',side_effect=AssertionError('Network forbidden')))
        self.count=0

    def event(self):self.count+=1;return 'event-'+str(self.count)
    def release(self,name='ran'):
        w=self.service.register(self.researcher,manifest(name),self.event())
        return self.service.release(self.researcher,w['id'],f'examples/telecom/{name}-chart',self.event())
    def target(self):
        return {**{k:str(uuid.uuid5(uuid.NAMESPACE_URL,k)) for k in ('ns_id','cluster_uid','namespace_uid','project_id')},'tenant':'lab','mode':'SIMULATED'}
    def approve(self,r,action,target=None):
        target=target or ({'project':'lab','mode':'SIMULATED'} if action=='catalog' else self.target())
        return self.service.approve(self.reviewer,r['id'],action,target,self.event())
    def sync(self,r):
        a=self.approve(r,'catalog');return self.service.catalog(self.operator,r['id'],a['id'],self.event())
    def update(self,r,outcome='SUCCESS'):
        self.sync(r);a=self.approve(r,'update');return self.service.update(self.operator,r['id'],a['id'],self.event(),outcome)
    def proof(self,provenance='SIMULATED',age=0,outcome='PASS'):
        return {'outcome':outcome,'provenance':provenance,'observed_at':self.now[0]-age,'evidence_digest':'sha256:'+identity('fixture')}
    def evidence(self,ran,core,tests=None,metrics=None):
        return self.service.evidence(self.operator,ran['id'],core['id'],tests or {k:self.proof() for k in ('F1','NGAP','registration','pdu_session','user_plane')}, {'up':self.proof()} if metrics is None else metrics,self.event())

    def test_immutable_release_and_duplicate_event(self):
        w=self.service.register(self.researcher,manifest(),'register')
        r=self.service.release(self.researcher,w['id'],'chart','release')
        again=self.service.release(self.researcher,w['id'],'chart','release')
        self.assertEqual(r,again)
        self.assertEqual(len(self.service.journal.list(self.operator,'releases')),1)
        m=manifest('core')
        with self.assertRaises(ValueError):self.service.register(self.researcher,m,'register')
        self.assertTrue(self.service.journal.verify_audit())

    def test_cross_tenant_read_write_and_ownership(self):
        r=self.release()
        for operation in (lambda:self.service.journal.get(self.foreign,'releases',r['id']),
                          lambda:self.service.approve(Principal('reviewer','foreign',self.reviewer.roles),r['id'],'catalog',{'project':'foreign','mode':'SIMULATED'},self.event())):
            with self.assertRaises(Denied):operation()
        other=Principal('other','lab',frozenset({'builder'}))
        with self.assertRaises(Denied):self.service.release(other,r['workload_id'],'chart',self.event())
        self.assertEqual(self.service.journal.list(self.foreign,'releases'),[])

    def test_roles_independent_approval_and_no_implicit_update(self):
        r=self.release()
        with self.assertRaises(Denied):self.service.catalog(self.researcher,r['id'],'missing',self.event())
        with self.assertRaises(Denied):self.service.approve(Principal('researcher','lab',self.reviewer.roles),r['id'],'catalog',{'project':'lab','mode':'SIMULATED'},self.event())
        a=self.approve(r,'update')
        with self.assertRaises(ValueError):self.service.update(self.operator,r['id'],a['id'],self.event())

    def test_protected_target_and_live_scope_denied(self):
        from assurance.model import PROTECTED_NS
        r=self.release()
        for ns in PROTECTED_NS:
            target=self.target();target['ns_id']=ns
            with self.assertRaises(Denied):self.approve(r,'update',target)
        target=self.target();target['mode']='LIVE'
        with self.assertRaises(Denied):self.approve(r,'update',target)
        target=self.target();target['tenant']='foreign'
        with self.assertRaises(Denied):self.approve(r,'update',target)

    def test_expired_and_consumed_approval(self):
        r=self.release();a=self.approve(r,'catalog');self.now[0]+=301
        with self.assertRaises(Denied):self.service.catalog(self.operator,r['id'],a['id'],self.event())
        a=self.approve(r,'catalog');self.service.catalog(self.operator,r['id'],a['id'],'sync')
        self.assertEqual(self.service.catalog(self.operator,r['id'],a['id'],'sync')['state'],'SIMULATED_CATALOG_SYNCED')
        with self.assertRaises(Denied):self.service.catalog(self.operator,r['id'],a['id'],'second-sync')

    def test_lifecycle_lock_and_release_after_error(self):
        r=self.release();a=self.approve(r,'catalog')
        with exclusive(self.runtime/'.lifecycle.lock'):
            with self.assertRaises(RuntimeError):self.service.catalog(self.operator,r['id'],a['id'],self.event())
        self.assertEqual(self.service.catalog(self.operator,r['id'],a['id'],self.event())['state'],'SIMULATED_CATALOG_SYNCED')

    def test_unknown_outcome_no_retry_and_manual_reconciliation(self):
        r=self.release();failed=self.update(r,'UNKNOWN_AFTER_SUBMIT');a=self.approve(r,'update')
        with self.assertRaises(ValueError):self.service.update(self.operator,r['id'],a['id'],self.event())
        with self.assertRaises(ValueError):self.service.reconcile(self.operator,r['id'],'wrong','NO_EFFECT',self.event())
        reconciled=self.service.reconcile(self.operator,r['id'],failed['operation']['id'],'NO_EFFECT',self.event())
        self.assertEqual(reconciled['state'],'SIMULATED_CATALOG_SYNCED')
        fresh=self.approve(r,'update')
        self.assertEqual(self.service.update(self.operator,r['id'],fresh['id'],self.event())['state'],'SIMULATED_UPDATED')

    def test_failure_before_submit_audit_and_rollback(self):
        r=self.release();failed=self.update(r,'FAIL_BEFORE_SUBMIT')
        self.assertEqual(failed['operation']['production_calls'],0)
        self.assertEqual(failed['operation']['attempts'],1)
        self.service.reconcile(self.operator,r['id'],failed['operation']['id'],'NO_EFFECT',self.event())
        self.assertTrue(self.service.journal.verify_audit())

    def test_sqlite_busy_fails_closed_and_cleanup(self):
        with sqlite3.connect(self.service.journal.path) as db:
            db.execute('BEGIN IMMEDIATE')
            with self.assertRaises(sqlite3.OperationalError):self.service.register(self.researcher,manifest(),self.event())
        self.assertEqual(self.service.journal.list(self.operator,'workloads'),[])
        self.service.register(self.researcher,manifest(),self.event())
        self.assertTrue(self.service.journal.verify_audit())

    def test_missing_stale_unavailable_and_forged_live_evidence(self):
        ran=self.update(self.release());core=self.update(self.release('core'))
        self.assertEqual(self.evidence(ran,core,metrics={})['missing_metrics'],['up'])
        self.assertEqual(self.evidence(ran,core,metrics={'up':self.proof(age=121)})['provenance'],'STALE')
        tests={k:self.proof() for k in ('F1','NGAP','registration','pdu_session','user_plane')}
        tests['NGAP']=self.proof('LIVE')
        with self.assertRaises(ValueError):self.evidence(ran,core,tests)
        tests['NGAP']=self.proof('UNAVAILABLE',outcome='UNAVAILABLE')
        self.assertNotEqual(self.evidence(ran,core,tests)['outcome'],'SIMULATION_PASS')

    def test_protocol_failure_and_incompatible_pair(self):
        ran=self.update(self.release());core=self.update(self.release('core'))
        tests={k:self.proof() for k in ('F1','NGAP','registration','pdu_session','user_plane')};tests['F1']=self.proof(outcome='FAIL')
        self.assertEqual(self.evidence(ran,core,tests)['outcome'],'INCONCLUSIVE_OR_FAILED')
        cm=self.service.journal.get(self.operator,'workloads',core['workload_id']);cm['manifest']['plmn']='99999'
        with self.service.journal.transaction() as db:self.service.journal.put(db,self.operator,'workloads',core['workload_id'],cm)
        with self.assertRaises(ValueError):self.evidence(ran,core)

    def test_existing_layer3_incident_dedup_and_proposal_only_report(self):
        ran=self.update(self.release());core=self.update(self.release('core'))
        matrix=self.evidence(ran,core)
        a=self.service.correlate(self.operator,ran['id'],'DUUnavailable','incident')
        b=self.service.correlate(self.operator,ran['id'],'DUUnavailable','incident')
        self.assertEqual(a['incident_ids'],b['incident_ids']);self.assertEqual(len(a['incident_ids']),1)
        with self.assertRaises(ValueError):self.service.correlate(self.operator,ran['id'],'AMFUnavailable','incident')
        report=self.service.report(self.operator,matrix['id'],'report')
        self.assertFalse(report['production_qualified']);self.assertEqual(report['qualification_outcome'],'SIMULATED_WITH_ISSUES')
        self.assertEqual(a['rollback_proposal']['execution'],'DISABLED')
        self.assertNotIn('<script>',report_html({'attacker':'<script>alert(1)</script>'}))
        self.assertTrue(self.service.journal.verify_audit())


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'auth.json';self.token='sandbox-test-'+('a'*40)
        self.path.write_text(json.dumps([{'subject':'r','tenant':'lab','roles':['researcher','builder'], 'sha256':hashlib.sha256(self.token.encode()).hexdigest()}]));self.path.chmod(0o600)
        self.env=mock.patch.dict(os.environ,{'TELECOM_AUTH_FILE':str(self.path),'TELECOM_RUNTIME_DIR':str(Path(self.temp.name)/'runtime'),'TELECOM_WORKSPACE':str(ROOT),'HELM_BIN':HELM,'TELECOM_LEGACY_TENANT':'lab'})
        self.env.start();self.addCleanup(self.env.stop)
        self.app=Flask('test-telecom');register(self.app);install_legacy_guard(self.app)
        for route in ('deploy','adopt-pending','reconcile-pending'):
            self.app.add_url_rule('/api/'+route,route,lambda: (_ for _ in ()).throw(AssertionError('Legacy mutation ran')),methods=['POST'])
        self.client=self.app.test_client();self.headers={'Authorization':'Bearer '+self.token}

    def test_no_auth_and_cross_role_fail_closed(self):
        self.assertEqual(self.client.get('/api/telecom/workloads').status_code,403)
        self.assertEqual(self.client.post('/api/telecom/workloads',json={'event_id':'one','manifest':manifest()},headers=self.headers).status_code,200)
        response=self.client.post('/api/telecom/approvals',json={'release_id':'missing','action':'catalog','target':{},'event_id':'two'},headers=self.headers)
        self.assertEqual(response.status_code,403)
        self.assertNotIn('artifacts',self.client.get('/api/telecom/releases',headers=self.headers).get_data(as_text=True))

    def test_legacy_routes_denied_before_business_logic(self):
        for path in ('deploy','adopt-pending','reconcile-pending'):
            self.assertEqual(self.client.post('/api/'+path,json={}).status_code,403)
        # Even a correctly authenticated operator cannot enable a legacy route.
        entries=json.loads(self.path.read_text());entries[0]['roles']=['operator'];self.path.write_text(json.dumps(entries))
        self.assertEqual(self.client.post('/api/deploy',json={},headers=self.headers).status_code,423)

    def test_private_auth_permissions_symlink_and_unknown_token(self):
        self.path.chmod(0o644)
        self.assertEqual(self.client.get('/api/telecom/workloads',headers=self.headers).status_code,403)
        self.path.chmod(0o600)
        with self.assertRaises(Denied):authenticate('Bearer '+'b'*40,str(self.path))
        link=self.path.parent/'symlink';link.symlink_to(self.path)
        with self.assertRaises(OSError):authenticate('Bearer '+self.token,str(link))

    def test_request_budget_strict_fields_and_no_sensitive_diagnostics(self):
        self.assertEqual(self.client.post('/api/telecom/workloads',data='x'*65537,content_type='application/json',headers=self.headers).status_code,413)
        response=self.client.post('/api/telecom/workloads',json={'manifest':manifest(),'event_id':'test','mode':'LIVE'},headers=self.headers)
        self.assertEqual(response.status_code,409)
        response=self.client.post('/api/telecom/workloads',json={'manifest':{'secret':'DO_NOT_ECHO'},'event_id':'test'},headers=self.headers)
        self.assertNotIn('DO_NOT_ECHO',response.get_data(as_text=True))
        self.assertEqual(response.headers['Cache-Control'],'no-store')


class PackageAndDemoTests(unittest.TestCase):
    def test_real_offline_http_workflow_and_package_integrity(self):
        with tempfile.TemporaryDirectory() as t:
            result=run(Path(t)/'demo',helm=HELM)
            self.assertEqual(result['production_calls'],0);self.assertFalse(result['production_qualified'])
            self.assertEqual(len(result['incident_ids']),1)
            self.assertTrue(all(c['status']==200 for c in result['http_stages']))
            self.assertEqual(len(list((Path(t)/'demo').glob('*.tar.gz'))),4)
            self.assertTrue((Path(t)/'demo/qualification.html').exists())

    def test_capability_revision_seals_distinct_catalog_names(self):
        first=prepare(ROOT,'examples/telecom/ran-chart',manifest(),HELM)
        revised=manifest();revised['version']='0.2.0'
        second=prepare(ROOT,'examples/telecom/ran-chart',revised,HELM)
        self.assertEqual(first['chart_digest'],second['chart_digest'])
        self.assertNotEqual(first['scenario']['knf_package'],second['scenario']['knf_package'])
        self.assertNotEqual(first['package_digests'],second['package_digests'])

    def test_package_rejection_paths(self):
        import shutil
        with tempfile.TemporaryDirectory() as t:
            chart=Path(t)/'chart';shutil.copytree(ROOT/'examples/telecom/ran-chart',chart)
            original=(chart/'templates/workload.yaml').read_text()
            cases=[original.replace('allowPrivilegeEscalation: false','allowPrivilegeEscalation: true'),
                   original.replace('automountServiceAccountToken: false','automountServiceAccountToken: true'),
                   original.replace('image: {{ .Values.image | quote }}','image: mutable:latest')]
            for content in cases:
                (chart/'templates/workload.yaml').write_text(content)
                with self.assertRaises(ValueError):prepare(t,chart,manifest(),HELM)
            (chart/'templates/workload.yaml').write_text(original)
            (chart/'extra.key').write_text('private sentinel')
            with self.assertRaises(ValueError):prepare(t,chart,manifest(),HELM)
            (chart/'extra.key').unlink();(chart/'escape').symlink_to('/etc/passwd')
            with self.assertRaises(ValueError):prepare(t,chart,manifest(),HELM)

    def test_retired_retry_script_cannot_call_osm(self):
        result=subprocess.run(['bash',str(ROOT/'scripts/instantiate-with-retry.sh')],capture_output=True,text=True,timeout=2)
        self.assertEqual(result.returncode,1);self.assertIn('disabled',result.stderr)
        self.assertNotIn('curl ',(ROOT/'scripts/instantiate-with-retry.sh').read_text())

    def test_dashboard_sections_and_no_polling_or_token_storage(self):
        source=(ROOT/'ran-selector/telecom.js').read_text()
        for name in ('External Workloads','Capability Registry','CI/CD Releases','Interoperability Matrix','Failure Analysis','Qualification Reports'):self.assertIn(name,source)
        self.assertNotIn('setInterval',source);self.assertNotIn('localStorage',source)
        self.assertIn('output.textContent',source)

class SnapshotCorrelationTests(unittest.TestCase):
    def test_existing_incident_linkage_and_provenance_isolation(self):
        from telecom.correlation import correlate_snapshot
        release={'id':'release','operation':{'target':{'ns_id':'ns','cluster_uid':'cluster'},'provenance':'SIMULATED'}}
        alarm={'alarm_id':'alarm','service':'ns','cluster':'cluster','provenance':'SIMULATED','correlation_id':'group'}
        snapshot={'alarm':[alarm,dict(alarm,alarm_id='foreign',service='foreign'),dict(alarm,alarm_id='live',provenance='LIVE')],
                  'incident':[{'incident_id':'preserved','source_alarms':['alarm']},{'incident_id':'other','source_alarms':['foreign']}]}
        before=copy.deepcopy(snapshot);value=correlate_snapshot(release,snapshot)
        self.assertEqual(value['incident_ids'],['preserved']);self.assertEqual(value['alarm_ids'],['alarm'])
        self.assertEqual(snapshot,before);self.assertEqual(value['rollback_proposal']['execution'],'DISABLED')

class BackendBoundaryTests(HTTPTests):
    def test_existing_dashboard_operator_reads_and_foreign_denial(self):
        import test_pending_adoption as fixture
        backend=fixture.backend
        entries=json.loads(self.path.read_text());entries[0]['roles']=['operator'];self.path.write_text(json.dumps(entries))
        client=backend.app.test_client()
        with (mock.patch.object(backend.osm_client,'terminate_ns',side_effect=AssertionError('Production mutation')),
              mock.patch.object(backend.osm_client,'instantiate_ns',side_effect=AssertionError('Production mutation'))):
            self.assertEqual(client.get('/api/scenarios').status_code,403)
            self.assertEqual(client.get('/api/scenarios',headers=self.headers).status_code,200)
            self.assertEqual(client.post('/api/deploy',json={'ran':'cran-oai'},headers=self.headers).status_code,423)
            self.assertEqual(client.post('/api/assurance/webhook',json={'alerts':[]},headers=self.headers).status_code,403)
            response=client.get('/telecom')
            self.assertEqual(response.status_code,200);response.close()
            entries[0]['tenant']='other';self.path.write_text(json.dumps(entries))
            self.assertEqual(client.get('/api/scenarios',headers=self.headers).status_code,403)

    def test_audit_corruption_blocks_new_events(self):
        from telecom.store import Journal
        journal=Journal(Path(self.temp.name)/'other-runtime')
        actor=Principal('a','lab',frozenset())
        journal.event(actor,'first',{'action':'fixture'},lambda db:{'ok':True})
        with sqlite3.connect(journal.path) as db:
            db.execute('DROP TRIGGER audit_no_update');db.execute("UPDATE audit SET hash='corrupt'")
        with self.assertRaises(ValueError):journal.event(actor,'second',{'action':'fixture'},lambda db:{'ok':True})

    def test_current_freshness_without_history_mutation(self):
        from telecom.freshness import current_evidence
        value={'tests':{'NGAP':{'observed_at':100}},'monitoring':{},'outcome':'SIMULATION_PASS'}
        result=current_evidence(value,300)
        self.assertEqual(result['current_freshness'],'STALE');self.assertNotIn('current_freshness',value)

class BoundedCommandTests(unittest.TestCase):
    def test_rss_watchdog_and_timeout_cleanup(self):
        from telecom.package import _bounded
        command=[sys.executable,'-c','import time; x=bytearray(20*1024*1024); time.sleep(5)']
        result=_bounded.run(command,max_rss=1024*1024,timeout=2)
        self.assertEqual(result.returncode,124);self.assertIn('RSS budget',result.stderr)
        result=_bounded.run([sys.executable,'-c','import time; time.sleep(5)'],timeout=.05)
        self.assertEqual(result.returncode,124);self.assertIn('timed out',result.stderr)

    def test_validation_environment_strips_credentials_and_bounds_go(self):
        from telecom.package import read_command
        with mock.patch.dict(os.environ,{'OSM_PASSWORD':'DO_NOT_INHERIT'}):
            result=read_command([sys.executable,'-c','import os,json; print(json.dumps(dict(os.environ)))'])
        env=json.loads(result)
        self.assertNotIn('OSM_PASSWORD',env);self.assertEqual(env['GOMAXPROCS'],'1')
        self.assertEqual(env['KUBECONFIG'],os.devnull)

class CancellationTests(unittest.TestCase):
    def test_parent_cancellation_kills_child_and_releases_capacity(self):
        from telecom.package import _bounded
        original=_bounded.subprocess.Popen;children=[]
        def start(*a,**kw):
            process=original(*a,**kw);children.append(process);return process
        selector=_bounded.selectors.DefaultSelector()
        with mock.patch.object(_bounded.subprocess,'Popen',side_effect=start), \
             mock.patch.object(_bounded.selectors,'DefaultSelector',return_value=selector), \
             mock.patch.object(selector,'select',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):_bounded.run([sys.executable,'-c','import time;time.sleep(20)'])
        self.assertIsNotNone(children[0].poll())
        self.assertEqual(_bounded.run([sys.executable,'-c','print(1)']).returncode,0)

    def test_accounting_failure_is_fail_closed(self):
        from telecom.package import _bounded
        from assurance.memory import MemoryMeasurementError
        with mock.patch('assurance.memory.tree_memory',side_effect=MemoryMeasurementError('unavailable')):
            result=_bounded.run([sys.executable,'-c','import time;time.sleep(20)'],max_rss=256*1024*1024)
        self.assertEqual(result.returncode,124);self.assertIn('accounting unavailable',result.stderr)
