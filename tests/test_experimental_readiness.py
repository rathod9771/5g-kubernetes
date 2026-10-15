"""Offline UE selection, bounded DNS and manifest-only metadata regressions."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'tests')]
import prepare_simulated_ue as ue
import image_metadata as metadata
from scenario_registry import load_registry,select_scenarios

class UETests(unittest.TestCase):
    def render(self,key,cell=None):
        cfg={'DEPLOYMENT_PROFILE':'rfsim','MCC':'208','MNC':'93','DNN':'internet','SST':'1','SD':'010203'}
        return ue.render({'imsi':'208930000000001','key':'0'*32,'opc':'1'*32},select_scenarios(load_registry(),[key])[0],'synthetic',cfg,cell=cell)
    def test_macro_small_and_fog_exact_peer(self):
        identities=set()
        for key,cell,du in [('hcran-oai','macro','hcran-oai-macro'),('hcran-oai','small','hcran-oai-small'),('fran-oai',None,'fran-oai-du')]:
            docs=self.render(key,cell);deployment=docs[-1]
            self.assertEqual(deployment['spec']['replicas'],0)
            env=deployment['spec']['template']['spec']['initContainers'][0]['env']
            self.assertIn({'name':'GNB_LABEL_SELECTOR','value':'app='+du},env)
            for d in docs:
                identity=(d['kind'],d['metadata']['name'])
                self.assertNotIn(identity,identities);identities.add(identity)
            secret=next(d for d in docs if d['kind']=='Secret')
            spec=deployment['spec']['template']['spec']
            self.assertEqual(spec['volumes'][0]['secret']['secretName'],secret['metadata']['name'])
            init=next(d for d in docs if d['kind']=='ConfigMap' and 'ue-init-cran.sh' in d['data'])['data']['ue-init-cran.sh']
            self.assertIn('-C 3450720000 --ssb 516',init)
            self.assertIn('${GNB_IP}',init)
            self.assertNotIn('208930000000001',init)
            self.assertTrue(any(d['kind']=='ServiceAccount' and d['metadata']['name']==spec['serviceAccountName'] for d in docs))
    def test_invalid_cell_and_oran_policy_unchanged(self):
        for key,cell in [('hcran-oai',None),('hcran-oai','du'),('fran-oai','macro'),('cran-oai','small'),('oran-oai',None)]:
            with self.subTest(key=key,cell=cell),self.assertRaises(ValueError):self.render(key,cell)
    def test_carrier_drift_fails_closed(self):
        scenario=select_scenarios(load_registry(),['fran-oai'])[0];chart=scenario['charts'][1]
        for content in ['absoluteFrequencySSB = 1;', 'serveraddr = "client";']:
            with mock.patch.object(ue,'render_chart',return_value=[{'kind':'ConfigMap','data':{'gnb.conf':content}}]):
                with self.assertRaisesRegex(ValueError,'carrier'):ue.validate_experimental_carrier(ROOT,chart)

class DNSTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import test_architecture_profiles as a
        a.ArchitectureTests.setUpClass()
        code=next(d for d in a.ArchitectureTests.docs[('fran-oai','fog')] if d['kind']=='ConfigMap')['data']['server.py']
        cls.ns={'__name__':'test'};exec(compile(code,'fog','exec'),cls.ns)
    def test_timeout_and_failure_safe(self):
        for effect in [subprocess.TimeoutExpired('synthetic',2),None]:
            with mock.patch.object(self.ns['subprocess'],'run',side_effect=effect,return_value=mock.Mock(returncode=1)) as run:
                with self.assertRaises(OSError):self.ns['bounded_dns']('private-host',38472)
                self.assertEqual(run.call_args.kwargs['timeout'],2)
                self.assertIs(run.call_args.kwargs['stderr'],subprocess.DEVNULL)
    def test_actual_stalled_resolver_process_is_killed(self):
        real_run=subprocess.run
        def stalled(args,**kw):return real_run([sys.executable,'-B','-c','import time;time.sleep(30)'],**kw)
        started=time.monotonic()
        with mock.patch.object(self.ns['subprocess'],'run',side_effect=stalled):
            with self.assertRaises(OSError):self.ns['bounded_dns']('synthetic',38472,timeout=.1)
        self.assertLess(time.monotonic()-started,3)
    def test_retry_then_success_and_budget_bound(self):
        resolver=mock.Mock(side_effect=[OSError(),None])
        with mock.patch.dict(self.ns['os'].environ,{'DU_SERVICE':'synthetic'}):
            self.ns['wait_for_du'](resolve=resolver,clock=lambda:0,pause=mock.Mock())
        self.assertEqual(resolver.call_count,2)
        self.assertEqual(resolver.call_args.kwargs['timeout'],2)

class MetadataTests(unittest.TestCase):
    def fixture(self):
        lock=json.loads((ROOT/'config/reference-versions.json').read_text())['ran_images']['components']
        digest='sha256:'+'a'*64;cfg='sha256:'+'b'*64
        rows='\n'.join((('docker.io/' if k.startswith('oai') else '')+lock[k]['repository']+':'+lock[k]['tag']+' type '+digest) for k in ['oai-cu','oai-du','srsran'])
        calls=[]
        def reader(args,deadline):
            calls.append(args)
            if args[-2:]==['images','list']:return rows.encode()
            if args[-1]==digest:return json.dumps({'config':{'digest':cfg},'layers':[{'digest':'sha256:'+'c'*64}]}).encode()
            if args[-1]==cfg:return json.dumps({'os':'linux','architecture':'amd64','config':{'Env':['PASSWORD=must-not-leak'],'Labels':{'private':'must-not-leak','org.opencontainers.image.revision':'1'*40}}}).encode()
            raise AssertionError('Layer traversal forbidden')
        return reader,calls
    def test_manifest_only_no_layers_or_secret_output(self):
        reader,calls=self.fixture();result=metadata.inspect(reader=reader)
        self.assertEqual(len(result['images']),3)
        self.assertTrue(all(i['complete'] for i in result['images']))
        self.assertNotIn('must-not-leak',json.dumps(result))
        self.assertEqual(len(calls),7)
        self.assertTrue(all(args[3:5] in (['images','list'],['content','get']) for args in calls))
    def test_partial_json_on_failure(self):
        reader,calls=self.fixture()
        def fail(args,deadline):
            if len(calls)>2:raise subprocess.TimeoutExpired(args,5)
            return reader(args,deadline)
        result=metadata.inspect(reader=fail)
        self.assertTrue(result['images'][0]['complete'])
        self.assertFalse(result['images'][1]['complete'])
        json.loads(json.dumps(result))
    def test_strict_command_timeout(self):
        started=time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            metadata.read_command([sys.executable,'-B','-c','import time;time.sleep(30)'],time.monotonic()+.1)
        self.assertLess(time.monotonic()-started,3)

class RFPeerTests(unittest.TestCase):
    def test_actual_du_endpoints_and_no_automatic_peer(self):
        from osm_packages import chart_files
        for key,roles in [('hcran-srsran',['macro','small']),('fran-srsran',['du'])]:
            scenario=select_scenarios(load_registry(),[key])[0]
            names=set()
            for role in roles:
                chart=next(c for c in scenario['charts'] if c['kdu']==role)
                files,_=chart_files(ROOT,chart)
                values=json.loads(files['values.yaml'])
                self.assertNotIn(values['fullname'],names);names.add(values['fullname'])
                text=files['templates/du.yaml'].decode()
                self.assertIn('tx_port=tcp://127.0.0.1:2000,rx_port=tcp://127.0.0.1:2001',text)
                self.assertIn('device_driver: zmq',text)
                self.assertNotIn('name: ue',text)
                self.assertNotIn('hostNetwork: true',text)
