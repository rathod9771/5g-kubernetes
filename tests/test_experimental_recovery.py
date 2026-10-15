"""No live calls: exact experimental teardown ownership and interrupted recovery."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import yaml
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'tests')]
import recover_experimental_ran as recovery
from scenario_registry import load_registry,select_scenarios
from state_schema import validate_state
from runtime_config import ConfigError

def uid(n):return f'00000000-0000-4000-8000-{n:012d}'

class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'state.yaml'
        self.registry=load_registry();self.scenario=select_scenarios(self.registry,['fran-oai'])[0]
        self.target,self.core,self.instantiate,self.terminate,self.nsd=map(uid,range(1,6))
        self.context={k:uid(9) for k in ['project_id','vim_id','cluster_uid','namespace_uid']}
        self.context.update(namespace='test',repo_root='/test',workload_kubeconfig='/test/kubeconfig',deployment_profile='rfsim',osm_host='https://test')
        self.state={'active':'fran-oai','context':copy.deepcopy(self.context),'unrelated':{'keep':True},
                    'osm':{'active_instance_id':self.target,'active_scenario':'fran-oai','active_operation_id':self.instantiate,'core_instance_id':self.core}}
        self.path.write_text(yaml.safe_dump(self.state));self.before=self.path.read_bytes()
        self.deleted=False;self.terminated=False
        self.client=mock.Mock();self.client._request.return_value=b'[]';self.client.ns_instance_absent.side_effect=lambda target:self.deleted
        def instance(target):
            if target==self.core:return {'_id':self.core,'name':'core-persistent','nsState':'READY','currentOperation':'IDLE','operational-status':'running'}
            return {'_id':self.target,'name':'ran-fran-oai','nsd-id':self.nsd,'nsState':'NOT_INSTANTIATED' if self.terminated else 'READY','currentOperation':'IDLE'}
        self.client.get_ns_instance.side_effect=instance
        self.client.get_operation.side_effect=lambda op:{'_id':op,'nsInstanceId':self.target,'lcmOperationType':'instantiate' if op==self.instantiate else 'terminate','operationState':'COMPLETED'}
        self.client.terminate_ns.return_value=self.terminate
        def wait(*args,**kw):self.terminated=True;return 'COMPLETED'
        self.client.wait_for_op.side_effect=wait
        def delete(target):self.deleted=True;return True
        self.client.delete_ns_instance.side_effect=delete
        self.catalog=mock.Mock();self.catalog.lookup.return_value=self.nsd
        self.resources=mock.Mock(side_effect=lambda:['Deployment/fran-oai-cu'] if not self.terminated else [])
    def run_recovery(self,execute=False,target=None):
        return recovery.recover(self.path,self.state,self.registry,self.context,self.scenario,target or self.target,self.core,self.catalog,self.resources,execute,self.client,wait_pods=lambda:True,clock=lambda:100,approval=(target or self.target) if execute else None)
    def test_missing_explicit_approval_cannot_execute(self):
        with self.assertRaisesRegex(ValueError,'approval'):
            recovery.recover(self.path,self.state,self.registry,self.context,self.scenario,self.target,self.core,self.catalog,self.resources,True,self.client)
        self.client.terminate_ns.assert_not_called()
    def test_both_protected_network_services_are_rejected(self):
        for target in recovery.PROTECTED_NS:
            with self.assertRaisesRegex(ValueError,'Protected Network'):
                self.run_recovery(True,target)
        self.client.terminate_ns.assert_not_called()
        self.client.delete_ns_instance.assert_not_called()
    def test_dry_run_never_mutates_or_calls_lifecycle(self):
        self.assertEqual(self.run_recovery()['status'],'preflight')
        self.assertEqual(self.path.read_bytes(),self.before)
        self.client.terminate_ns.assert_not_called();self.client.delete_ns_instance.assert_not_called()
    def test_exact_cleanup_preserves_core_context_and_unrelated(self):
        self.assertEqual(self.run_recovery(True)['status'],'complete')
        self.client.terminate_ns.assert_called_once_with(self.target);self.client.delete_ns_instance.assert_called_once_with(self.target)
        saved=yaml.safe_load(self.path.read_text());self.assertEqual(saved['active'],'none')
        self.assertEqual(saved['osm']['core_instance_id'],self.core);self.assertEqual(saved['context'],self.context)
        self.assertEqual(saved['unrelated'],{'keep':True});self.assertNotIn('active_operation_id',saved['osm'])
        self.assertNotIn(recovery.JOURNAL,saved['osm']);self.assertEqual(saved['osm'][recovery.RECEIPT]['phase'],'completed')
        self.assertEqual(self.run_recovery(True)['status'],'already_complete')
        self.client.terminate_ns.assert_called_once()
    def test_core_and_wrong_instance_rejected_before_mutation(self):
        for target in [self.core,uid(999)]:
            with self.assertRaises(ValueError):self.run_recovery(True,target)
        self.assertEqual(self.path.read_bytes(),self.before);self.client.terminate_ns.assert_not_called()
    def test_pending_and_context_mismatch_rejected(self):
        self.state['osm']['pending_instance']={'id':uid(22),'operation':uid(23),'scenario':'cran-oai'}
        with self.assertRaises(ValueError):self.run_recovery(True)
        self.state['osm'].pop('pending_instance');self.state['context']['cluster_uid']=uid(44)
        with self.assertRaises(ValueError):self.run_recovery(True)
        self.client.terminate_ns.assert_not_called()
    def test_wrong_remote_identity_or_descriptor_rejected(self):
        original=self.client.get_ns_instance.side_effect
        for field,value in [('_id',uid(77)),('name','core-persistent'),('nsd-id',uid(66)),('currentOperation','INSTANTIATING')]:
            def changed(target):
                d=original(target)
                if target==self.target:d[field]=value
                return d
            self.client.get_ns_instance.side_effect=changed
            with self.assertRaises(ValueError):self.run_recovery(True)
        self.client.terminate_ns.assert_not_called()
    def test_failed_wait_retains_journal_and_reuses_operation(self):
        self.client.wait_for_op.side_effect=None;self.client.wait_for_op.return_value='TIMEOUT'
        with self.assertRaises(RuntimeError):self.run_recovery(True)
        saved=yaml.safe_load(self.path.read_text());self.assertEqual(saved['active'],'fran-oai')
        self.assertEqual(saved['osm'][recovery.JOURNAL]['termination_operation'],self.terminate)
        self.client.wait_for_op.side_effect=lambda *a,**kw:(setattr(self,'terminated',True) or 'COMPLETED')
        self.run_recovery(True);self.client.terminate_ns.assert_called_once()
    def test_resources_remaining_blocks_delete_and_clear(self):
        self.resources.side_effect=None;self.resources.return_value=['Service/fran-oai-cu']
        with self.assertRaisesRegex(RuntimeError,'resources remain'):self.run_recovery(True)
        self.client.delete_ns_instance.assert_not_called();self.assertEqual(self.state['active'],'fran-oai')
    def test_unexpected_absence_without_journal_rejected(self):
        self.deleted=True
        with self.assertRaisesRegex(ValueError,'unexpectedly absent'):self.run_recovery(True)
        self.client.terminate_ns.assert_not_called()
    def test_interrupted_post_discovers_exact_operation_without_reposting(self):
        self.state['osm'][recovery.JOURNAL]={'instance_id':self.target,'scenario':'fran-oai','core_id':self.core,'nsd_id':self.nsd,'phase':'requesting','requested_at':100}
        self.client._request.return_value=yaml.safe_dump([{'_id':self.terminate,'nsInstanceId':self.target,'lcmOperationType':'terminate','startTime':101}]).encode()
        self.run_recovery(True);self.client.terminate_ns.assert_not_called()
    def test_ambiguous_interrupted_post_refused(self):
        self.state['osm'][recovery.JOURNAL]={'instance_id':self.target,'scenario':'fran-oai','core_id':self.core,'nsd_id':self.nsd,'phase':'requesting','requested_at':100}
        self.client._request.return_value=b'[]'
        with self.assertRaisesRegex(ValueError,'ambiguous'):self.run_recovery(True)
        self.client.terminate_ns.assert_not_called();self.client.delete_ns_instance.assert_not_called()
    def test_wrong_operation_owner_refused(self):
        self.client.get_operation.side_effect=None
        self.client.get_operation.return_value={'_id':self.instantiate,'nsInstanceId':self.core,'lcmOperationType':'instantiate','operationState':'COMPLETED'}
        with self.assertRaises(ValueError):self.run_recovery(True)
        self.client.terminate_ns.assert_not_called()
    def test_resume_after_record_delete_clears_only_after_resource_absence(self):
        self.state['osm'][recovery.JOURNAL]={'instance_id':self.target,'scenario':'fran-oai','core_id':self.core,'nsd_id':self.nsd,'phase':'deleting','requested_at':100,'termination_operation':self.terminate}
        self.deleted=True;self.terminated=True
        self.run_recovery(True);self.client.terminate_ns.assert_not_called();self.client.delete_ns_instance.assert_not_called()
    def test_remote_still_ready_after_completed_terminate_cannot_be_deleted(self):
        self.client.wait_for_op.side_effect=None;self.client.wait_for_op.return_value='COMPLETED'
        self.resources.side_effect=None;self.resources.return_value=[]
        with self.assertRaisesRegex(RuntimeError,'confirmed terminated'):self.run_recovery(True)
        self.client.delete_ns_instance.assert_not_called()
    def test_competing_deploy_is_blocked_by_journal(self):
        import test_pending_adoption as a
        self.state['osm'][recovery.JOURNAL]={'instance_id':self.target,'scenario':'fran-oai','core_id':self.core,'nsd_id':self.nsd,'phase':'requesting','requested_at':100}
        self.path.write_text(yaml.safe_dump(self.state))
        with a.backend.app.test_request_context(),mock.patch.object(a.backend,'_state_path',return_value=self.path),mock.patch.object(a.backend.osm_client,'terminate_ns') as terminate:
            with self.assertRaisesRegex(ValueError,'competing deployments'):
                a.backend._deploy_verified('cran-oai',self.registry,{}, {},select_scenarios(self.registry,['cran-oai'])[0],self.context,{})
            terminate.assert_not_called()

    def test_source_resource_identity_change_during_resume_refused(self):
        self.state['osm'][recovery.JOURNAL]={'instance_id':self.target,'scenario':'fran-oai','core_id':self.core,'nsd_id':self.nsd,'phase':'terminating','requested_at':100,'termination_operation':self.terminate,'resource_targets':[['Deployment','fran-oai-cu']]}
        with self.assertRaisesRegex(ValueError,'Resource identity'):
            recovery.recover(self.path,self.state,self.registry,self.context,self.scenario,self.target,self.core,self.catalog,self.resources,True,self.client,targets={('Deployment','different-cu')},approval=self.target)
        self.client.terminate_ns.assert_not_called();self.client.delete_ns_instance.assert_not_called()
    def test_old_termination_operation_cannot_be_adopted_after_lost_response(self):
        self.state['osm'][recovery.JOURNAL]={'instance_id':self.target,'scenario':'fran-oai','core_id':self.core,'nsd_id':self.nsd,'phase':'requesting','requested_at':100,'existing_termination_ops':[self.terminate]}
        self.client._request.return_value=yaml.safe_dump([{'_id':self.terminate,'nsInstanceId':self.target,'lcmOperationType':'terminate','startTime':100,'operationState':'COMPLETED'}]).encode()
        with self.assertRaisesRegex(ValueError,'ambiguous'):self.run_recovery(True)
        self.client.terminate_ns.assert_not_called()
    def test_competing_remote_operation_refused(self):
        self.client._request.return_value=yaml.safe_dump([{'_id':uid(88),'nsInstanceId':self.target,'lcmOperationType':'terminate','operationState':'PROCESSING'}]).encode()
        with self.assertRaisesRegex(ValueError,'nonterminal'):self.run_recovery(True)
        self.client.terminate_ns.assert_not_called()
    def test_failed_delete_preserves_resumable_active_binding(self):
        self.client.delete_ns_instance.side_effect=RuntimeError('synthetic transport failure')
        with self.assertRaisesRegex(RuntimeError,'transport'):self.run_recovery(True)
        self.assertEqual(self.state['active'],'fran-oai');self.assertEqual(self.state['osm'][recovery.JOURNAL]['phase'],'deleting')
        self.deleted=True
        self.run_recovery(True);self.client.terminate_ns.assert_called_once()
