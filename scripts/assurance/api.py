"""Opt-in authenticated operational writes; reads only the durable observation cache."""
import hmac
import os
from pathlib import Path
import time
from flask import Blueprint, request, jsonify
from .store import Store, JournalBusy
from .engine import Engine
from .catalog import load_catalog
from .model import PROTECTED_NS
from .simulation import run_scenario, SCENARIOS


def register(app, cfg_loader):
    bp=Blueprint('assurance',__name__)
    def engine():
        return Engine(Store(Path(cfg_loader()['RUNTIME_DIR'])/'assurance.sqlite'),load_catalog(),time.time)
    def authorized(name):
        configured=os.environ.get(name,'')
        supplied=request.headers.get('Authorization','')
        return len(configured)>=32 and hmac.compare_digest(supplied,'Bearer '+configured)
    @bp.errorhandler(JournalBusy)
    def journal_busy(error):return jsonify({'error':str(error),'monitoring_confidence':'UNAVAILABLE','live_auto_enabled':False,'production_executor':'DISABLED'}),503
    @bp.errorhandler(ValueError)
    def invalid(error):return jsonify({'error':str(error)}),400
    @bp.get('/api/assurance')
    def snapshot():
        e=engine(); out=e.store.snapshot()
        from .governance import inspect_lock
        out['lifecycle_lock']=inspect_lock(Path(cfg_loader()['RUNTIME_DIR'])/'.lifecycle.lock')
        now=time.time()
        age=now-out['telemetry'].get('observed_at',0)
        out['monitoring_confidence']='UNAVAILABLE' if not out['telemetry'] else 'STALE' if age>120 else 'LIVE'
        if out['monitoring_confidence']=='LIVE' and any(out['telemetry'].get(k,{}).get('provenance')!='LIVE' for k in ('kubernetes','prometheus')):
            out['monitoring_confidence']='UNAVAILABLE'
        out['monitoring_status']=out['telemetry'].get('monitoring_status','UNAVAILABLE')
        for alarm in out['alarm']:
            alarm['stale']=now-alarm['last_seen']>e.catalog[alarm['alarm_type']]['max_age']
        out.update(live_auto_enabled=False,production_executor='DISABLED',protected_ns=sorted(PROTECTED_NS),
            emergency_3gpp='UNAVAILABLE: not end-to-end validated',
            simulations=[s[0] for s in SCENARIOS],approval_enabled=bool(len(os.environ.get('L3_OPERATOR_TOKEN',''))>=32))
        return jsonify(out)
    @bp.post('/api/assurance/webhook')
    def webhook():
        if not authorized('L3_WEBHOOK_TOKEN'):return jsonify({'error':'Webhook authentication not configured or denied'}),403
        if request.content_length is None or request.content_length>131072:return jsonify({'error':'Bounded body required'}),413
        payload=request.get_json(silent=True)
        if not isinstance(payload,dict) or not isinstance(payload.get('alerts'),list) or len(payload['alerts'])>64:
            raise ValueError('Expected at most 64 alerts')
        # Never trust a synthetic event or simulation label on a production route.
        for alarm in payload['alerts']:
            if not isinstance(alarm,dict):raise ValueError('Alarm must be an object')
            labels=alarm.get('labels',{})
            if not isinstance(labels,dict):raise ValueError('Labels must be an object')
            if alarm.get('provenance','LIVE')!='LIVE' or str(labels.get('simulation','false')).lower() not in ('false','0'):
                raise ValueError('Synthetic events are forbidden on production webhook')
        e=engine()
        # Prevalidate the entire batch before mutation.
        from .model import normalize
        for alarm in payload['alerts']:
            normalized=normalize(alarm,time.time())
            if normalized['alarm_type'] not in e.catalog:raise ValueError('Alarm absent from reviewed catalog')
        keys=[]
        for alarm in payload['alerts']:
            # Receipt freshness differs from condition onset. A native Alertmanager
            # notification supplies onset but no latest metric sample. Treat it as
            # an upstream alarm assertion; never as independent remediation evidence.
            if 'timestamp' not in alarm:
                from .model import timestamp
                onset=timestamp(alarm.get('startsAt',time.time()))
                alarm={**alarm,'timestamp':onset,'source':'alertmanager'}
                # A delivery does not prove a fresh source sample. Native repeats deduplicate.
                # Read-only collectors provide explicit observation timestamps.
                result=e.ingest(alarm)
                with e.store.transaction() as db:
                    current=e.store.get(db,'alarm',result['key'])
                    current['upstream_onset']=onset
                    e.store.put(db,'alarm',result['key'],current)
                keys.append(result['key'])
            else:
                keys.append(e.ingest(alarm)['key'])
        return jsonify({'accepted':keys,'execution':'DISABLED'})
    @bp.post('/api/assurance/operator')
    def operator():
        if not authorized('L3_OPERATOR_TOKEN'):return jsonify({'error':'Operator authentication not configured or denied'}),403
        if request.content_length is None or request.content_length>8192:return jsonify({'error':'Bounded body required'}),413
        body=request.get_json(silent=True)
        if not isinstance(body,dict):raise ValueError('Expected command object')
        e=engine(); command=body.get('command')
        if command=='control':result=e.control(body.get('mode'),body.get('emergency_stop'))
        elif command=='acknowledge':e.acknowledge(body.get('key'),'authenticated_operator');result={'acknowledged':True}
        elif command=='decision':result=e.decide(body.get('action_id'),body.get('decision'),'authenticated_operator')
        elif command=='maintenance':result=e.maintenance(body.get('service'),body.get('start'),body.get('end'),'authenticated_operator')
        else:raise ValueError('Unknown operator command')
        return jsonify({'result':result,'production_execution':'DISABLED'})
    @bp.post('/api/assurance/simulate')
    def simulate():
        if not authorized('L3_OPERATOR_TOKEN'):return jsonify({'error':'Operator authentication required'}),403
        body=request.get_json(silent=True)
        if not isinstance(body,dict):raise ValueError('Expected simulation scenario')
        return jsonify(run_scenario(body.get('scenario')))
    app.register_blueprint(bp)
