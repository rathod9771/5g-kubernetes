"""Fail-closed instance adapter. No certified upgrade/rollback backend exists yet.

Static eligibility is useful for review, but never authorizes an OSM submission.
Existing Catalog/lifecycle clients remain the only future transport integration.
"""
import time
import uuid
from assurance.model import PROTECTED_NS
from .contract import identity
from .security import Denied

class Lifecycle:
    installation_status='NOT_VERIFIED'
    def __init__(self, service, policy):self.service,self.policy=service,policy
    def plan(self, principal, release_id, instance_id, rollback_release_id, evidence):
        principal.require('operator')
        if str(uuid.UUID(instance_id))!=instance_id or instance_id in PROTECTED_NS: raise Denied('Protected/invalid instance')
        ownership=self.policy.value.get('instance_ownership',{}).get(instance_id)
        if not ownership or ownership.get('tenant')!=principal.tenant or ownership.get('scope')!='DISPOSABLE_RESEARCH_INSTANCE':
            raise Denied('Verified instance ownership required')
        release=self.service.journal.get(principal,'releases',release_id)
        rollback=self.service.journal.get(principal,'releases',rollback_release_id)
        if release['workload_id']!=rollback['workload_id'] or release_id==rollback_release_id:
            raise Denied('Distinct versioned rollback artifact required')
        if release.get('pipeline_version')!=2 or rollback.get('pipeline_version')!=2:
            raise Denied('Verified artifact identities required')
        if evidence.get('provenance')!='LIVE' or not 0<=time.time()-evidence.get('at',0)<=30:
            raise Denied('Fresh live context required; simulated evidence rejected')
        if evidence.get('pending_operations')!=[] or evidence.get('operational_state')!='running' or evidence.get('config_state')!='configured':
            raise Denied('Lifecycle conflict or unhealthy instance')
        if evidence.get('instance_id')!=instance_id or evidence.get('cluster_uid')!=ownership.get('cluster_uid') or evidence.get('namespace_uid')!=ownership.get('namespace_uid'):
            raise Denied('Instance/cluster/namespace ownership evidence mismatch')
        # A caller assertion of LIVE does not enable execution: installation status
        # is hard-blocked below until the adapter and trusted readers are certified.
        return {'id':identity({'release':release_id,'rollback':rollback_release_id,'instance':instance_id,'evidence':evidence}),
                'instance_id':instance_id,'release_id':release_id,'rollback_release_id':rollback_release_id,
                'status':'NOT_VERIFIED','rollback_eligibility':'ARTIFACTS_ONLY; OSM_AND_STATE_COMPATIBILITY_NOT_VERIFIED',
                'required_checks':['OSM_SUPPORTED_OPERATION','NO_PENDING_OPERATION','EXPLICIT_APPROVAL',
                                   'ROLLOUT_UID_GENERATION_IMAGE_DIGEST','HEALTH_AND_TELECOM_TESTS','STATE_COMPATIBLE_ROLLBACK'],
                'production_execution':'DISABLED'}
    def execute(self, *args, **kwargs):
        raise Denied('NOT_VERIFIED: no certified OSM upgrade/rollback or rollout adapter; no lifecycle request sent')
