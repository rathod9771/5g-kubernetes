from legacy_logic_client import legacy_logic_client
"""Static topology and fog processing checks; no cluster or OSM requests."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'scripts'), str(ROOT/'ran-selector')]
import osm_packages as packages
from runtime_images import chart_values
from scenario_registry import load_registry, select_scenarios, dashboard_scenarios

KEYS = ['hcran-oai','hcran-srsran','fran-oai','fran-srsran']


class ArchitectureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_registry()
        cls.selected = select_scenarios(cls.registry, KEYS)
        cls.docs = {}
        with tempfile.TemporaryDirectory() as t:
            for scenario in cls.selected:
                for chart in scenario['charts']:
                    f = Path(t)/'image.json'
                    f.write_text(json.dumps(chart_values(ROOT, chart)))
                    cls.docs[(scenario['key'],chart['kdu'])] = packages.render(
                        ROOT/chart['source'],chart['release_name'],
                        [ROOT/v for v in chart['values_files']]+[f])

    def values(self, chart):
        import yaml
        return yaml.safe_load((ROOT/chart['values_files'][0]).read_text())

    def test_four_explicit_experimental_selections(self):
        for s in self.selected:
            self.assertTrue(s['experimental'])
            self.assertEqual(s['acceptance_status'],'STATIC VALIDATED')
            self.assertFalse(s['additive'])
            self.assertIn(s['key'],dashboard_scenarios(self.registry))
        self.assertTrue(select_scenarios(self.registry,['fran'])[0]['additive'])

    def test_hcran_central_cu_two_distinct_du_cells(self):
        for s in self.selected:
            if s['ran_type']!='hcran':continue
            v={c['kdu']:self.values(c) for c in s['charts']}
            self.assertEqual(set(v),{'cu','macro','small'})
            for role in ['macro','small']:
                self.assertEqual(v[role]['cuFullname'],v['cu']['fullname'])
                self.assertEqual(v[role]['radio']['gnbId'],v['cu']['radio']['gnbId'])
            for field in ['duId','pci','cellId' if s['implementation']=='OAI' else 'sectorId']:
                self.assertNotEqual(v['macro']['radio'][field],v['small']['radio'][field])
            self.assertNotEqual(v['macro']['radio'],v['small']['radio'])
            power='ssbPower' if s['implementation']=='OAI' else 'txGain'
            self.assertGreater(v['macro']['radio'][power],v['small']['radio'][power])

    def test_no_cross_scenario_object_collisions(self):
        identities=set()
        for docs in self.docs.values():
            for d in docs:
                identity=(d['kind'],d['metadata']['name'])
                self.assertNotIn(identity,identities)
                identities.add(identity)

    def test_stateful_ran_fixed_replicas_and_memory_policy(self):
        for s in self.selected:
            for c in s['charts']:
                docs=self.docs[(s['key'],c['kdu'])]
                self.assertFalse(any(d['kind']=='HorizontalPodAutoscaler' for d in docs))
                d=next(d for d in docs if d['kind']=='Deployment')
                self.assertEqual(d['spec']['replicas'],1)
                resources=d['spec']['template']['spec']['containers'][0]['resources']
                self.assertIn('memory',resources['requests'])
                if s['implementation']=='srsRAN' and c['role']=='ran':self.assertNotIn('memory',resources['limits'])

    def test_fog_placement_is_real_and_optional(self):
        for s in self.selected:
            if s['ran_type']!='fran':continue
            for c in s['charts']:
                with tempfile.TemporaryDirectory() as t:
                    f=Path(t)/'override.json';v=chart_values(ROOT,c)
                    v['nodeSelector']={'kubernetes.io/hostname':'synthetic-edge' if c['kdu']!='cu' else 'synthetic-central'}
                    f.write_text(json.dumps(v))
                    docs=packages.render(ROOT/c['source'],c['release_name'],[ROOT/v for v in c['values_files']]+[f])
                    d=next(d for d in docs if d['kind']=='Deployment')
                    self.assertEqual(d['spec']['template']['spec']['nodeSelector'],v['nodeSelector'])

    def test_fog_monitor_has_real_processing_and_telemetry(self):
        docs=self.docs[('fran-oai','fog')]
        code=next(d for d in docs if d['kind']=='ConfigMap')['data']['server.py']
        namespace={'__name__':'test'}
        exec(compile(code,'fog-server.py','exec'),namespace)
        import io
        handler=object.__new__(namespace['Handler']);handler.path='/process'
        body=b'{"values":[2,4,6]}'
        handler.headers={'Content-Length':str(len(body))};handler.rfile=io.BytesIO(body)
        replies=[];handler.respond=lambda *args:replies.append(args)
        handler.do_POST()
        self.assertEqual(replies[-1][0],200)
        self.assertEqual(json.loads(replies[-1][1]),{'count':3,'mean':4.0,'minimum':2,'maximum':6})
        handler.path='/metrics'
        namespace['dns_cache'].seed(('192.0.2.1',))
        with mock.patch.dict(namespace, {'bounded_dns':mock.Mock(side_effect=AssertionError('metrics must use cache'))}):handler.do_GET()
        self.assertIn('fog_processed_values_total 3',replies[-1][1])
        self.assertIn('fog_du_dns_resolved 1',replies[-1][1])
        self.assertFalse(namespace.get('PDU_SESSION',False))
        monitor=next(d for d in docs if d['kind']=='PodMonitor')
        self.assertEqual(monitor['spec']['podMetricsEndpoints'][0]['path'],'/metrics')

    def test_experimental_gate_before_lifecycle(self):
        import test_pending_adoption as adoption
        b=adoption.backend
        with b.app.test_request_context('/api/deploy',method='POST',json={'ran':'hcran-oai'}), mock.patch.object(b,'validated_snapshot') as snapshot:
            response=b._deploy_locked('hcran-oai')
            self.assertEqual(response[1],409)
            snapshot.assert_not_called()

    def test_no_automatic_installer_or_watcher_activation(self):
        source=(ROOT/'scripts/installer/install.py').read_text()
        self.assertIn("and not s.get('experimental')",source)
        import runtime_config as rc
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);(root/'config').mkdir();(root/'config/scenarios.json').write_text(json.dumps(self.registry))
            # Registry source paths are validated, so exercise the real runtime root.
        with self.assertRaisesRegex(rc.ConfigError,'explicitly configured ready'):
            rc.load_config(ROOT,environ={'LAYER3_FAILOVER_SCENARIO':'hcran-oai'},require=('watcher',))

    def test_duplicate_identity_and_f1_mismatch_rejected(self):
        scenario=next(s for s in self.selected if s['key']=='hcran-oai')
        files={}
        for c in scenario['charts']:
            values=self.values(c)
            files[f"{scenario['knf_package']}/helm-chart-v3s/{c['kdu']}/values.yaml"]=json.dumps(values).encode()
        packages.validate_architecture_inputs(scenario,files)
        small=f"{scenario['knf_package']}/helm-chart-v3s/small/values.yaml"
        values=json.loads(files[small]);values['radio']['pci']=101;files[small]=json.dumps(values).encode()
        with self.assertRaisesRegex(packages.PackageError,'Duplicate'):
            packages.validate_architecture_inputs(scenario,files)
        values['radio']['pci']=102;values['cuFullname']='wrong-cu';files[small]=json.dumps(values).encode()
        with self.assertRaisesRegex(packages.PackageError,'identity mismatch'):
            packages.validate_architecture_inputs(scenario,files)

    def test_invalid_radio_schema_rejected(self):
        s=next(s for s in self.selected if s['key']=='hcran-srsran');c=s['charts'][1]
        with tempfile.TemporaryDirectory() as t:
            f=Path(t)/'invalid.json';override=chart_values(ROOT,c);override['radio']={'pci':1008};f.write_text(json.dumps(override))
            with self.assertRaises(packages.PackageError):
                packages.render(ROOT/c['source'],c['release_name'],[ROOT/c['values_files'][0],f])

    def test_dashboard_names_and_legacy_aliases_preserved(self):
        self.assertEqual(self.registry['aliases']['cran'],'cran-srsran')
        import test_pending_adoption as adoption
        b=adoption.backend
        catalog=legacy_logic_client(b.app).get('/api/scenarios').json['scenarios']
        for key in KEYS:
            item=next(x for x in catalog if x['key']==key)
            self.assertTrue(item['experimental'])
            s=next(x for x in self.selected if x['key']==key)
            self.assertEqual(item['releases'],[c['release_name'] for c in s['charts']])

    def test_knf_nsd_three_kdu_references(self):
        for s in self.selected:
            vnfd,nsd=packages.descriptors(s)
            files={f"{s['knf_package']}/{s['knf_package']}_vnfd.yaml":json.dumps(vnfd).encode(),
                   f"{s['nsd_package']}/{s['nsd_package']}_nsd.yaml":json.dumps(nsd).encode()}
            for c in s['charts']:
                files[f"{s['knf_package']}/helm-chart-v3s/{c['kdu']}/Chart.yaml"]=(ROOT/c['source']/'Chart.yaml').read_bytes()
            packages.validate_references(s,files)
            roles=['cu','macro','small'] if s['ran_type']=='hcran' else ['cu','du','fog']
            self.assertEqual(vnfd['vnfd']['kdu'],[{'name':role,'helm-chart':role} for role in roles])
            self.assertEqual(vnfd['vnfd']['id'],s['knf_package'])
            self.assertEqual(vnfd['vnfd']['product-name'],s['knf_package'])
            self.assertEqual(nsd['nsd']['nsd'][0]['vnfd-id'],[s['knf_package']])
            self.assertEqual(nsd['nsd']['nsd'][0]['df'][0]['vnf-profile'][0]['id'],s['member_id'])

    def test_monitoring_uses_all_catalog_pod_names(self):
        import test_pending_adoption as adoption
        b=adoption.backend
        for scenario in self.selected:
            pods=[{'metadata':{'name':name+'-hash','labels':{'ran-type':scenario['ran_type']}},'status':{'phase':'Running'}} for name in scenario['pods']]
            pods.append({'metadata':{'name':'unrelated-hash','labels':{'ran-type':scenario['ran_type']}},'status':{'phase':'Running'}})
            with b.app.test_request_context(),mock.patch.object(b,'_namespace',return_value='synthetic'),mock.patch.object(b,'_kubectl_json',return_value={'items':pods}):
                selected=b._scenario_pods(scenario['key'])
            self.assertEqual([p['metadata']['name'] for p in selected],[name+'-hash' for name in scenario['pods']])

    def test_experimental_events_do_not_claim_partial_topology_operational(self):
        import test_pending_adoption as adoption
        b=adoption.backend
        for scenario in self.selected:
            name=scenario['pods'][0]
            pod={'metadata':{'name':name+'-hash','labels':{'app':name}},
                 'status':{'phase':'Running','containerStatuses':[{'name':'cu','ready':True}]}}
            with mock.patch.object(b,'_runtime_targets',return_value=[pod]), \
                 mock.patch.object(b,'_log_text_for_pod',return_value='Connected to AMF\nF1SetupResponse'), \
                 mock.patch.object(b,'_ue_status_payload',return_value={}):
                result=b._client_events(scenario['key'])
            self.assertEqual(result['overall'],'PARTIAL')
            self.assertIn('Missing experimental components',result['overall_detail'])

    def test_fog_request_read_has_deadline(self):
        code=next(d for d in self.docs[('fran-oai','fog')] if d['kind']=='ConfigMap')['data']['server.py']
        ns={'__name__':'test'};exec(compile(code,'fog','exec'),ns)
        handler=object.__new__(ns['Handler']);handler.request=mock.Mock()
        from http.server import BaseHTTPRequestHandler
        with mock.patch.object(BaseHTTPRequestHandler,'setup'):
            handler.setup()
        handler.request.settimeout.assert_called_once_with(5)
        handler.path='/process';handler.headers={'Content-Length':'1'}
        handler.rfile=mock.Mock();handler.rfile.read.side_effect=TimeoutError()
        handler.respond=mock.Mock();handler.do_POST()
        self.assertEqual(handler.respond.call_args.args[0],400)

    def test_experimental_transport_has_bounded_discovery_and_pod_f1u(self):
        import yaml
        for scenario in self.selected:
            for chart in scenario['charts']:
                if chart['role']!='ran':continue
                docs=self.docs[(scenario['key'],chart['kdu'])]
                data=next(d for d in docs if d['kind']=='ConfigMap')['data']
                init=data['cu-init.sh' if chart['kdu']=='cu' else 'du-init.sh']
                self.assertIn('SECONDS + 120',init)
                self.assertIn('timeout 5 getent ahostsv4',init)
                self.assertIn('exit 1',init)
                if scenario['implementation']=='srsRAN':
                    config=yaml.safe_load(data['cu.yml' if chart['kdu']=='cu' else 'du.yml'])
                    f1u=config['cu_up']['f1u'] if chart['kdu']=='cu' else config['f1u']
                    self.assertEqual(f1u['socket'],[{'bind_addr':'POD_IP'}])
                    self.assertEqual(f1u['bind_port'],2152)
                    self.assertNotIn('127.',json.dumps(f1u))
                    if chart['kdu']=='cu' and scenario['ran_type']=='hcran':
                        self.assertEqual(config['cu_cp']['max_nof_dus'],2)
                else:
                    services=[d for d in docs if d['kind']=='Service']
                    self.assertTrue(any(p['port']==38472 and p['protocol']=='UDP'
                                        for d in services for p in d['spec']['ports']))
                    config=data['gnb.conf']
                    self.assertIn('local_s_address = "POD_IP"' if chart['kdu']=='cu'
                                  else 'local_n_address = "POD_IP"',config)

    def test_experimental_deploy_refuses_active_instance_before_termination(self):
        import test_pending_adoption as adoption
        b=adoption.backend
        scenario=self.selected[0]
        state={'active':'cran-srsran','osm':{'active_instance_id':'existing'},'context':{}}
        with tempfile.TemporaryDirectory() as t:
            path=Path(t)/'state.yaml';path.write_text(json.dumps(state))
            with b.app.test_request_context(),mock.patch.object(b,'_state_path',return_value=path), \
                 mock.patch.object(b,'validate_state'),mock.patch.object(b.osm_client,'terminate_ns') as terminate:
                with self.assertRaisesRegex(ValueError,'refusing teardown'):
                    b._deploy_verified(scenario['key'],self.registry,{}, {},scenario,{}, {})
                terminate.assert_not_called()
            self.assertEqual(json.loads(path.read_text()),state)

    def test_fog_dependency_retry_and_exhaustion(self):
        code=next(d for d in self.docs[('fran-oai','fog')] if d['kind']=='ConfigMap')['data']['server.py']
        ns={'__name__':'test'};exec(compile(code,'fog','exec'),ns)
        with mock.patch.dict(ns['os'].environ,{'DU_SERVICE':'synthetic-du'}):
            resolve=mock.Mock(side_effect=[OSError(),[]]);pause=mock.Mock()
            ns['wait_for_du'](resolve=resolve,clock=lambda:0,pause=pause)
            self.assertEqual(resolve.call_count,2);pause.assert_called_once_with(1)
            with self.assertRaisesRegex(RuntimeError,'discovery timed out'):
                ns['wait_for_du'](resolve=mock.Mock(side_effect=OSError()),clock=mock.Mock(side_effect=[0,121]),pause=pause)
