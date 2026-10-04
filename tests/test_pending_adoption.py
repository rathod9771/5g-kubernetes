"""Pending-success recovery without OSM lifecycle actions or state ambiguity."""
import contextlib
import copy
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import yaml

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'ran-selector')]
spec=importlib.util.spec_from_file_location('adoption_backend',ROOT/'ran-selector/backend.py')
backend=importlib.util.module_from_spec(spec);spec.loader.exec_module(backend)


class PendingAdoptionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'active.yaml'
        self.context={k:'00000000-0000-4000-8000-000000000001' for k in ['project_id','vim_id','cluster_uid','namespace_uid']}
        self.context.update(namespace='test',repo_root='/test',workload_kubeconfig='/test/kubeconfig',deployment_profile='rfsim',osm_host='http://test')
        self.state={'active':'none','context':self.context,'preserved':'extra',
                    'osm':{'core_instance_id':'core','pending_instance':{'id':'pending','operation':'op','scenario':'vcran-srsran'}}}
        self.write(self.state)
        self.instance={'_id':'pending','name':'ran-vcran-srsran','nsState':'READY','operational-status':'running'}
        self.operation={'_id':'op','nsInstanceId':'pending','lcmOperationType':'instantiate','operationState':'COMPLETED'}
        stack=contextlib.ExitStack();self.addCleanup(stack.close)
        stack.enter_context(mock.patch.object(backend,'CONFIG_FILE',str(self.path)))
        stack.enter_context(mock.patch.object(backend,'runtime_preflight',return_value=({'ACTIVE_STATE_PATH':str(self.path)},self.context,None)))
        stack.enter_context(mock.patch.object(backend.osm_client,'runtime_session',side_effect=lambda cfg:contextlib.nullcontext()))
        self.get_instance=stack.enter_context(mock.patch.object(backend.osm_client,'get_ns_instance',return_value=self.instance))
        self.get_operation=stack.enter_context(mock.patch.object(backend.osm_client,'get_operation',return_value=self.operation))
        self.terminate=stack.enter_context(mock.patch.object(backend.osm_client,'terminate_ns',return_value='cleanup-op'))
        self.delete=stack.enter_context(mock.patch.object(backend.osm_client,'delete_ns_instance',return_value=True))
        self.instantiate=stack.enter_context(mock.patch.object(backend.osm_client,'instantiate_ns'))
        self.client=backend.app.test_client()
        self.cleanup_allowed=False

    def tearDown(self):
        self.instantiate.assert_not_called()
        if not self.cleanup_allowed:
            self.terminate.assert_not_called();self.delete.assert_not_called()

    def write(self,state):self.path.write_text(yaml.safe_dump(state));self.before=self.path.read_bytes()
    def post(self,body=None):return self.client.post('/api/adopt-pending',json=body or {'instance_id':'pending','operation_id':'op'})
    def rejected(self):
        self.assertEqual(self.post().status_code,409)
        self.assertEqual(self.path.read_bytes(),self.before)

    def test_exact_completed_ready_running_atomic_adoption(self):
        response=self.post();self.assertEqual(response.status_code,200);self.assertFalse(response.json['already_adopted'])
        saved=yaml.safe_load(self.path.read_bytes())
        expected=copy.deepcopy(self.state);expected['active']='vcran-srsran'
        expected['osm'].pop('pending_instance')
        expected['osm'].update(active_instance_id='pending',active_scenario='vcran-srsran',active_operation_id='op')
        self.assertEqual(saved,expected)
        self.get_instance.assert_called_once_with('pending');self.get_operation.assert_called_once_with('op')

    def test_repeated_adoption_revalidates_and_does_not_rewrite(self):
        self.assertEqual(self.post().status_code,200)
        contents=self.path.read_bytes();mtime=self.path.stat().st_mtime_ns
        with mock.patch.object(backend,'atomic_write') as write:
            response=self.post();self.assertEqual(response.status_code,200)
            self.assertTrue(response.json['already_adopted']);write.assert_not_called()
        self.assertEqual(self.path.read_bytes(),contents);self.assertEqual(self.path.stat().st_mtime_ns,mtime)
        self.assertEqual(self.get_operation.call_count,2)

    def test_processing_failed_partial_and_missing_operation_state_rejected(self):
        for status in ['PROCESSING','FAILED','PARTIALLY_COMPLETED',None]:
            self.operation['operationState']=status;self.rejected()

    def test_exact_request_instance_and_operation_ids_required(self):
        for body in [{'instance_id':'other','operation_id':'op'},{'instance_id':'pending','operation_id':'other'}]:
            self.assertEqual(self.post(body).status_code,409);self.assertEqual(self.path.read_bytes(),self.before)
        self.get_operation.assert_not_called()

    def test_completed_but_not_ready_or_running_rejected(self):
        for field,value in [('nsState','BROKEN'),('nsState',None),('operational-status','stopped'),('operational-status',None)]:
            old=self.instance[field];self.instance[field]=value;self.rejected();self.instance[field]=old

    def test_osm_identity_ownership_type_or_missing_objects_rejected(self):
        for obj,field,value in [(self.operation,'_id','other'),(self.instance,'_id','other'),
                                (self.operation,'nsInstanceId','core'),(self.operation,'lcmOperationType','terminate'),
                                (self.instance,'name','core-persistent'),(self.operation,'_id',None)]:
            old=obj[field];obj[field]=value;self.rejected();obj[field]=old
        self.get_instance.return_value={};self.rejected()

    def test_stale_repeated_request_or_changed_context_rejected(self):
        self.assertEqual(self.post().status_code,200)
        self.before=self.path.read_bytes();self.operation['operationState']='PROCESSING';self.rejected()
        self.operation['operationState']='COMPLETED'
        state=yaml.safe_load(self.before);state['osm']['active_operation_id']='new-op';self.write(state);self.rejected()
        self.write(self.state)
        changed=dict(self.context,namespace='different')
        with mock.patch.object(backend,'runtime_preflight',return_value=({'ACTIVE_STATE_PATH':str(self.path)},changed,None)):
            self.rejected()

    def test_termination_stage_core_overlap_active_conflict_and_no_pending_rejected(self):
        for mode in ['termination','core','active','no-pending']:
            state=copy.deepcopy(self.state)
            if mode=='termination':state['osm']['pending_instance']['termination_operation']='cleanup-op'
            if mode=='core':state['osm']['core_instance_id']='pending'
            if mode=='active':state.update(active='cran-srsran');state['osm'].update(active_scenario='cran-srsran',active_instance_id='old')
            if mode=='no-pending':state['osm'].pop('pending_instance')
            self.write(state);self.rejected()

    def test_failed_operation_retains_existing_cleanup_path(self):
        self.operation['operationState']='FAILED';self.rejected()
        self.instance['nsState']='BROKEN';self.cleanup_allowed=True
        with mock.patch.object(backend.osm_client,'ns_instance_absent',side_effect=[False,True]), \
             mock.patch.object(backend.osm_client,'wait_for_op',return_value='COMPLETED'), \
             mock.patch.object(backend,'_wait_pods_gone',return_value=True):
            self.assertEqual(self.client.post('/api/reconcile-pending',json={'instance_id':'pending'}).status_code,200)
        self.terminate.assert_called_once_with('pending');self.delete.assert_called_once_with('pending')

    def test_adoption_holds_shared_lifecycle_lock_during_reads_and_write(self):
        locked=[]
        @contextlib.contextmanager
        def lock(path):
            self.assertEqual(path,self.path.parent/'.lifecycle.lock');locked.append(True)
            try:yield
            finally:locked.pop()
        def get(op):self.assertTrue(locked);return self.operation
        with mock.patch.object(backend,'exclusive_lock',side_effect=lock), \
             mock.patch.object(backend.osm_client,'get_operation',side_effect=get), \
             mock.patch.object(backend,'atomic_write',wraps=backend.atomic_write) as write:
            self.assertEqual(self.post().status_code,200);write.assert_called_once()

    def test_atomic_write_failure_retains_pending_and_retry_adopts(self):
        with mock.patch.object(backend,'atomic_write',side_effect=OSError('synthetic write failure')):
            self.assertEqual(self.post().status_code,409)
        self.assertEqual(self.path.read_bytes(),self.before)
        self.assertEqual(self.post().status_code,200)
        self.assertNotIn('pending_instance',yaml.safe_load(self.path.read_bytes())['osm'])

    def test_bad_request_shape_rejected(self):
        for body in [{'instance_id':'pending'},{'instance_id':'pending','operation_id':3},
                     {'instance_id':'pending','operation_id':'op','scenario':'vcran-srsran'}]:
            self.assertEqual(self.post(body).status_code,400)
        self.get_operation.assert_not_called()


if __name__=='__main__':unittest.main()
