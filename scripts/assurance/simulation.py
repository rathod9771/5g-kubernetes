"""Sandboxed synthetic fault traces. No production executor import or injectable callback."""
import tempfile
from pathlib import Path
from .store import Store
from .engine import Engine
from .catalog import load_catalog
from .policy import Verification
from .journal import ActionJournal

SCENARIOS = (
 ('du_failure','DUUnavailable',{'pod_ready':False,'f1_association':False}),
 ('cu_failure','CUUnavailable',{'pod_ready':False,'ngap_association':False,'f1_association':False}),
 ('f1_loss','F1AssociationLost',{'f1_association':False,'pod_ready':True}),
 ('ngap_loss','NGAPAssociationLost',{'ngap_association':False,'core_healthy':True}),
 ('amf_unavailable','AMFUnavailable',{'metric_present':True,'service_degraded':True}),
 ('upf_unavailable','UPFUnavailable',{'metric_present':True,'service_degraded':True}),
 ('cpu_overload','HostCPUContentionHigh',{'host_pressure':0.45,'service_degraded':True}),
 ('memory_pressure','KubernetesMemoryPressure',{'metric_present':True,'service_degraded':True}),
 ('fog_latency','FogProcessingLatencyHigh',{'fog_latency':0.5,'metric_present':True,'service_degraded':True}),
 ('osm_failure','OSMLifecycleOperationFailed',{'operation_failed':True,'osm_trustworthy':False}),
 ('monitoring_loss','MonitoringPipelineDegraded',{'metric_present':False}),
 ('node_failure','KubernetesNodeNotReady',{'node_ready':False,'metric_present':True,'service_degraded':True}),
 ('crash_loops','PodCrashLoop',{'pod_ready':False,'metric_present':True,'service_degraded':True}),
 ('network_congestion','NetworkCongestion',{'packet_loss':0.12,'metric_present':True,'service_degraded':True}),
 ('protected_emergency_policy','EmergencyServiceProtection',{'protected_state':True}),
 ('registration_failures','UERegistrationFailureRateHigh',{'registration_failures':20,'metric_present':True,'service_degraded':True}))


def run_scenario(name):
    scenario=next((s for s in SCENARIOS if s[0]==name),None)
    if scenario is None:raise ValueError('Unknown simulation scenario')
    with tempfile.TemporaryDirectory(prefix='assurance-simulation-') as tmp:
        now=[2000000000.0]; store=Store(Path(tmp)/'simulation.sqlite')
        engine=Engine(store,load_catalog(),lambda:now[0],simulation=True)
        engine.control(mode='SIMULATION')
        event={'alarm_type':scenario[1],'cluster':'sandbox','namespace':'synthetic',
            'resource':'synthetic-'+name,'service':'synthetic-service','node':'synthetic-node',
            'observations':scenario[2]}
        first=engine.ingest({**event,'timestamp':now[0]})
        now[0]+=60
        alarm=engine.ingest({**event,'timestamp':now[0]})
        snapshot=store.snapshot(); incident=snapshot['incident'][0]; action=snapshot['action'][0]
        # This is an invented recovery outcome, explicitly synthetic, never a live measurement.
        journal=ActionJournal(store)
        journal.claim_simulated(action,now[0])
        evidence={key:True for key in action['verification']}
        verified=Verification.assess(evidence,action['verification'])
        with store.transaction() as db:
            if incident['state']=='APPROVAL_PENDING':
                engine.transition(db,incident,'EXECUTING',now[0])
                action['status']='SIMULATED'; action['execution_status']='SIMULATED'
                engine.transition(db,incident,'VERIFYING',now[0])
            if incident['state']=='VERIFYING' or incident['state']=='ESCALATED':
                engine.transition(db,incident,'RESOLVED',now[0])
            incident.update(verification_evidence={'provenance':'SIMULATED','checks':evidence},
                            final_outcome='SIMULATED verification; no production action',recovery_duration=60)
            store.put(db,'incident',incident['incident_id'],incident)
            store.put(db,'action',action['action_id'],action)
            store.record(db,now[0],'simulation_verified',incident['incident_id'],{'provenance':'SIMULATED','passed':verified['passed']})
        journal.finish_simulated(action['action_id'],'VERIFIED',now[0])
        return {'scenario':name,'provenance':'SIMULATED','alarm_type':alarm['alarm_type'],
            'severity':alarm['severity'],'incident_state':incident['state'],'action':action['action'],
            'class':action['class'],'verification':verified,'audit_valid':store.verify_audit(),
            'production_calls':0,'timeline':incident['timeline']}


def run_all():return [run_scenario(s[0]) for s in SCENARIOS]
