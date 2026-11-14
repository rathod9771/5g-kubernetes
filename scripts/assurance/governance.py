"""Future execution preflight uses the SAME lifecycle lock and recovery state schema."""
from contextlib import contextmanager
import json
from pathlib import Path
import time
import yaml
from local_safety import exclusive_lock
from state_schema import validate_state
from .model import PROTECTED_NS

class LifecycleGate:
    def __init__(self, active_state_path, registry, expected_context):
        self.path=Path(active_state_path);self.registry=registry;self.context=expected_context
    @contextmanager
    def locked_state(self):
        with exclusive_lock(self.path.parent/'.lifecycle.lock'):
            state=validate_state(yaml.safe_load(self.path.read_text()),self.registry,self.context)
            osm=state.get('osm',{})
            if osm.get('pending_instance') or osm.get('experimental_recovery'):
                raise PermissionError('Existing lifecycle operation or recovery journal blocks remediation')
            if osm.get('active_instance_id') in PROTECTED_NS:
                raise PermissionError('Active NS is protected')
            if not osm.get('core_instance_id') or osm.get('active_instance_id')==osm.get('core_instance_id'):
                raise PermissionError('Core ownership is unknown or overlaps action target')
            yield state


def transition_matrix(registry):
    scenarios=[s for s in registry['scenarios'] if s['key']!='none' and not s.get('additive')]
    return [{'from':a['key'],'to':b['key'],'proposal_only':True,'auto_allowed':False,
        'status':'UNVALIDATED' if not b.get('blocked_reason') else 'UNAVAILABLE',
        'target_blocked_reason':b.get('blocked_reason'),
        'required':['active NS identity','target readiness','core compatibility','radio compatibility',
            'UE session continuity assessment','capacity','service interruption approval',
            'OSM ownership','rollback readiness'],
        'hitless':False,'rollback':'Operator-reviewed recovery of previous profile; continuity unproven'}
        for a in scenarios for b in scenarios if a['key']!=b['key']]


def inspect_lock(path):
    """Read Linux lock metadata without taking, creating or releasing the lifecycle lock."""
    import os
    try:
        metadata=Path(path).stat()
        target=(os.major(metadata.st_dev),os.minor(metadata.st_dev),metadata.st_ino)
        locked=False
        for line in Path('/proc/locks').read_text().splitlines():
            for field in line.split():
                pieces=field.split(':')
                if len(pieces)!=3:continue
                try:identity=(int(pieces[0],16),int(pieces[1],16),int(pieces[2]))
                except ValueError:continue
                if identity==target:locked=True
        return {'provenance':'LIVE','locked':locked,'observed_at':time.time(),
                'note':'Instantaneous Linux lock-table observation; executor must still acquire shared lifecycle lock'}
    except OSError:
        return {'provenance':'UNAVAILABLE','locked':None,'observed_at':time.time()}
