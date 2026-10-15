"""Durable execution reservations for sandbox validation and future reviewed integration."""
from .model import timestamp

class ActionJournal:
    def __init__(self, store):self.store=store
    def claim_simulated(self, action, now):
        now=timestamp(now)
        if action.get('provenance')!='SIMULATED':
            raise PermissionError('Journal execution reservations are simulation-only')
        scope=action['service']+':'+action['action']
        with self.store.transaction() as db:
            previous=self.store.get(db,'execution',action['action_id'])
            if previous:return previous,False  # Ambiguous or completed outcomes are never blindly retried.
            cooldown=self.store.get(db,'cooldown',scope,{'until':0})
            if now<cooldown['until']:raise PermissionError('Durable cooldown is active')
            entry={'action_id':action['action_id'],'service':action['service'],'action':action['action'],
                'provenance':'SIMULATED','status':'EXECUTING','attempts':1,'at':now}
            self.store.put(db,'execution',action['action_id'],entry)
            self.store.put(db,'cooldown',scope,{'until':now+action['cooldown']})
            self.store.record(db,now,'simulated_execution_reserved',action['action_id'],entry)
            return entry,True
    def finish_simulated(self, action_id, outcome, now):
        if outcome not in ('VERIFIED','FAILED','TIMEOUT_AMBIGUOUS'):raise ValueError('Invalid outcome')
        with self.store.transaction() as db:
            entry=self.store.get(db,'execution',action_id)
            if not entry or entry['provenance']!='SIMULATED':raise PermissionError('No sandbox reservation')
            if entry['status']!='EXECUTING':return entry
            entry.update(status=outcome,finished_at=timestamp(now))
            self.store.put(db,'execution',action_id,entry)
            self.store.record(db,now,'simulated_execution_outcome',action_id,{'outcome':outcome,'provenance':'SIMULATED'})
            return entry
