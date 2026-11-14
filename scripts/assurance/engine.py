"""Event-driven alarm lifecycle and incident pipeline. No polling and no live mutations."""
import uuid
from .model import normalize, timestamp, identity, digest, SEVERITIES
from .policy import Policy, SafetyValidator

TRANSITIONS = {
 'DETECTED':('TRIAGED',), 'TRIAGED':('CORRELATED',),
 'CORRELATED':('IMPACT_ASSESSED',), 'IMPACT_ASSESSED':('ACTION_PROPOSED',),
 'ACTION_PROPOSED':('APPROVAL_PENDING','ESCALATED'),
 'APPROVAL_PENDING':('EXECUTING','ESCALATED','RESOLVED'),
 'EXECUTING':('VERIFYING','FAILED','ESCALATED'),
 'VERIFYING':('RESOLVED','FAILED','ESCALATED'),
 'FAILED':('ESCALATED',), 'ESCALATED':('RESOLVED',), 'RESOLVED':()}

class Correlator:
    @staticmethod
    def group(alarm):
        # Unknown service identity must not correlate unrelated resources.
        scope = alarm['service'] if alarm['service'] != 'unknown' else alarm['resource']
        return digest([alarm['provenance'],alarm['cluster'],alarm['namespace'],scope])
    @staticmethod
    def inhibited(alarm, alarms):
        for parent in alarms:
            if parent['state'] == 'CLEARED' or parent['stale'] or parent['provenance'] != alarm['provenance']:
                continue
            if parent['cluster'] != alarm['cluster'] or alarm['cluster']=='unknown':
                continue
            if parent['alarm_type'] == 'KubernetesNodeNotReady' and parent['node'] == alarm['node'] != 'unknown' and parent['alarm_id'] != alarm['alarm_id']:
                return parent['alarm_id']
            if parent['service'] == alarm['service'] != 'unknown' and parent['namespace']==alarm['namespace']:
                if (parent['alarm_type'], alarm['alarm_type']) in (('CUUnavailable','F1AssociationLost'),('CUUnavailable','NGAPAssociationLost'),('DUUnavailable','F1AssociationLost')):
                    return parent['alarm_id']
        return None

class Engine:
    def __init__(self, store, catalog, clock, *, simulation=False):
        self.store,self.catalog,self.clock,self.simulation = store,catalog,clock,simulation
        self.policy = Policy(catalog)
    def transition(self, db, incident, state, at):
        if state not in TRANSITIONS.get(incident['state'], ()):
            raise ValueError('Invalid incident transition')
        incident['state'] = state
        incident['timeline'].append({'at':at,'state':state})
        self.store.record(db,at,'incident_transition',incident['incident_id'],{'state':state})
    def ingest(self, raw):
        now = timestamp(self.clock())
        event = normalize(raw, now, simulation=self.simulation)
        if event['alarm_type'] not in self.catalog:
            raise ValueError('Alarm absent from reviewed catalog')
        with self.store.transaction() as db:
            old = self.store.get(db,'alarm',event['key'])
            if old and event['timestamp'] < old['timestamp']:
                self.store.record(db,now,'out_of_order',old['alarm_id'],{})
                return old
            if old and old['delivery_id'] == event['delivery_id']:
                return old
            rule = self.catalog[event['alarm_type']]
            # Keep observed severity explicit; reviewed catalog sets minimum priority.
            event['severity'] = min((event['severity'],rule['severity']),key=SEVERITIES.index)
            recurring = old is None or old['state']=='CLEARED' and event['status']=='firing'
            reset = old is None or recurring or event['timestamp']-old['last_seen'] > rule['max_gap'] or old['status'] != event['status']
            alarm = {**(old or {}),**event,'alarm_id':old['alarm_id'] if old else str(uuid.uuid4()),
                'first_seen':event['timestamp'] if recurring else old['first_seen'],
                'last_seen':event['timestamp'], 'condition_since':event['timestamp'] if reset else old['condition_since'],
                'samples':1 if reset else old.get('samples',1)+int(event['timestamp']>old['timestamp']),
                'acknowledged':False if recurring else old['acknowledged'],
                'state':'RAISED' if recurring else old['state'],
                'resolved':bool(old and old['state']=='CLEARED' and event['status']=='resolved'),
                'stale':now-event['timestamp'] > rule['max_age'],
                'history':list(old.get('history',[]) if old else []),
                'correlation_id':Correlator.group(event)}
            alarm['history'] = [x for x in alarm['history'] if x['at'] >= now-600]
            if not old or old['status'] != event['status']:
                alarm['history'].append({'at':event['timestamp'],'status':event['status']})
            alarm['flapping'] = len(alarm['history']) >= 4
            alarm['history'] = alarm['history'][-20:]
            if event['status']=='resolved' and old is None:
                alarm.update(state='CLEARED',resolved=True)
            if event['status']=='resolved':
                # Recovery hysteresis: resolved updates must persist, not one clear packet.
                if event['timestamp']-alarm['condition_since'] >= rule['clear_persistence'] and not alarm['stale']:
                    alarm.update(state='CLEARED',resolved=True)
            elif old and not recurring:
                alarm['state']='ACKNOWLEDGED' if alarm['acknowledged'] else 'UPDATED'
            self.store.put(db,'alarm',alarm['key'],alarm)
            self.store.record(db,now,'alarm_observed',alarm['alarm_id'],
                {'alarm_type':alarm['alarm_type'],'state':alarm['state'],'provenance':alarm['provenance'],'timestamp':event['timestamp']})
            self._analyze(db,alarm,now)
            # Reassess dependent alarms when a root alarm appears or clears.
            for dependent in self.store.all(db,'alarm'):
                if dependent['key']!=alarm['key']:
                    self._analyze(db,dependent,now)
            return self.store.get(db,'alarm',alarm['key'])
    def _analyze(self, db, alarm, now):
        rule=self.catalog[alarm['alarm_type']]
        alarm['stale'] = now-alarm['last_seen']>rule['max_age']
        alarm['inhibited_by'] = Correlator.inhibited(alarm,self.store.all(db,'alarm'))
        alarm['maintenance'] = any(m['start']<=now<m['end'] and m['service']==alarm['service'] for m in self.store.all(db,'maintenance'))
        decision=self.policy.evaluate(alarm)
        alarm.update(probable_cause=decision['hypothesis'],service_impact=decision['service_impact'],
                     recommended_action=decision['action'],automation_eligible=False,runbook=decision['runbook'])
        self.store.put(db,'alarm',alarm['key'],alarm)
        previous = self.store.get(db,'incident',alarm.get('incident_id',''))
        if alarm['state']=='CLEARED':
            if previous and previous['state'] in ('APPROVAL_PENDING','ESCALATED'):
                members=[a for a in self.store.all(db,'alarm') if a.get('incident_id')==previous['incident_id']]
                if members and all(a['state']=='CLEARED' for a in members):
                    self.transition(db,previous,'RESOLVED',now)
                    previous.update(final_outcome='Condition cleared; no live remediation executed',recovery_duration=now-previous['detected_at'])
                    self.store.put(db,'incident',previous['incident_id'],previous)
                    for action in self.store.all(db,'action'):
                        if action['incident_id']==previous['incident_id'] and action['status'] in ('PROPOSED','APPROVED'):
                            action['status']='CANCELLED'
                            self.store.put(db,'action',action['action_id'],action)
                            self.store.record(db,now,'action_cancelled',action['action_id'],{'reason':'condition cleared'})
            return
        eligible = ((rule['persistence']==0 or alarm.get('samples',1)>=2) and alarm['status']=='firing' and not alarm['stale'] and not alarm['flapping'] and not alarm['maintenance'] and not alarm['inhibited_by'] and now-alarm['condition_since']>=rule['persistence'])
        if not eligible:
            return
        if previous and previous['state']!='RESOLVED':
            return
        incident=next((i for i in self.store.all(db,'incident') if i['correlation_id']==alarm['correlation_id'] and i['state']!='RESOLVED'),None)
        if incident:
            if alarm['alarm_id'] not in incident['source_alarms']:
                incident['source_alarms'].append(alarm['alarm_id'])
                incident['severity']=min((incident['severity'],alarm['severity']),key=SEVERITIES.index)
                self.store.record(db,now,'alarm_correlated',incident['incident_id'],{'alarm_id':alarm['alarm_id']})
                self.store.put(db,'incident',incident['incident_id'],incident)
        else:
            incident={'incident_id':str(uuid.uuid4()),'correlation_id':alarm['correlation_id'],
                'source_alarms':[alarm['alarm_id']],'affected_services':[alarm['service']],
                'resource_ids':[alarm['observations'].get('resource_uid',alarm['resource'])],
                'severity':alarm['severity'],'state':'DETECTED','detected_at':now,
                'timeline':[{'at':now,'state':'DETECTED'}],'root_cause_hypothesis':decision['hypothesis'],
                'provenance':alarm['provenance'],'operator_decisions':[],'verification_evidence':{},
                'osm_operation_id':alarm['observations'].get('operation_id'), 'final_outcome':None}
            self.store.record(db,now,'incident_detected',incident['incident_id'],{'alarm_id':alarm['alarm_id']})
            for state in ('TRIAGED','CORRELATED','IMPACT_ASSESSED','ACTION_PROPOSED'):
                self.transition(db,incident,state,now)
            action_id=digest([incident['incident_id'],decision['action']])
            action={**decision,'action_id':action_id,'incident_id':incident['incident_id'],
                'service':alarm['service'],'provenance':alarm['provenance'],'status':'PROPOSED',
                'created_at':now,'execution_status':'DISABLED','attempts':0}
            incident['proposed_action']=action_id
            self.store.put(db,'action',action_id,action)
            self.store.record(db,now,'action_proposed',action_id,{'action':decision['action'],'class':decision['class'],'provenance':alarm['provenance']})
            self.transition(db,incident,'APPROVAL_PENDING' if decision['class']=='C' else 'ESCALATED',now)
            self.store.put(db,'incident',incident['incident_id'],incident)
        alarm['incident_id']=incident['incident_id']
        self.store.put(db,'alarm',alarm['key'],alarm)
    def refresh(self):
        now=timestamp(self.clock())
        with self.store.transaction() as db:
            for alarm in self.store.all(db,'alarm'):
                self._analyze(db,alarm,now)
    def control(self, mode=None, emergency_stop=None):
        if mode is not None and mode not in ('OBSERVE_ONLY','SIMULATION','APPROVAL_REQUIRED'):
            raise ValueError('LIVE_AUTO is disabled')
        if emergency_stop is not None and type(emergency_stop) is not bool:
            raise ValueError('Emergency stop must be boolean')
        with self.store.transaction() as db:
            value=self.store.get(db,'control','governance',{'mode':'OBSERVE_ONLY','emergency_stop':False})
            if mode is not None:value['mode']=mode
            if emergency_stop is not None:value['emergency_stop']=emergency_stop
            self.store.put(db,'control','governance',value)
            self.store.record(db,self.clock(),'governance_changed','governance',value)
        return value
    def acknowledge(self, key, actor):
        actor=identity(actor)
        with self.store.transaction() as db:
            alarm=self.store.get(db,'alarm',key)
            if not alarm or alarm['state']=='CLEARED':raise ValueError('No active alarm')
            alarm.update(acknowledged=True,state='ACKNOWLEDGED')
            self.store.put(db,'alarm',key,alarm)
            self.store.record(db,self.clock(),'alarm_acknowledged',alarm['alarm_id'],{'actor':actor})
    def maintenance(self, service, start, end, actor):
        start,end=timestamp(start),timestamp(end)
        if end<=start or end-start>86400:raise ValueError('Maintenance must be bounded to 24h')
        value={'id':str(uuid.uuid4()),'service':identity(service),'start':start,'end':end,'actor':identity(actor)}
        with self.store.transaction() as db:
            self.store.put(db,'maintenance',value['id'],value)
            self.store.record(db,self.clock(),'maintenance_created',value['id'],value)
        return value
    def decide(self, action_id, decision, actor):
        if decision not in ('APPROVED','REJECTED','CANCELLED'):raise ValueError('Invalid operator decision')
        with self.store.transaction() as db:
            action=self.store.get(db,'action',action_id)
            if not action or action['status'] not in ('PROPOSED','APPROVED'):raise ValueError('Action is no longer pending')
            if action['status']==decision:return action
            if decision=='APPROVED' and action['class']!='C':raise ValueError('No disruptive approval applicable')
            action['status']=decision
            incident=self.store.get(db,'incident',action['incident_id'])
            incident['operator_decisions'].append({'at':self.clock(),'actor':identity(actor),'decision':decision})
            if decision in ('REJECTED','CANCELLED') and incident['state']=='APPROVAL_PENDING':
                self.transition(db,incident,'ESCALATED',self.clock())
            self.store.put(db,'incident',incident['incident_id'],incident)
            self.store.put(db,'action',action_id,action)
            self.store.record(db,self.clock(),'operator_decision',action_id,{'actor':identity(actor),'decision':decision})
            return action
    def validate_action(self, action_id, context):
        with self.store.transaction() as db:
            action=self.store.get(db,'action',action_id)
            if not action:raise ValueError('Unknown action')
            governance=self.store.get(db,'control','governance',{'mode':'OBSERVE_ONLY','emergency_stop':False})
            reasons=SafetyValidator().validate(action,context,**governance)
            self.store.record(db,self.clock(),'action_blocked',action_id,{'reasons':reasons})
            return reasons
