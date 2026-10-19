"""Tenant-scoped offline CI/CD coordinator. No production mutation path exists."""
import hashlib
import html
import json
from pathlib import Path
import time
import uuid
import yaml
from assurance.locking import exclusive
from assurance.model import PROTECTED_NS
from osm_catalog import Catalog
from osm_onboard import onboard
from .contract import canonical, identity, validate, confined
from .package import prepare, source_evidence
from .security import Denied
from .store import Journal

PROTOCOLS = {'F1', 'NGAP', 'registration', 'pdu_session', 'user_plane'}


class SimulatedCatalog(Catalog):
    """Reuse real catalog upload/verify behavior, replace transport with memory only."""
    def __init__(self):
        super().__init__('https://simulation.invalid')
        self.entries = {}; self.pending = None; self.requests = []

    def lookup(self, kind, name, required=True):
        entry = self.entries.get((kind, name))
        if required and entry is None:
            raise ValueError('Simulated catalog object missing')
        return entry['id'] if entry else None

    def upload(self, kind, name, content):
        existing = self.entries.get((kind, name))
        if existing and existing['content'] != content:
            raise ValueError('Referenced catalog content is immutable')
        self.pending = (kind, name)
        super().upload(kind, name, content)

    def request(self, method, path, data=None, content_type='application/yaml'):
        self.requests.append({'method':method, 'path':path, 'provenance':'SIMULATED'})
        if method == 'GET':
            for (kind, _), entry in self.entries.items():
                if path == self.content_path(kind, entry['id']):
                    return entry['content']
        elif method == 'POST' and path in ('/vnfpkgm/v1/vnf_packages_content', '/nsd/v1/ns_descriptors_content'):
            self.entries[self.pending] = {'id':str(uuid.uuid5(uuid.NAMESPACE_URL, '/'.join(self.pending))), 'content':data}
            return b'{}'
        elif method == 'PUT':
            # Same-byte idempotence only. This simulator never replaces bytes.
            for (kind, _), entry in self.entries.items():
                if path == self.content_path(kind, entry['id']) and data == entry['content']:
                    return b'{}'
        raise PermissionError('Simulator transport rejects unexpected request')


class Service:
    def __init__(self, runtime, workspace, *, helm='helm', clock=time.time):
        self.journal = Journal(runtime); self.workspace = Path(workspace).resolve()
        self.helm = helm; self.clock = clock

    def owned(self, principal, kind, key, db=None):
        item = self.journal.get(principal, kind, key, db)
        if item.get('owner') not in (None, principal.subject) and 'operator' not in principal.roles and 'reviewer' not in principal.roles:
            raise Denied('Workload owner authorization required')
        return item

    def register(self, principal, manifest, event_id):
        principal.require('researcher')
        manifest = validate(manifest)
        evidence = source_evidence(manifest['source'], self.workspace)
        key = identity({'tenant':principal.tenant, 'manifest':manifest})
        def write(db):
            value = {'id':key, 'schema_version':1, 'owner':principal.subject, 'tenant':principal.tenant,
                     'manifest':manifest, 'declaration_authority':'PROVIDER_DECLARED',
                     'capability_validation':'STATIC_VALIDATED', 'platform_protocol_verification':'NOT_RUN',
                     'source_evidence':evidence, 'production_eligible':False}
            return self.journal.put(db, principal, 'workloads', key, value, immutable=True)
        return self.journal.event(principal, event_id, {'action':'register','manifest':manifest}, write)

    def release(self, principal, workload_id, chart, event_id):
        principal.require('builder')
        workload = self.owned(principal, 'workloads', workload_id)
        package = prepare(self.workspace, chart, workload['manifest'], self.helm)
        key = identity({'schema_version':1,'tenant':principal.tenant,'workload':workload_id,
                        'packages':package['package_digests'],'chart':package['chart_digest']})
        def write(db):
            try:
                return self.journal.get(principal, 'releases', key, db)
            except Denied:
                pass
            value = {'id':key, 'schema_version':1, 'pipeline_version':1, 'tenant':principal.tenant, 'owner':principal.subject,
                     'workload_id':workload_id, 'image':workload['manifest']['image'],
                     'manifest_digest':'sha256:'+identity(workload['manifest']),
                     'source':workload['source_evidence'], 'package':package,
                     'state':'PREPARED', 'provenance':'SIMULATED', 'production_eligible':False,
                     'history':[{'state':'PREPARED','at':self.clock(),'authority':'PLATFORM_OFFLINE_VERIFIED'}]}
            return self.journal.put(db, principal, 'releases', key, value, immutable=True)
        return self.journal.event(principal, event_id, {'action':'release','workload':workload_id,'packages':package['package_digests']}, write)

    def approve(self, principal, release_id, action, target, event_id, ttl=300):
        principal.require('reviewer')
        release = self.journal.get(principal, 'releases', release_id)
        if principal.subject == release['owner']:
            raise Denied('Independent reviewer required')
        if action not in ('catalog', 'update') or type(ttl) is not int or not 1 <= ttl <= 300:
            raise ValueError('Bounded explicit operation approval required')
        if action == 'catalog':
            if target != {'project':principal.tenant, 'mode':'SIMULATED'}:
                raise Denied('Catalog tenant/mode mismatch')
        else:
            if not isinstance(target, dict) or set(target) != {'ns_id','cluster_uid','namespace_uid','project_id','tenant','mode'} or target['tenant'] != principal.tenant or target['mode'] != 'SIMULATED':
                raise Denied('Target tenant/mode mismatch')
            for field in ('ns_id','cluster_uid','namespace_uid','project_id'):
                if str(uuid.UUID(target[field])) != target[field]:
                    raise ValueError('Exact UUID target required')
            if target['ns_id'] in PROTECTED_NS:
                raise Denied('Protected Network Service')
        payload = {'release':release_id,'action':action,'target':target,'packages':release['package']['package_digests']}
        key = identity({'intent':payload,'reviewer':principal.subject,'event':event_id})
        def write(db):
            approval = {'id':key,'intent':payload,'plan_digest':identity(payload),'approved_by':principal.subject,
                        'expires_at':self.clock()+ttl,'execution_scope':'SIMULATED_ONLY','ownership_verified_live':False}
            return self.journal.put(db,principal,'approvals',key,approval,immutable=True)
        return self.journal.event(principal,event_id,{'action':'approve','intent':payload,'ttl':ttl},write)

    def _approval(self, principal, release, approval_id, action, db):
        approval = self.journal.get(principal, 'approvals', approval_id, db)
        intent = approval['intent']
        if approval['expires_at'] < self.clock() or intent['release'] != release['id'] or intent['action'] != action or intent['packages'] != release['package']['package_digests'] or approval['plan_digest'] != identity(intent):
            raise Denied('Expired or mismatched approval')
        if intent['target'].get('ns_id') in PROTECTED_NS:
            raise Denied('Protected Network Service')
        try:
            self.journal.get(principal,'used-approvals',approval_id,db)
        except Denied:
            pass
        else:
            raise Denied('Approval already consumed; authorize a new operation')
        self.journal.put(db,principal,'used-approvals',approval_id,{'release':release['id'],'action':action})
        return approval

    def catalog(self, principal, release_id, approval_id, event_id):
        principal.require('publisher')
        with exclusive(self.journal.directory/'.lifecycle.lock'):
            def write(db):
                release = self.owned(principal,'releases',release_id,db)
                approval = self._approval(principal,release,approval_id,'catalog',db)
                if release['state'] != 'PREPARED':
                    raise ValueError('Catalog operation conflicts with release state')
                package = release['package']; artifacts = {k:bytes.fromhex(v) for k,v in package['artifacts'].items()}
                for name,data in artifacts.items():
                    if 'sha256:'+hashlib.sha256(data).hexdigest() != package['package_digests'][name]:
                        raise ValueError('Package bytes no longer match sealed release')
                simulator = SimulatedCatalog()
                onboard(simulator,[package['scenario']],[artifacts])
                identities = simulator.verify(package['scenario'],artifacts)
                release['catalog'] = {'identities':identities,'requests':simulator.requests,'provenance':'SIMULATED','approval':approval['id']}
                release['state'] = 'SIMULATED_CATALOG_SYNCED'
                release['history'].append({'state':release['state'],'at':self.clock(),'provenance':'SIMULATED'})
                return self.journal.put(db,principal,'releases',release_id,release)
            return self.journal.event(principal,event_id,{'action':'catalog','release':release_id,'approval':approval_id},write)

    def update(self, principal, release_id, approval_id, event_id, outcome='SUCCESS'):
        principal.require('operator')
        if outcome not in ('SUCCESS','FAIL_BEFORE_SUBMIT','UNKNOWN_AFTER_SUBMIT'):
            raise ValueError('Unknown simulated fault')
        with exclusive(self.journal.directory/'.lifecycle.lock'):
            def write(db):
                release = self.owned(principal,'releases',release_id,db)
                approval = self._approval(principal,release,approval_id,'update',db)
                target = approval['intent']['target']
                # Pending operations survive process/lock release and block later jobs.
                try:
                    pending = self.journal.get(principal,'pending',target['ns_id'],db)
                except Denied:
                    pending = None
                if pending:
                    raise ValueError('Pending lifecycle outcome requires reconciliation')
                if release['state'] != 'SIMULATED_CATALOG_SYNCED':
                    raise ValueError('Update conflicts with release state')
                state = {'SUCCESS':'SIMULATED_UPDATED','FAIL_BEFORE_SUBMIT':'SIMULATED_FAILED',
                         'UNKNOWN_AFTER_SUBMIT':'UNKNOWN_OUTCOME'}[outcome]
                operation = {'id':identity({'event':event_id,'target':target,'release':release_id}),
                             'target':target,'approval':approval_id,'outcome':state,'provenance':'SIMULATED',
                             'attempts':1,'exit_status':0 if outcome=='SUCCESS' else 1,'production_calls':0}
                release.update(state=state,operation=operation)
                release['history'].append({'state':state,'at':self.clock(),'provenance':'SIMULATED'})
                if outcome == 'UNKNOWN_AFTER_SUBMIT':
                    self.journal.put(db,principal,'pending',target['ns_id'],{'operation':operation['id'],'release':release_id})
                return self.journal.put(db,principal,'releases',release_id,release)
            return self.journal.event(principal,event_id,{'action':'update','release':release_id,'approval':approval_id,'fault':outcome},write)

    def reconcile(self, principal, release_id, operation_id, outcome, event_id):
        principal.require('operator'); principal.require('verifier')
        if outcome not in ('NO_EFFECT','COMPLETED'):
            raise ValueError('Explicit simulated reconciliation evidence required')
        with exclusive(self.journal.directory/'.lifecycle.lock'):
            def write(db):
                release=self.owned(principal,'releases',release_id,db)
                operation=release.get('operation',{})
                if operation.get('id')!=operation_id or release['state'] not in ('UNKNOWN_OUTCOME','SIMULATED_FAILED'):
                    raise ValueError('Reconciliation identity/state mismatch')
                ns=operation['target']['ns_id']
                if ns in PROTECTED_NS: raise Denied('Protected Network Service')
                if release['state']=='UNKNOWN_OUTCOME':
                    pending=self.journal.get(principal,'pending',ns,db)
                    if pending!={'operation':operation_id,'release':release_id}:
                        raise ValueError('Pending identity mismatch')
                    db.execute('DELETE FROM objects WHERE tenant=? AND kind=? AND id=?',(principal.tenant,'pending',ns))
                release['state']='SIMULATED_UPDATED' if outcome=='COMPLETED' else 'SIMULATED_CATALOG_SYNCED'
                release['history'].append({'state':release['state'],'at':self.clock(),'provenance':'SIMULATED','reason':'operator reconciliation; no request submitted'})
                return self.journal.put(db,principal,'releases',release_id,release)
            return self.journal.event(principal,event_id,{'action':'reconcile','release':release_id,'operation':operation_id,'outcome':outcome},write)

    def evidence(self, principal, ran_id, core_id, tests, metrics, event_id):
        principal.require('verifier')
        ran = self.journal.get(principal,'releases',ran_id); core = self.journal.get(principal,'releases',core_id)
        if ran.get('schema_version')==2 or core.get('schema_version')==2:
            # This interoperability-evidence subsystem assumes a single
            # release['image']; multi-image releases are not wired into it.
            raise Denied('Multi-image interoperability evidence is not supported')
        rm = self.journal.get(principal,'workloads',ran['workload_id'])['manifest']
        cm = self.journal.get(principal,'workloads',core['workload_id'])['manifest']
        if not set(rm['roles']) & {'gnb','cu','du'} or not set(cm['roles']) & {'amf','smf','upf'}:
            raise ValueError('A RAN/Core role pair is required')
        if rm['plmn'] != cm['plmn'] or rm['tac'] != cm['tac'] or not set(rm['dnns']) & set(cm['dnns']) or not {identity(s) for s in rm['slices']} & {identity(s) for s in cm['slices']}:
            raise ValueError('PLMN/TAC/slice/DNN interoperability conflict')
        for interface in ('N2','N3'):
            if interface in rm['interfaces'] and interface in cm['interfaces'] and any(rm['interfaces'][interface][k] != cm['interfaces'][interface][k] for k in ('transport','port','mtu')):
                raise ValueError('Transport/port/MTU interoperability conflict')
        applicable=PROTOCOLS if 'F1-C' in rm['interfaces'] else PROTOCOLS-{'F1'}
        if not isinstance(tests,dict) or set(tests) != applicable or not isinstance(metrics,dict) or len(metrics)>64:
            raise ValueError('A bounded complete protocol matrix is required')
        allowed_metrics=set(rm['monitoring']['required_metrics']+rm['monitoring']['optional_metrics']+cm['monitoring']['required_metrics']+cm['monitoring']['optional_metrics'])
        if set(metrics)-allowed_metrics: raise ValueError('Metric absent from capability contract')
        now = self.clock()
        for value in list(tests.values())+list(metrics.values()):
            if not isinstance(value,dict) or set(value) != {'outcome','provenance','observed_at','evidence_digest'} or value['provenance'] not in ('SIMULATED','UNAVAILABLE') or value['outcome'] not in ('PASS','FAIL','UNAVAILABLE'):
                raise ValueError('Only explicitly simulated/unavailable sandbox evidence is accepted')
            from .contract import digest
            digest(value['evidence_digest'])
            at=value['observed_at']
            if type(at) not in (int,float) or not __import__('math').isfinite(at) or at>now+1 or at<0:
                raise ValueError('Invalid evidence timestamp')
            if value['provenance']=='UNAVAILABLE' and value['outcome']!='UNAVAILABLE':
                raise ValueError('Unavailable evidence cannot assert health')
        required=set(rm['monitoring']['required_metrics']) | set(cm['monitoring']['required_metrics'])
        missing=sorted(name for name in required if name not in metrics or metrics[name]['outcome']!='PASS' or now-metrics[name]['observed_at']>120)
        stale=any(now-v['observed_at']>120 for v in list(tests.values())+list(metrics.values()))
        passed=all(v['outcome']=='PASS' and v['provenance']=='SIMULATED' for v in tests.values())
        updated=all(r['state']=='SIMULATED_UPDATED' for r in (ran,core)) and bool(set(rm['roles']) & {'cu','gnb'}) and {'amf','smf','upf'} <= set(cm['roles'])
        key=identity({'ran':ran_id,'core':core_id,'tests':tests,'metrics':metrics})
        row={'id':key,'ran_release':ran_id,'core_release':core_id,'ran_image':ran['image'],
             'core_image':core['image'],'ran_version':rm['version'],'core_version':cm['version'],'applicable_protocols':sorted(applicable),'tests':tests,'monitoring':metrics,'missing_metrics':missing,
             'provenance':'STALE' if stale else 'SIMULATED','authority':'SANDBOX_ASSERTION',
             'outcome':'SIMULATION_PASS' if passed and not missing and not stale and updated else 'INCONCLUSIVE_OR_FAILED',
             'production_eligible':False,'recorded_at':now}
        return self.journal.event(principal,event_id,{'action':'evidence','ran':ran_id,'core':core_id,'tests':tests,'metrics':metrics},
            lambda db:self.journal.put(db,principal,'matrix',key,row,immutable=True))

    def correlate(self, principal, release_id, alarm_type, event_id):
        principal.require('verifier')
        payload={'action':'correlate','release':release_id,'alarm':alarm_type}
        with exclusive(self.journal.directory/'.lifecycle.lock'):
            replay=self.journal.replay(principal,event_id,payload)
            if replay is not None: return replay
            release=self.journal.get(principal,'releases',release_id)
            if release['state']!='SIMULATED_UPDATED':
                raise ValueError('A simulated deployment event is required')
            from assurance.store import Store
            from assurance.engine import Engine
            from assurance.catalog import load_catalog
            catalog=load_catalog()
            if alarm_type not in ('DUUnavailable','AMFUnavailable','PrometheusTargetDown'):
                raise ValueError('Select a reviewed sandbox alarm')
            # Dedicated per-tenant simulation journal. Never open the production journal.
            store=Store(self.journal.directory/('simulation-'+identity(principal.tenant)[:16]+'.sqlite'))
            now=[self.clock()]; engine=Engine(store,catalog,lambda:now[0],simulation=True)
            target=release['operation']['target']; event={'alarm_type':alarm_type,'cluster':target['cluster_uid'],
                'namespace':'simulation','service':target['ns_id'],'resource':target['ns_id'],
                'source':'telecom-sandbox','observations':{'pod_ready':False,'f1_association':False,'metric_present':False}}
            # Deterministic synthetic trace; virtual time satisfies policy persistence.
            engine.ingest({**event,'timestamp':now[0]}); now[0]+=catalog[alarm_type]['persistence']
            alarm=engine.ingest({**event,'timestamp':now[0]}); snapshot=store.snapshot()
            from .correlation import correlate_snapshot
            value=correlate_snapshot(release,snapshot)
            return self.journal.event(principal,event_id,{'action':'correlate','release':release_id,'alarm':alarm_type},
                lambda db:self.journal.put(db,principal,'analysis',value['id'],value))

    def report(self, principal, matrix_id, event_id):
        principal.require('verifier')
        matrix=self.journal.get(principal,'matrix',matrix_id)
        releases=[self.journal.get(principal,'releases',matrix[k]) for k in ('ran_release','core_release')]
        from .freshness import current_evidence
        matrix=current_evidence(matrix,self.clock())
        issues=list(matrix['missing_metrics'])
        if matrix['current_freshness']=='STALE': issues.append('Evidence is now stale')
        analyses=[a for a in self.journal.list(principal,'analysis') if a['release_id'] in {r['id'] for r in releases}]
        if analyses: issues.append('Synthetic post-update incident remains unresolved')
        issues.extend(['Live interoperability/rollback/hardware NOT VERIFIED','Source builds and registry provenance NOT VERIFIED','Production execution disabled'])
        value={'schema_version':1,'id':identity({'matrix':matrix_id,'analyses':analyses,'event':event_id}),'tenant':principal.tenant,
               'release_ids':[r['id'] for r in releases],
               'releases':[{k:r[k] for k in ('id','workload_id','source','image','state','history','manifest_digest')} for r in releases],
               'package_digests':{r['id']:r['package']['package_digests'] for r in releases},
               'matrix':matrix,'monitoring_observations':matrix['monitoring'],'failure_analysis':analyses,
               'unresolved_issues':issues,'qualification_outcome':'SIMULATED_WITH_ISSUES' if analyses or matrix['outcome']!='SIMULATION_PASS' or matrix['current_freshness']=='STALE' else 'SIMULATION_ONLY',
               'production_qualified':False,'remediation_execution':'DISABLED'}
        return self.journal.event(principal,event_id,{'action':'report','matrix':matrix_id,'analyses':analyses},
            lambda db:self.journal.put(db,principal,'reports',value['id'],value,immutable=True))


def report_html(value):
    return ('<!doctype html><html><head><meta charset="utf-8"><title>Telecom Qualification Report</title></head>'
            '<body><h1>Internal Telecom Qualification Report</h1><strong>SIMULATED — NOT PRODUCTION QUALIFIED</strong>'
            '<pre>'+html.escape(json.dumps(value,indent=2))+'</pre></body></html>')
