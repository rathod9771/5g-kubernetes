"""Explicit, locked teardown of one recorded experimental NS; dry-run by default.

No catalog upload, redeploy, direct Kubernetes deletion or platform uninstall.
"""
import argparse
import copy
import json
from pathlib import Path
import sys
import time
import yaml
from local_safety import exclusive_lock, atomic_write
from state_schema import validate_state
from assurance.model import PROTECTED_NS
from runtime_config import load_config, private_text, kubernetes_json
from scenario_registry import load_registry, select_scenarios
from osm_packages import DEFAULT_OUTPUT, REPO_ROOT, validated_snapshot, render
from osm_catalog import identifier, parse_response
sys.path.insert(0,str(REPO_ROOT/'ran-selector'))
import osm_client

JOURNAL='experimental_recovery'
RECEIPT='experimental_recovery_receipt'
PHASES=('requesting','terminating','terminated','deleting')


def resource_targets(scenario, artifacts):
    import tempfile
    names=set()
    with tempfile.TemporaryDirectory() as directory:
        for chart in scenario['charts']:
            prefix=scenario['knf_package']+'/helm-chart-v3s/'+chart['kdu']+'/'
            destination=Path(directory)/chart['kdu']
            for name,data in artifacts.items():
                if name.startswith(prefix):
                    path=destination/name[len(prefix):];path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
            for document in render(destination,chart['release_name']):
                if document['kind'] not in ('Deployment','Service','ConfigMap','PodMonitor'):
                    raise ValueError('Recovery refuses chart with unexpected resource kind')
                names.add((document['kind'],document['metadata']['name']))
    return names


def remaining_resources(cfg, context, targets):
    data=kubernetes_json(cfg,['get','deployments,services,configmaps,podmonitors,pods,replicasets',
                             '-n',context['namespace'],'-o','json'])
    if not isinstance(data,dict) or not isinstance(data.get('items'),list):
        raise ValueError('Cannot verify Kubernetes resource removal')
    deployments={name for kind,name in targets if kind=='Deployment'}
    owned_rs={p['metadata']['name'] for p in data['items'] if p.get('kind')=='ReplicaSet' and any(
        o.get('kind')=='Deployment' and o.get('name') in deployments for o in p['metadata'].get('ownerReferences',[]))}
    found=[]
    for item in data['items']:
        metadata=item.get('metadata',{});kind=item.get('kind');name=metadata.get('name')
        if ((kind,name) in targets or (kind=='ReplicaSet' and name in owned_rs) or
            (kind=='Pod' and (metadata.get('labels',{}).get('app') in deployments or any(
                o.get('kind')=='ReplicaSet' and o.get('name') in owned_rs for o in metadata.get('ownerReferences',[]))))):
            found.append(kind+'/'+name)
    return sorted(found)


def core_healthy(client, core_id):
    core=client.get_ns_instance(core_id)
    if (core.get('_id',core.get('id'))!=core_id or core.get('name',core.get('nsName'))!='core-persistent' or
        core.get('nsState')!='READY' or core.get('operational-status')!='running' or core.get('currentOperation')!='IDLE'):
        raise ValueError('Protected core identity/state is unexpected; refusing recovery')


def check_operation(client, operation_id, target):
    op=client.get_operation(operation_id)
    if (op.get('_id',op.get('id'))!=operation_id or op.get('nsInstanceId')!=target or
        op.get('lcmOperationType')!='terminate'):
        raise ValueError('Termination operation identity/owner/type mismatch')
    return op


def operation_inventory(client):
    ops=parse_response(client._request('GET','/nslcm/v1/ns_lcm_op_occs'))
    if not isinstance(ops,list) or any(not isinstance(o,dict) for o in ops):
        raise ValueError('Invalid OSM operation inventory')
    return ops


def discover_interrupted_operation(client, target, requested_at, existing=()):
    ops=operation_inventory(client)
    matches=[o for o in ops if o.get('nsInstanceId')==target and o.get('lcmOperationType')=='terminate'
             and isinstance(o.get('startTime'),(int,float)) and o['startTime']>=requested_at
             and o.get('_id',o.get('id')) not in existing]
    if len(matches)!=1:
        raise ValueError('Interrupted termination request is ambiguous; retain journal and inspect OSM operations')
    return identifier(matches[0].get('_id',matches[0].get('id')))


def recover(path, state, registry, context, scenario, target, core_id, catalog,
            resources, execute=False, client=osm_client, wait_pods=None, clock=time.time, targets=None, approval=None):
    if target in PROTECTED_NS:
        raise ValueError('Protected Network Service cannot be recovered or terminated')
    if execute and approval != target:
        raise ValueError('Explicit matching instance approval is required for recovery execution')
    validate_state(state,registry,context)
    identifier(target);identifier(core_id)
    osm=state['osm'];key=scenario['key']
    if not scenario.get('experimental') or scenario.get('additive'):
        raise ValueError('Recovery supports only an experimental nonadditive scenario')
    if (target==core_id or osm.get('core_instance_id')!=core_id or
        target in osm.get('additive_instances',{}).values()):
        raise ValueError('Target overlaps protected core/additive identity or core binding differs')
    if osm.get('pending_instance'):
        raise ValueError('Pending lifecycle operation exists; refusing active recovery')
    core_healthy(client,core_id)
    receipt=osm.get(RECEIPT)
    if state.get('active')=='none' and not osm.get('active_instance_id'):
        if (not isinstance(receipt,dict) or receipt.get('instance_id')!=target or
            receipt.get('scenario')!=key or receipt.get('core_id')!=core_id or
            not client.ns_instance_absent(target) or resources()):
            raise ValueError('Missing/mismatched completed recovery evidence or resources remain')
        return {'status':'already_complete','instance_id':target}
    if (state.get('active')!=key or osm.get('active_scenario')!=key or osm.get('active_instance_id')!=target):
        raise ValueError('Target must exactly match recorded active experimental instance/scenario')
    journal=osm.get(JOURNAL)
    if journal and (journal.get('instance_id')!=target or journal.get('scenario')!=key or journal.get('core_id')!=core_id):
        raise ValueError('Recovery journal identity mismatch')
    frozen_targets=sorted([list(t) for t in targets]) if targets is not None else []
    if targets is not None and journal and journal.get('resource_targets')!=frozen_targets:
        raise ValueError('Resource identity set changed during recovery; refusing guessed cleanup')
    nsd_id=catalog.lookup('ns',scenario['nsd_package'])
    if journal and journal.get('nsd_id')!=nsd_id:
        raise ValueError('Catalog identity changed during recovery')
    absent=client.ns_instance_absent(target)
    if absent and (not journal or journal.get('phase') not in ('terminated','deleting')):
        raise ValueError('Active instance unexpectedly absent without completed termination evidence')
    if not absent:
        ns=client.get_ns_instance(target)
        if (ns.get('_id',ns.get('id'))!=target or ns.get('name',ns.get('nsName'))!='ran-'+key or
            ns.get('nsd-id',ns.get('nsdId'))!=nsd_id):
            raise ValueError('Remote NS identity/name/descriptor differs from the recorded target')
        if not journal and (ns.get('nsState')!='READY' or ns.get('currentOperation')!='IDLE'):
            raise ValueError('Target has unexpected state or a competing lifecycle operation')
        if not journal:
            op=client.get_operation(identifier(osm.get('active_operation_id')))
            if (op.get('_id',op.get('id'))!=osm.get('active_operation_id') or op.get('nsInstanceId')!=target or
                op.get('lcmOperationType')!='instantiate' or op.get('operationState')!='COMPLETED'):
                raise ValueError('Recorded instantiate operation is not the exact completed target operation')
    existing=[]
    if not journal:
        for operation in operation_inventory(client):
            if operation.get('nsInstanceId') != target:
                continue
            if operation.get('operationState') not in ('COMPLETED','FAILED','FAILED_TEMP','PARTIALLY_COMPLETED'):
                raise ValueError('Target has a competing nonterminal OSM operation')
            if operation.get('lcmOperationType') == 'terminate':
                existing.append(identifier(operation.get('_id',operation.get('id'))))
        if len(existing)>128:
            raise ValueError('Unexpected termination inventory size')
    elif not absent:
        if journal.get('termination_operation'):
            check_operation(client,journal['termination_operation'],target)
        else:
            discover_interrupted_operation(client,target,journal['requested_at'],journal.get('existing_termination_ops',[]))
    present=resources()
    plan={'status':'preflight','instance_id':target,'protected_core_id':core_id,
          'scenario':key,'resources_present':present,'resume_phase':journal.get('phase') if journal else None}
    if not execute:return plan
    def persist():
        validate_state(state,registry,context)
        atomic_write(path,yaml.safe_dump(state,sort_keys=False).encode())
    if not journal:
        journal={'instance_id':target,'scenario':key,'core_id':core_id,'nsd_id':nsd_id,
                 'phase':'requesting','requested_at':int(clock()),'existing_termination_ops':existing,'resource_targets':frozen_targets}
        osm[JOURNAL]=journal;persist()
        # Journal precedes POST: a lost response is resolved by exact operation inventory, never blind retry.
        journal['termination_operation']=identifier(client.terminate_ns(target))
        journal['phase']='terminating';persist()
    if not absent and journal['phase'] in ('requesting','terminating'):
        if not journal.get('termination_operation'):
            journal['termination_operation']=discover_interrupted_operation(client,target,journal['requested_at'],journal.get('existing_termination_ops',[]))
            journal['phase']='terminating';persist()
        op_id=journal['termination_operation'];check_operation(client,op_id,target)
        if client.wait_for_op(op_id,timeout=300)!='COMPLETED':
            raise RuntimeError('Termination did not complete; journal retained for exact resume')
        if check_operation(client,op_id,target).get('operationState')!='COMPLETED':
            raise RuntimeError('Termination completion cannot be reconfirmed')
        journal['phase']='terminated';persist()
    core_healthy(client,core_id)
    if wait_pods is not None and not wait_pods():
        raise RuntimeError('Experimental pods remain; active state and journal retained')
    if resources():
        raise RuntimeError('Experimental Kubernetes resources remain; refusing NS deletion/state clearing')
    if not client.ns_instance_absent(target):
        terminated=client.get_ns_instance(target)
        if (terminated.get('_id',terminated.get('id'))!=target or
            terminated.get('name',terminated.get('nsName'))!='ran-'+key or
            terminated.get('nsd-id',terminated.get('nsdId'))!=nsd_id or
            terminated.get('nsState')!='NOT_INSTANTIATED' or terminated.get('currentOperation')!='IDLE'):
            raise RuntimeError('NS is not exactly the confirmed terminated target; refusing record deletion')
        if check_operation(client,journal['termination_operation'],target).get('operationState')!='COMPLETED':
            raise RuntimeError('Recorded termination is no longer confirmed complete')
        journal['phase']='deleting';persist()
        if not client.delete_ns_instance(target):
            raise RuntimeError('NS record deletion failed; journal retained')
    if not client.ns_instance_absent(target) or resources():
        raise RuntimeError('NS/resource absence unconfirmed; active state and journal retained')
    core_healthy(client,core_id)
    osm[RECEIPT]=copy.deepcopy(journal)
    osm[RECEIPT]['phase']='completed'
    osm.pop(JOURNAL,None)
    for field in ('active_instance_id','active_scenario','active_operation_id'):osm.pop(field,None)
    state['active']='none';persist()
    return {'status':'complete','instance_id':target,'protected_core_id':core_id}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario',required=True)
    parser.add_argument('--instance-id',required=True)
    parser.add_argument('--protect-core',required=True)
    parser.add_argument('--approve-instance-id',help='Separately approved exact target; required with --execute')
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--execute',action='store_true')
    mode.add_argument('--dry-run',action='store_true')
    args=parser.parse_args()
    try:
        cfg=load_config(network='auto',require=('osm','kubernetes'))
        path=Path(cfg['ACTIVE_STATE_PATH'])
        with exclusive_lock(path.parent/'.lifecycle.lock'):
            text,_=private_text(path,required=True,strict=True)
            try:state=yaml.safe_load(text)
            except yaml.YAMLError:raise ValueError('Malformed runtime state (values suppressed)') from None
            registry=load_registry();scenario=select_scenarios(registry,[args.scenario])[0]
            with osm_client.runtime_session(cfg):
                from osm_catalog import Catalog
                catalog=Catalog(cfg['OSM_HOST'],osm_client.get_token(),cfg.get('OSM_CA_CERT_PATH') or None)
                context=osm_client.context(cfg,catalog)
                validate_state(state,registry,context)
                artifacts=validated_snapshot(REPO_ROOT,DEFAULT_OUTPUT,scenario)
                targets=resource_targets(scenario,artifacts)
                def resources():return remaining_resources(cfg,context,targets)
                import backend
                with backend.app.app_context():
                    from flask import g
                    g.runtime_config,g.runtime_context,g.runtime_state_path=cfg,context,str(path)
                    result=recover(path,state,registry,context,scenario,args.instance_id,args.protect_core,catalog,
                                   resources,args.execute,wait_pods=lambda:backend._wait_pods_gone(scenario['pods'],scenario.get('selector')),targets=targets,approval=args.approve_instance_id)
            print(json.dumps(result,indent=2))
        return 0
    except (OSError,ValueError,RuntimeError) as error:
        parser.exit(1,'ERROR: '+str(error)+'\n')

if __name__=='__main__':raise SystemExit(main())
