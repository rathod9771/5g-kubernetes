"""Authenticated sandbox endpoints; lazy, no runtime state touched on registration."""
import os
import sqlite3
import threading

_REQUESTS=threading.BoundedSemaphore(4)
from flask import Blueprint, request, jsonify, Response, g
from .security import authenticate, Denied
from .service import Service, report_html


def register(app):
    bp=Blueprint('telecom',__name__,url_prefix='/api/telecom')

    @bp.before_request
    def boundary():
        try:
            if request.path.startswith('/api/telecom/github-webhooks/'):
                from .execution import Policy, verify_webhook
                from .security import Principal
                policy=Policy.environment();key=request.view_args['workload_id']
                binding=policy.value.get('webhooks',{}).get(key,{})
                if request.content_length is None or request.content_length>65536 or request.headers.get('X-GitHub-Event')!='push':
                    raise Denied('Bounded push required')
                verify_webhook(request.get_data(),request.headers.get('X-Hub-Signature-256'),policy.secret(binding['secret_ref'],binding['tenant'],'github-webhook',key))
                g.telecom_principal=Principal(binding['subject'],binding['tenant'],frozenset({'builder'}))
            else:
                g.telecom_principal=authenticate(request.headers.get('Authorization',''))
        except (Denied,OSError,ValueError,TypeError,KeyError):
            return jsonify(error='Authentication/authorization required'),403
        if not _REQUESTS.acquire(blocking=False): return jsonify(error='Sandbox capacity busy'),503
        g.telecom_slot=True
        if request.method not in ('GET','HEAD'):
            if request.content_length is None or request.content_length>65536:
                return jsonify(error='Bounded JSON body required'),413
            if request.mimetype!='application/json':
                return jsonify(error='JSON required'),415

    @bp.teardown_request
    def release_capacity(error):
        if getattr(g,'telecom_slot',False):
            g.telecom_slot=False;_REQUESTS.release()

    def service():
        runtime=os.environ.get('TELECOM_RUNTIME_DIR');workspace=os.environ.get('TELECOM_WORKSPACE')
        if not runtime or not workspace:
            raise RuntimeError('Sandbox runtime configuration unavailable')
        return Service(runtime,workspace,helm=os.environ.get('HELM_BIN','helm'))

    def body(*allowed):
        value=request.get_json(silent=True)
        if not isinstance(value,dict) or set(value)-set(allowed):raise ValueError('Strict bounded JSON object required')
        return value

    def failure(error):
        # Do not serialize request fields, file paths or arbitrary exception messages.
        status=403 if isinstance(error,Denied) else 409
        return jsonify(error='Sandbox operation rejected',error_class=type(error).__name__,
                       production_execution='DISABLED'),status
    for exception in (Denied,ValueError,KeyError,TypeError,OSError,RuntimeError,sqlite3.Error):
        bp.register_error_handler(exception,failure)

    @bp.get('/<kind>')
    def listing(kind):
        mapping={'workloads':'workloads','capabilities':'workloads','releases':'releases',
                 'interoperability':'matrix','failure-analysis':'analysis','qualification-reports':'reports','pipeline-jobs':'jobs','publication-requests':'publication-requests','registry-publications':'registry-publications','deployments':'deployments'}
        if kind not in mapping:return jsonify(error='Unknown view'),404
        values=service().journal.list(g.telecom_principal,mapping[kind])
        from .freshness import current_evidence
        if kind=='deployments':
            for v in values:
                obs=v.get('observation',{});at=obs.get('at',0)
                v['current_freshness']='STALE' if obs.get('provenance')=='LIVE' and __import__('time').time()-at>120 else obs.get('provenance','UNAVAILABLE')
        if kind=='interoperability': values=[current_evidence(v,__import__('time').time()) for v in values]
        for value in values:
            if 'package' in value:value['package'].pop('artifacts',None)
        return jsonify(schema_version=1,items=values,mode='EXPLICIT_EXECUTION; SIMULATIONS_LABELED_SEPARATELY',production_execution='DISABLED')

    @bp.post('/workloads')
    def workloads():
        b=body('manifest','event_id');return jsonify(service().register(g.telecom_principal,b['manifest'],b['event_id']))

    @bp.post('/releases')
    def releases():
        b=body('workload_id','chart','event_id');value=service().release(g.telecom_principal,b['workload_id'],b['chart'],b['event_id'])
        value['package'].pop('artifacts',None);return jsonify(value)

    @bp.post('/approvals')
    def approvals():
        b=body('release_id','action','target','event_id','ttl');return jsonify(service().approve(g.telecom_principal,b['release_id'],b['action'],b['target'],b['event_id'],b.get('ttl',300)))

    @bp.post('/catalog-simulations')
    def catalog():
        b=body('release_id','approval_id','event_id');value=service().catalog(g.telecom_principal,b['release_id'],b['approval_id'],b['event_id'])
        value['package'].pop('artifacts',None);return jsonify(value)

    @bp.post('/update-simulations')
    def update():
        b=body('release_id','approval_id','event_id','outcome');value=service().update(g.telecom_principal,b['release_id'],b['approval_id'],b['event_id'],b.get('outcome','SUCCESS'))
        value['package'].pop('artifacts',None);return jsonify(value)

    @bp.post('/reconciliations')
    def reconcile():
        b=body('release_id','operation_id','outcome','event_id')
        value=service().reconcile(g.telecom_principal,b['release_id'],b['operation_id'],b['outcome'],b['event_id'])
        value['package'].pop('artifacts',None);return jsonify(value)

    @bp.post('/interoperability')
    def interoperability():
        b=body('ran_release','core_release','tests','metrics','event_id');return jsonify(service().evidence(g.telecom_principal,b['ran_release'],b['core_release'],b['tests'],b['metrics'],b['event_id']))

    @bp.post('/failure-analysis')
    def analysis():
        b=body('release_id','alarm_type','event_id');return jsonify(service().correlate(g.telecom_principal,b['release_id'],b['alarm_type'],b['event_id']))

    @bp.post('/qualification-reports')
    def report():
        b=body('matrix_id','event_id');return jsonify(service().report(g.telecom_principal,b['matrix_id'],b['event_id']))

    @bp.get('/qualification-reports/<key>.html')
    def html_report(key):
        value=service().journal.get(g.telecom_principal,'reports',key)
        response=Response(report_html(value),mimetype='text/html')
        response.headers['Content-Security-Policy']="default-src 'none'; frame-ancestors 'none'"
        response.headers['X-Content-Type-Options']='nosniff'
        return response

    def pipeline():
        from .execution import Policy
        from .pipeline import Pipeline
        return Pipeline(service(),Policy.environment())

    @bp.post('/pipeline-jobs')
    def jobs():
        b=body('workload_id','chart','event_id','commit','image_mode','secret_ref')
        return jsonify(pipeline().queue(g.telecom_principal,b['workload_id'],b['chart'],b['event_id'],b.get('commit'),b.get('image_mode','prebuilt'),b.get('secret_ref')))

    @bp.post('/pipeline-runs')
    def run_job():
        b=body('job_id');return jsonify(pipeline().run(g.telecom_principal,b['job_id']))

    @bp.post('/pipeline-cancellations')
    def cancel_job():
        b=body('job_id','event_id');return jsonify(pipeline().cancel(g.telecom_principal,b['job_id'],b['event_id']))

    @bp.post('/github-webhooks/<workload_id>')
    def github_hook(workload_id):
        p=pipeline();binding=p.policy.value['webhooks'][workload_id]
        return jsonify(p.webhook(g.telecom_principal,workload_id,request.get_data(),request.headers.get('X-Hub-Signature-256'),request.headers.get('X-GitHub-Delivery',''),binding['chart']))

    @bp.post('/publication-requests')
    def publication_request():
        b=body('release_id','action','event_id');return jsonify(pipeline().request(g.telecom_principal,b['release_id'],b['action'],b['event_id']))

    @bp.post('/publication-approvals')
    def publication_approval():
        b=body('request_id','event_id');return jsonify(pipeline().approve(g.telecom_principal,b['request_id'],b['event_id']))

    @bp.post('/catalog-publications')
    def catalog_publication():
        b=body('request_id','approval_id');return jsonify(pipeline().publish(g.telecom_principal,b['request_id'],b['approval_id']))

    def initial():
        from .initial import InitialDeployment
        from .execution import Policy
        return InitialDeployment(service(),Policy.environment())

    @bp.post('/deployment-requests')
    def deployment_request():
        b=body('publication_id','event_id')
        return jsonify(initial().request(g.telecom_principal,b['publication_id'],b['event_id']))

    @bp.post('/deployment-approvals')
    def deployment_approval():
        b=body('request_id','event_id')
        return jsonify(initial().approve(g.telecom_principal,b['request_id'],b['event_id']))

    @bp.post('/initial-deployments')
    def initial_deployment():
        b=body('request_id','approval_id')
        return jsonify(initial().submit(g.telecom_principal,b['request_id'],b['approval_id']))

    @bp.post('/deployment-observations')
    def deployment_observation():
        b=body('request_id','event_id')
        return jsonify(initial().refresh(g.telecom_principal,b['request_id'],b['event_id']))

    @bp.after_request
    def privacy(response):
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        return response
    app.register_blueprint(bp)
