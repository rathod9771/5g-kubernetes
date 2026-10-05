"""Local regression contracts for approved images, safe import and simulator UE."""
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'ran-selector')]
import runtime_images as images
import srsran_image as importer
import osm_packages as packages
from scenario_registry import load_registry, select_scenarios
import prepare_simulated_ue as ue


class ImagePolicyTests(unittest.TestCase):
    def test_known_working_identity_is_not_current_remote_master(self):
        image = images.policy()['components']['srsran']
        self.assertEqual(image['source']['commit'], '11c9bbabb69873752500d676f55e0034f6caa5c5')
        self.assertEqual(image['config_digest'], 'sha256:8a156365eb292ce9d5b03ba69a2901fa997490196b6a89becaf31c55194e514d')
        self.assertEqual(image['digest'], 'sha256:e34a0ef3aa0649b0ba0e4b4d136867084371b780e55ec859c601a2987041fc59')
        self.assertEqual(image['pull_policy'], 'Never')

    def test_oai_pair_preserved_and_digest_deferral_explicit(self):
        components = images.policy()['components']
        self.assertEqual(images.reference(components['oai-cu']), 'oaisoftwarealliance/oai-gnb:2026.w13')
        self.assertEqual(images.reference(components['oai-du']), 'oaisoftwarealliance/oai-gnb:2026.w25')
        for name in ['oai-cu', 'oai-du']:
            self.assertEqual(components[name]['digest_status'], 'deferred-reference-cache-verification')
        self.assertIn('@sha256:', images.reference(components['fran-edge']))

    def test_floating_tags_and_missing_digest_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d);(root/'config').mkdir()
            lock = json.loads((ROOT/images.LOCK_PATH).read_text())
            for tag in ['master', 'latest', '']:
                changed = copy.deepcopy(lock)
                changed['ran_images']['components']['srsran']['tag'] = tag
                (root/images.LOCK_PATH).write_text(json.dumps(changed))
                with self.assertRaises(ValueError):images.policy(root)
            changed = copy.deepcopy(lock);changed['ran_images']['components']['srsran']['digest'] = None
            (root/images.LOCK_PATH).write_text(json.dumps(changed))
            with self.assertRaises(ValueError):images.policy(root)

    def test_all_three_srsran_profiles_use_the_same_bindings(self):
        registry = load_registry()
        refs = []
        for s in select_scenarios(registry, ['cran-srsran', 'vcran-srsran', 'cloudran-srsran']):
            for chart in s['charts']:
                self.assertTrue(chart['source'].startswith('helm/cran-srsran/'))
                refs.append(images.chart_values(ROOT, chart)['image']['reference'])
        self.assertEqual(len(refs), 6)
        self.assertEqual(len(set(refs)), 1)

    def test_blocked_scenarios_cannot_be_selected(self):
        for key in ['oran-srsran', 'hcran-srsran', 'hcran-oai']:
            with self.assertRaisesRegex(ValueError, 'BLOCKED'):
                select_scenarios(load_registry(), [key])

    def test_image_lock_is_captured_in_source_snapshot(self):
        scenario = select_scenarios(load_registry(), ['fran'])[0]
        with tempfile.TemporaryDirectory() as d:
            captured = packages.source_snapshot(ROOT, [scenario], Path(d))
            self.assertEqual(captured[images.LOCK_PATH], (ROOT/images.LOCK_PATH).read_bytes())

    def test_render_policy_rejects_unknown_runtime_and_wrong_pull_policy(self):
        chart = {'image_bindings': {'image': 'srsran'}}
        doc = {'kind':'Deployment','spec':{'template':{'spec':{'containers':[{'image':'example/ran:master'}]}}}}
        with self.assertRaisesRegex(ValueError, 'Unapproved'):images.verify_rendered(ROOT, chart, [doc])
        container = doc['spec']['template']['spec']['containers'][0]
        container['image'] = images.reference(images.policy()['components']['srsran'])
        with self.assertRaisesRegex(ValueError, 'pull policy'):images.verify_rendered(ROOT, chart, [doc])
        container['imagePullPolicy'] = 'Never'
        images.verify_rendered(ROOT, chart, [doc])

    def test_central_image_profile_wins_over_scenario_override(self):
        chart=select_scenarios(load_registry(),['cran-srsran'])[0]['charts'][0]
        files={'Chart.yaml':b'apiVersion: v2\nname: synthetic\nversion: 0.1.0\n','values.yaml':b'image: {}\n'}
        result=packages.helm_effective_values(files,[b'image: {reference: "unapproved/image:master", pullPolicy: Always}\n',
                                                     packages.json_bytes(images.chart_values(ROOT,chart))])
        self.assertEqual(result['image']['reference'],'localhost/5g-kubernetes/srsran:25.04.0-11c9bbabb6')
        self.assertEqual(result['image']['pullPolicy'],'Never')

    def test_node_import_gate_is_fail_closed_and_multi_node(self):
        image = images.policy()['components']['srsran']
        node = {'status':{'nodeInfo':{'architecture':'amd64'}, 'images':[{'names':[images.reference(image)]}]}}
        images.verify_imported_nodes({'items':[node]}, image)
        for nodes in [None, {'items':[]}, {'items':[node, {'status':{'nodeInfo':{'architecture':'amd64'}}}]},
                      {'items':[{'status':{'nodeInfo':{'architecture':'arm64'}}}]}]:
            with self.assertRaises((ValueError, AttributeError)):images.verify_imported_nodes(nodes, image)


class ImportTests(unittest.TestCase):
    def fixture(self, directory):
        layer = b'non-secret synthetic layer'
        digest = lambda data:'sha256:'+hashlib.sha256(data).hexdigest()
        config = importer.canonical({'architecture':'amd64','os':'linux','rootfs':{'type':'layers','diff_ids':[digest(layer)]}})
        manifest = importer.canonical(dict(schemaVersion=2, mediaType='application/vnd.oci.image.manifest.v1+json',
            config=dict(mediaType='application/vnd.oci.image.config.v1+json', digest=digest(config), size=len(config)),
            layers=[dict(mediaType='application/vnd.oci.image.layer.v1.tar', digest=digest(layer), size=len(layer))]))
        path = directory/'docker.tar'
        files = {'manifest.json':json.dumps([{'Config':'config.json','Layers':['layer.tar']}]).encode(),
                 'config.json':config, 'layer.tar':layer}
        with tarfile.open(path,'w') as archive:
            for name,data in files.items():
                info=tarfile.TarInfo(name);info.size=len(data);archive.addfile(info,io.BytesIO(data))
        image=dict(runtime_reference='local-tag',repository='localhost/5g-kubernetes/srsran',tag='test-fixed',digest=digest(manifest),
                   config_digest=digest(config),archive_sha256=digest(path.read_bytes()),archive_size=path.stat().st_size)
        return path,image,manifest,layer

    def test_verified_conversion_has_exact_oci_structure_and_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);path,image,manifest,layer=self.fixture(root)
            output=importer.convert(path,root/'oci.tar',image)
            with tarfile.open(output) as archive:
                index=json.load(archive.extractfile('index.json'))
                self.assertEqual(index['manifests'][0]['digest'],image['digest'])
                self.assertEqual(index['manifests'][0]['platform'],{'os':'linux','architecture':'amd64'})
                self.assertEqual(archive.extractfile('blobs/sha256/'+image['digest'][7:]).read(),manifest)
                self.assertEqual(len(archive.getmembers()),5)
                self.assertTrue(all(m.mtime==m.uid==m.gid==0 for m in archive))

    def test_conversion_is_deterministic_and_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);path,image,_,_=self.fixture(root)
            first=importer.convert(path,root/'a.tar',image);second=importer.convert(path,root/'b.tar',image)
            self.assertEqual(first.read_bytes(),second.read_bytes())
            with self.assertRaisesRegex(ValueError,'overwrite'):importer.convert(path,first,image)

    def test_wrong_export_and_config_and_manifest_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path,image,_,_=self.fixture(Path(d))
            for field in ['archive_sha256','config_digest','digest']:
                bad=dict(image);bad[field]='sha256:'+'0'*64
                with self.assertRaises(ValueError):importer.inspect_archive(path,bad)
            link=Path(d)/'link';link.symlink_to(path)
            with self.assertRaises(ValueError):importer.inspect_archive(link,image)

    def test_partial_conversion_never_publishes(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);path,image,_,_=self.fixture(root)
            with mock.patch.object(tarfile.TarFile,'addfile',side_effect=OSError('synthetic disk failure')):
                with self.assertRaises(OSError):importer.convert(path,root/'oci.tar',image)
            self.assertFalse((root/'oci.tar').exists())
            self.assertFalse(list(root.glob('.srsran-oci-*')))

    def test_changed_layer_between_verification_and_conversion_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);path,image,_,_=self.fixture(root)
            verified=importer.inspect_archive(path,image)
            with tarfile.open(path,'a') as archive:
                # Simulate the source changing after verification, including a
                # replacement layer with the same byte length.
                info=tarfile.TarInfo('layer.tar');data=b'X'*verified[2][0][1]['size'];info.size=len(data)
                archive.addfile(info,io.BytesIO(data))
            with mock.patch.object(importer,'inspect_archive',return_value=verified):
                with self.assertRaisesRegex(ValueError,'changed during conversion'):importer.convert(path,root/'oci.tar',image)
            self.assertFalse((root/'oci.tar').exists())

    def test_archive_traversal_rejected_without_extraction(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);path,image,_,_=self.fixture(root)
            with tarfile.open(path,'a') as archive:
                info=tarfile.TarInfo('../escape');info.size=1;archive.addfile(info,io.BytesIO(b'x'))
            image['archive_sha256']='sha256:'+hashlib.sha256(path.read_bytes()).hexdigest()
            image['archive_size']=path.stat().st_size
            with self.assertRaisesRegex(ValueError,'Unsafe'):importer.inspect_archive(path,image)
            self.assertFalse((root.parent/'escape').exists())

    def runtime_results(self, image, manifest, config):
        ref=images.reference(image)
        return [subprocess.CompletedProcess([],0,(ref+' OCI '+image['digest']+' 3GB linux/amd64 -\n').encode()),
                subprocess.CompletedProcess([],0,(ref+'\n').encode()),
                subprocess.CompletedProcess([],0,manifest), subprocess.CompletedProcess([],0,config),
                subprocess.CompletedProcess([],0,json.dumps({'status':{'id':image['config_digest'],'repoTags':[ref]}}).encode())]

    def test_existing_digest_reuses_without_conversion_or_import(self):
        with tempfile.TemporaryDirectory() as d:
            path,image,manifest,_=self.fixture(Path(d))
            with tarfile.open(path) as archive: config=archive.extractfile('config.json').read()
            run=mock.Mock(side_effect=self.runtime_results(image,manifest,config))
            with mock.patch.object(importer,'convert') as convert:
                self.assertFalse(importer.import_image(None,d,run,image))
                convert.assert_not_called();self.assertEqual(run.call_count,5)
                self.assertEqual(run.call_args[0][0][-2:],['inspecti',images.reference(image)])
                self.assertEqual(run.call_args.kwargs,{'check':False})

    def test_cri_missing_or_wrong_content_never_reports_success(self):
        with tempfile.TemporaryDirectory() as d:
            path,image,manifest,_=self.fixture(Path(d))
            with tarfile.open(path) as archive: config=archive.extractfile('config.json').read()
            for final in [subprocess.CompletedProcess([],1,b'',b'private stderr'),
                          subprocess.CompletedProcess([],0,json.dumps({'status':{'id':'sha256:'+'0'*64,'repoTags':[images.reference(image)]}}).encode())]:
                results=self.runtime_results(image,manifest,config);results[-1]=final
                with self.assertRaisesRegex(ValueError,'CRI'):
                    importer.installed(image,mock.Mock(side_effect=results))

    def test_containerd_blob_tampering_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path,image,manifest,_=self.fixture(Path(d))
            with tarfile.open(path) as archive: config=archive.extractfile('config.json').read()
            for index in [2,3]:
                results=self.runtime_results(image,manifest,config)
                results[index]=subprocess.CompletedProcess([],0,b'tampered')
                with self.assertRaisesRegex(ValueError,'integrity lock'):
                    importer.installed(image,mock.Mock(side_effect=results))

    def test_wrong_existing_tag_is_preserved_and_never_imported_over(self):
        image=images.policy()['components']['srsran']
        row=(images.reference(image)+' OCI sha256:'+'0'*64+' 3GB linux/amd64 -\n').encode()
        run=mock.Mock(return_value=subprocess.CompletedProcess([],0,row))
        with tempfile.TemporaryDirectory() as d,mock.patch.object(importer,'convert') as convert:
            with self.assertRaisesRegex(ValueError,'unapproved'):
                importer.import_image('/synthetic/input',d,run,image)
            convert.assert_not_called();self.assertEqual(run.call_count,1)

    def test_partial_containerd_image_is_not_treated_as_reusable(self):
        image=images.policy()['components']['srsran']
        run=mock.Mock(side_effect=[subprocess.CompletedProcess([],0,(images.reference(image)+' OCI '+image['digest']+' 3GB linux/amd64 -\n').encode()),
                                   subprocess.CompletedProcess([],0,b'')])
        self.assertFalse(importer.installed(image,run))
        self.assertEqual(run.call_args[0][0][-2:],['--quiet','name=='+images.reference(image)])

    def test_import_failure_cleans_transaction_and_does_not_hide_error(self):
        image=images.policy()['components']['srsran']
        run=mock.Mock(side_effect=[subprocess.CompletedProcess([],0,b''),ValueError('synthetic import failure')])
        with tempfile.TemporaryDirectory() as d,mock.patch.object(importer,'convert',return_value=Path(d)/'fake.tar'):
            with self.assertRaisesRegex(ValueError,'import failure'):importer.import_image('/synthetic/input',d,run,image)
            self.assertFalse(list(Path(d).glob('srsran-import-*')))
            args=run.call_args[0][0]
            self.assertNotIn('--digests',args);self.assertIn('k8s.io',args);self.assertIn('linux/amd64',args)

    def test_successful_import_checks_digest_unpacked_content_and_exact_cri_name(self):
        with tempfile.TemporaryDirectory() as d:
            path,image,manifest,_=self.fixture(Path(d))
            with tarfile.open(path) as archive: config=archive.extractfile('config.json').read()
            run=mock.Mock(side_effect=[subprocess.CompletedProcess([],0,b''),subprocess.CompletedProcess([],0,b'')]+
                          self.runtime_results(image,manifest,config))
            self.assertTrue(importer.import_image(path,d,run,image))
            self.assertFalse(list(Path(d).glob('srsran-import-*')))
            self.assertEqual(run.call_count,7)
            self.assertEqual(images.reference(image),image['repository']+':'+image['tag'])

    def test_install_shell_accepts_only_the_image_archive_option(self):
        text=(ROOT/'install.sh').read_text()
        self.assertIn('"$#" -eq 2 && "$1" == --srsran-image-archive',text)

    def test_missing_image_or_wrong_manifest_mapping_rejected(self):
        image=images.policy()['components']['srsran']
        run=mock.Mock(return_value=subprocess.CompletedProcess([],0,b''))
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError,'supply'):importer.import_image(None,d,run,image)
        run.return_value.stdout=(images.reference(image)+' OCI sha256:'+'0'*64+' 3GB linux/amd64 -\n').encode()
        with self.assertRaisesRegex(ValueError,'unapproved'):importer.installed(image,run)


class PendingReconciliationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec=importlib.util.spec_from_file_location('ready_image_backend',ROOT/'ran-selector/backend.py')
        cls.backend=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.backend)

    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.path=Path(self.temporary.name)/'active.yaml'
        self.state={'active':'none','osm':{'core_instance_id':'core',
                                         'pending_instance':{'id':'pending','operation':'failed-op','scenario':'cran-srsran'}}}
        self.path.write_text(packages.yaml.safe_dump(self.state))
        self.before=self.path.read_bytes()
        self.registry=load_registry()

    def invoke(self, state=None, requested='pending'):
        b=self.backend
        with b.app.test_request_context(),mock.patch.object(b,'CONFIG_FILE',str(self.path)):
            return b._reconcile_failed_pending(state or self.state,self.registry,requested)

    def mocks(self, absent=False, operation=None):
        import contextlib
        stack=contextlib.ExitStack();self.addCleanup(stack.close);b=self.backend
        self.absent=stack.enter_context(mock.patch.object(b.osm_client,'ns_instance_absent',side_effect=[absent,True]))
        self.instance=stack.enter_context(mock.patch.object(b.osm_client,'get_ns_instance',return_value={'name':'ran-cran-srsran','nsState':'BROKEN'}))
        self.operation=stack.enter_context(mock.patch.object(b.osm_client,'get_operation',return_value=operation or {'nsInstanceId':'pending','operationState':'FAILED'}))
        self.terminate=stack.enter_context(mock.patch.object(b.osm_client,'terminate_ns',return_value='terminate-op'))
        self.wait=stack.enter_context(mock.patch.object(b.osm_client,'wait_for_op',return_value='COMPLETED'))
        self.gone=stack.enter_context(mock.patch.object(b,'_wait_pods_gone',return_value=True))
        self.delete=stack.enter_context(mock.patch.object(b.osm_client,'delete_ns_instance',return_value=True))

    def test_failed_instance_cleanup_clears_only_pending_state(self):
        self.mocks();self.invoke()
        self.terminate.assert_called_once_with('pending');self.delete.assert_called_once_with('pending')
        saved=packages.yaml.safe_load(self.path.read_text())
        self.assertNotIn('pending_instance',saved['osm']);self.assertEqual(saved['osm']['core_instance_id'],'core')

    def test_absent_instance_resume_does_not_terminate_again(self):
        self.mocks(absent=True);self.invoke()
        self.terminate.assert_not_called();self.delete.assert_not_called()
        self.assertNotIn('pending_instance',packages.yaml.safe_load(self.path.read_text())['osm'])

    def test_wrong_requested_id_and_core_overlap_refused(self):
        self.mocks()
        with self.assertRaises(ValueError):self.invoke(requested='unowned')
        self.state['osm']['core_instance_id']='pending'
        with self.assertRaisesRegex(ValueError,'overlaps'):self.invoke()
        self.terminate.assert_not_called();self.assertEqual(self.path.read_bytes(),self.before)

    def test_running_or_successful_pending_operation_is_not_destroyed(self):
        self.mocks()
        for status in ['PROCESSING','COMPLETED']:
            self.absent.side_effect=[False,True]
            self.operation.return_value={'nsInstanceId':'pending','operationState':status}
            with self.assertRaisesRegex(ValueError,'failed terminal'):self.invoke()
        self.terminate.assert_not_called();self.assertEqual(self.path.read_bytes(),self.before)

    def test_wrong_operation_owner_or_name_rejected(self):
        self.mocks(operation={'nsInstanceId':'another','operationState':'FAILED'})
        with self.assertRaisesRegex(ValueError,'does not belong'):self.invoke()
        self.instance.return_value={'name':'core-persistent','nsState':'BROKEN'}
        self.absent.side_effect=[False,True]
        with self.assertRaisesRegex(ValueError,'NS name differs'):self.invoke()
        self.terminate.assert_not_called();self.assertEqual(self.path.read_bytes(),self.before)

    def test_failed_termination_or_remaining_resources_retains_state(self):
        self.mocks();self.wait.return_value='FAILED'
        with self.assertRaises(RuntimeError):self.invoke()
        self.delete.assert_not_called()
        self.assertEqual(packages.yaml.safe_load(self.path.read_text())['osm']['pending_instance']['termination_operation'],'terminate-op')

    def test_reconciliation_timeout_reuses_recorded_termination_operation(self):
        self.mocks();self.state['osm']['pending_instance']['termination_operation']='recorded-terminate-op'
        self.invoke()
        self.terminate.assert_not_called();self.wait.assert_called_once_with('recorded-terminate-op',timeout=180)

    def test_deleted_instance_with_remaining_resources_cannot_clear_state(self):
        self.mocks(absent=True);self.gone.return_value=False
        with self.assertRaisesRegex(RuntimeError,'Resources remain'):self.invoke()
        self.assertEqual(self.path.read_bytes(),self.before)

    def test_image_inventory_failure_and_missing_import_block_preflight(self):
        b=self.backend;s=select_scenarios(self.registry,['cran-srsran'])[0]
        for response in [None,{'items':[]}]:
            with b.app.test_request_context(),mock.patch.object(b,'_kubectl_json',return_value=response):
                with self.assertRaises(ValueError):b._verify_ready_image(s)

    def test_blocked_dashboard_keys_stop_before_package_or_cluster_work(self):
        b=self.backend
        with mock.patch.object(b,'CONFIG_FILE',str(self.path)),mock.patch.object(b,'validated_snapshot') as snapshot:
            for key in ['oran-srsran','hcran-srsran','hcran-oai']:
                self.assertEqual(b.app.test_client().post('/api/deploy',json={'ran':key}).status_code,409)
            snapshot.assert_not_called()


class SimulatedUETests(unittest.TestCase):
    def fixture(self, key='cran-oai'):
        cfg={'DEPLOYMENT_PROFILE':'rfsim','MCC':'208','MNC':'93','DNN':'internet','SST':'1','SD':'010203'}
        subscriber={'imsi':'208930000000001','key':'0'*32,'opc':'1'*32}
        return ue.render(subscriber,select_scenarios(load_registry(),[key])[0],'synthetic',cfg)

    def test_reference_simulator_settings_and_private_secret(self):
        docs=self.fixture();deployment=docs[-1];pod=deployment['spec']['template']['spec']
        self.assertEqual(deployment['spec']['replicas'],0)
        self.assertEqual(pod['volumes'][0]['secret']['defaultMode'],0o400)
        secret=next(d for d in docs if d['kind']=='Secret')
        self.assertIn('nssai_sd = 0x010203',secret['stringData']['ue.conf'])
        self.assertIn('dnn = "internet"',secret['stringData']['ue.conf'])
        init=next(d for d in docs if d['metadata']['name']=='oai-nr-ue-cran-init')['data']['ue-init-cran.sh']
        self.assertIn('--rfsim',init);self.assertIn('-C 3450720000 --ssb 516',init)
        self.assertNotIn('PLACEHOLDER_KEY',init);self.assertNotIn('--uicc0.key',init)
        self.assertNotIn('pip install',json.dumps(docs))
        for c in pod['containers']+pod['initContainers']:self.assertIn('@sha256:',c['image'])

    def test_profile_du_discovery_selector(self):
        for key,label in [('cran-oai','cran-oai-du'),('vcran-oai','vcran-oai-du'),('cloudran-oai','cloud-ran-oai-du')]:
            pod=self.fixture(key)[-1]['spec']['template']['spec']
            env=pod['initContainers'][0]['env']
            self.assertIn({'name':'GNB_LABEL_SELECTOR','value':'app='+label},env)

    def test_private_output_cannot_enter_source_paths(self):
        with self.assertRaises(ValueError):ue.private_destination(ROOT/'config/private.yaml')
        self.assertEqual(ue.private_destination(ROOT/'.runtime/ue.yaml'),ROOT/'.runtime/ue.yaml')

    def test_ue_input_rejects_shell_material_and_wrong_profile(self):
        scenario=select_scenarios(load_registry(),['cran-oai'])[0]
        cfg={'DEPLOYMENT_PROFILE':'rfsim','MCC':'208','MNC':'93','DNN':'internet','SST':'1','SD':'010203'}
        for bad in [{'imsi':'208930000000001','key':'$(id)','opc':'0'*32},{'password':'synthetic'}]:
            with self.assertRaises(ValueError):ue.render(bad,scenario,'synthetic',cfg)
        cfg['DEPLOYMENT_PROFILE']='usrp'
        with self.assertRaises(ValueError):ue.render({'imsi':'208930000000001','key':'0'*32,'opc':'1'*32},scenario,'synthetic',cfg)


if __name__ == '__main__':
    unittest.main()
