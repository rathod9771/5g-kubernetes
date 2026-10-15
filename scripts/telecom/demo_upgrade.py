"""Reproducible offline end-to-end P9 demonstration: onboard, then update/rollback.

All remote transport (registry, OSM NBI, Kubernetes) is an explicit fixture.
Real Helm lint/render, real package/catalog-identity computation and the real
InitialDeployment/InstanceUpdate adapter code execute throughout; only the I/O
at their edges is replaced. No BuildKit, push, real OSM NS or live workload is
used. `upgrade_rollback_certified` is set only in this private fixture policy;
no shipped configuration ever sets it, so live update/rollback stays blocked
everywhere else. Every SIMULATED stage below is labeled as such.
"""
import argparse
import io
import json
from pathlib import Path
import shutil
from unittest.mock import patch, Mock
from .demo_execution import image_fixture, ROOT
from .contract import canonical
from .execution import Policy
from .service import Service, SimulatedCatalog
from .pipeline import Pipeline
from .initial import InitialDeployment
from .upgrade import InstanceUpdate
from .security import Principal

NS = '11111111-2222-4333-8444-555555555555'
OP = '22222222-2222-4333-8444-555555555555'
OP2 = '22222222-2222-4333-8444-666666666666'


def run(output, helm):
    output = Path(output); output.mkdir(mode=0o700, parents=True, exist_ok=False)
    workspace = output/'workspace'; workspace.mkdir(mode=0o700)
    shutil.copytree(ROOT/'examples/telecom/disposable/chart', workspace/'chart')
    _, digest, blobs, raw = image_fixture()
    manifest = json.loads((ROOT/'examples/telecom/disposable/manifest.json').read_text())
    repo = 'registry.example/offline/disposable'
    manifest['source'].update(repository=repo, digest=digest); manifest['image'].update(repository=repo, digest=digest)
    (workspace/'chart/values.yaml').write_text('image: '+repo+'@'+digest+'\n')
    user = Principal('researcher', 'demo', frozenset({'researcher', 'builder', 'publisher', 'operator'}))
    reviewer = Principal('independent-reviewer', 'demo', frozenset({'reviewer'}))
    target = {'scope': 'DISPOSABLE_RESEARCH_NAMESPACE', 'tenant': 'demo', 'enabled': True,
              'namespace': 'research-demo-offline', 'namespace_uid': NS, 'cluster_uid': OP,
              'vim_id': NS, 'kubeconfig': 'UNUSED_OFFLINE_FIXTURE',
              # Fixture-only: no shipped environment ever sets this. Live execution
              # requires a separately approved disposable OSM v19 certification.
              'upgrade_rollback_certified': True}
    policy = Policy({'local_roots': {'demo': '.'}, 'oci_repositories': [repo],
        'catalog_targets': {'demo': {'scope': 'DISPOSABLE_RESEARCH_PROJECT', 'enabled': True,
            'host': 'https://osm.example', 'project': 'research-demo'}},
        'deployment_targets': {'demo': target}})
    service = Service(output/'runtime', workspace, helm=helm); pipeline = Pipeline(service, policy)
    workload = service.register(user, manifest, 'register')

    def registry(request, **kwargs):
        if not request.full_url.startswith('https://registry.example/v2/offline/disposable/'):
            raise AssertionError('Unexpected network destination')
        key = request.full_url.rsplit('/', 1)[-1]
        return io.BytesIO(raw if '/manifests/' in request.full_url else blobs['blobs/sha256/'+key[7:]])

    def run_job(event, chart_path='chart'):
        job = pipeline.queue(user, workload['id'], chart_path, event, image_mode='prebuilt')
        with patch('urllib.request.OpenerDirector.open', side_effect=registry), \
             patch('telecom.pipeline.build', side_effect=AssertionError('No BuildKit needed')):
            job = pipeline.run(user, job['id'])
        if job['state'] != 'COMPLETED':
            raise RuntimeError('Offline package pipeline failed: '+json.dumps(job))
        return service.journal.get(user, 'releases', job['release_id'])

    def publish(release, event):
        scenario = release['package']['scenario']
        class OfflineCatalog(SimulatedCatalog):
            def request(self, method, path, data=None, content_type='application/yaml'):
                if method == 'POST':
                    self.pending = ('knf', scenario['knf_package']) if 'vnfpkgm' in path else ('ns', scenario['nsd_package'])
                return super().request(method, path, data, content_type)
        item = pipeline.request(user, release['id'], 'catalog', event+'-request')
        approval = pipeline.approve(reviewer, item['id'], event+'-approve')
        publication = pipeline.publish(user, item['id'], approval['id'], OfflineCatalog)
        if publication['state'] != 'PUBLISHED_CONTENT_VERIFIED':
            raise RuntimeError('Fixture catalog failure: '+json.dumps(publication))
        return publication

    release_v1 = run_job('queue-v1'); publication_v1 = publish(release_v1, 'catalog-v1')

    adapter = InitialDeployment(service, policy)
    request = adapter.request(user, publication_v1['id'], 'initial-request')
    approval = adapter.approve(reviewer, request['id'], 'initial-approve')
    client = Mock(); client.verify.return_value = publication_v1['diagnostic']['catalog_ids']
    calls = []

    def initial_nbi(method, path, data=None, *args):
        calls.append({'method': method, 'path': path})
        if method == 'POST' and path.endswith('ns_instances_content'):
            return canonical({'id': NS, 'nslcmop_id': OP})
        if path.endswith('ns_lcm_op_occs'):
            return b'[]'
        if '/ns_instances/' in path and not path.endswith('/update'):
            return canonical({'_id': NS, 'name': request['ns_name'], 'nsd-id': publication_v1['diagnostic']['catalog_ids']['ns'],
                               'nsState': 'READY', 'operational-status': 'running', 'config-status': 'configured'})
        return canonical({'nsInstanceId': NS, 'lcmOperationType': 'INSTANTIATE', 'operationState': 'COMPLETED'})
    client.request.side_effect = initial_nbi

    def pod(image_ref):
        return [{'metadata': {'namespace': target['namespace']},
                  'spec': {'containers': [{'image': image_ref}]},
                  'status': {'containerStatuses': [{'ready': True, 'imageID': 'x@'+image_ref.rsplit('@', 1)[-1]}]}}]

    with patch('telecom.initial.scoped_catalog', return_value=client), patch.object(adapter, 'snapshot', return_value=[]) as reader:
        submitted = adapter.submit(user, request['id'], approval['id'])
        reader.return_value = pod(repo+'@'+digest)
        observed = adapter.refresh(user, request['id'], 'observe-initial')

    # New source revision: same verified image, bumped chart content → new package identity.
    chart_v2 = output/'chart-v2'; shutil.copytree(workspace/'chart', chart_v2)
    (chart_v2/'Chart.yaml').write_text((chart_v2/'Chart.yaml').read_text().replace('version: 0.1.0', 'version: 0.2.0'))
    shutil.copytree(chart_v2, workspace/'chart-v2')
    release_v2 = run_job('queue-v2', 'chart-v2'); publication_v2 = publish(release_v2, 'catalog-v2')

    update_adapter = InstanceUpdate(service, policy)
    update_request = update_adapter.request(user, request['id'], publication_v2['id'], 'update', 'update-request')
    update_approval = update_adapter.approve(reviewer, update_request['id'], 'update-approve')

    def update_nbi(method, path, data=None, *args):
        calls.append({'method': method, 'path': path})
        if path.endswith('/update'):
            return canonical({'nslcmop_id': OP2})
        if path.endswith('ns_lcm_op_occs'):
            return b'[{"operationState":"COMPLETED"}]'
        return canonical({'nsInstanceId': NS, 'operationState': 'COMPLETED'})
    client.verify.side_effect = lambda scenario, artifacts: (publication_v1['diagnostic']['catalog_ids'] if scenario['key'] == release_v1['package']['scenario']['key'] else publication_v2['diagnostic']['catalog_ids'])
    client.request.side_effect = update_nbi
    with patch('telecom.upgrade.scoped_catalog', return_value=client), patch.object(update_adapter.initial, 'snapshot', return_value=pod(repo+'@'+digest)) as update_reader:
        update_submitted = update_adapter.submit(user, update_request['id'], update_approval['id'])
        update_reader.return_value = pod(repo+'@'+digest)  # same image: a config-only revision
        update_observed = update_adapter.refresh(user, update_request['id'], 'observe-update')

    deployment_after_update = service.journal.get(user, 'deployments', request['id'])

    rollback_request = update_adapter.request(user, request['id'], publication_v1['id'], 'rollback', 'rollback-request')
    rollback_approval = update_adapter.approve(reviewer, rollback_request['id'], 'rollback-approve')
    with patch('telecom.upgrade.scoped_catalog', return_value=client), patch.object(update_adapter.initial, 'snapshot', return_value=pod(repo+'@'+digest)):
        rollback_submitted = update_adapter.submit(user, rollback_request['id'], rollback_approval['id'])
        rollback_observed = update_adapter.refresh(user, rollback_request['id'], 'observe-rollback')

    deployment_after_rollback = service.journal.get(user, 'deployments', request['id'])

    summary = {'status': 'OFFLINE_TESTED', 'remote_evidence': 'SIMULATED', 'source': 'PREBUILT_OCI',
        'buildkit_required': False, 'image_digest': digest, 'registry_bytes': 'VERIFIED_FIXTURE_CONTENT',
        'helm': 'REAL_LINT_RENDER', 'release_v1_id': release_v1['id'], 'release_v2_id': release_v2['id'],
        'initial_catalog': publication_v1['state'], 'initial_submission': submitted['state'], 'initial_deployment': observed['state'],
        'update_catalog': publication_v2['state'], 'update_submission': update_submitted['state'], 'update_observation': update_observed['state'],
        'deployment_release_after_update': deployment_after_update['release_id'],
        'rollback_submission': rollback_submitted['state'], 'rollback_observation': rollback_observed['state'],
        'deployment_release_after_rollback': deployment_after_rollback['release_id'],
        'release_history_length': len(deployment_after_rollback.get('release_history', [])),
        'certification': 'FIXTURE_ONLY; NO_SHIPPED_CONFIGURATION_SETS_THIS',
        'application_health': 'UNVERIFIED', 'protocol_verification': 'NOT_RUN',
        'simulated_nbi_calls': len(calls), 'production_calls': 0, 'production_eligible': False,
        'audit_integrity': service.journal.verify_audit()}
    (output/'result.json').write_text(json.dumps(summary, indent=2)+'\n')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True); parser.add_argument('--helm', default='helm')
    args = parser.parse_args(); print(json.dumps(run(args.output, args.helm), indent=2))


if __name__ == '__main__':
    main()
