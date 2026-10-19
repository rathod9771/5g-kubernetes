"""schema_version=2 multi-image onboarding: contract, discovery/verification, packaging.

Positive path is exercised end-to-end against a real (synthetic) multi-NF
Helm chart with `helm lint`/`helm template`. Negative/adversarial cases
reproduce, as compact self-contained fixtures, the exact real-world shapes
independently confirmed in this session against the actual upstream
free5gc-helm charts at commit d22485f5a95fd6bb97ec1252f0933ef5da17f96d
(cloned and rendered offline; not vendored into this repo) -- in particular:
free5gc's default NF containers set only `allowPrivilegeEscalation: false`
(no `runAsNonRoot`, no explicit `capabilities.drop`), its UPF/gNB containers
add `NET_ADMIN` without any pipeline-side approval, its init containers
(wait-nrf/wait-mongo/wait-amf) carry an empty securityContext entirely, and
none of its images are declared or pinned to a digest -- so the real chart,
unmodified, is correctly rejected by this verification engine.
"""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from telecom.contract import validate
from telecom import multi_image
from telecom.package import prepare

MANIFEST = json.loads((ROOT / 'examples/telecom/multi-nf.json').read_text())
CHART = ROOT / 'examples/telecom/multi-nf-chart'
HELM_ENV = dict(os.environ, HELM_CACHE_HOME='/tmp/helmcache', HELM_CONFIG_HOME='/tmp/helmconfig',
                HELM_DATA_HOME='/tmp/helmdata', KUBECONFIG='/dev/null')


def render(chart=CHART, values=()):
    import yaml
    args = ['helm', 'template', 'research-fixture', str(chart), '--namespace', 'research-sandbox']
    for key, value in values:
        args += ['--set', key + '=' + value]
    out = subprocess.run(args, capture_output=True, text=True, env=HELM_ENV, timeout=30)
    if out.returncode:
        raise AssertionError(out.stderr)
    return list(yaml.safe_load_all(out.stdout))


def fixture_container(identity, *, security=None, resources=None):
    return {
        'name': identity,
        'image': 'example.invalid/research/' + identity + ':v1.0.0',
        'securityContext': security if security is not None else {
            'runAsNonRoot': True, 'allowPrivilegeEscalation': False, 'capabilities': {'drop': ['ALL']}},
        'resources': resources if resources is not None else {'limits': {'cpu': '100m', 'memory': '128Mi'}},
    }


def fixture_deployment(name, container, *, init=None, pod=None):
    """`pod` defaults to the compliant shape (automount disabled, no custom
    service account) so adversarial tests about OTHER failure modes (e.g.
    undeclared image) aren't masked by an unrelated automount/SA rejection;
    pass `pod={...}` to override specific fields for pod-security tests."""
    template_spec = {'automountServiceAccountToken': False, 'containers': [container]}
    if init is not None:
        template_spec['initContainers'] = [init]
    if pod:
        template_spec.update(pod)
    spec = {'template': {'spec': template_spec}}
    return {'kind': 'Deployment', 'metadata': {'name': name}, 'spec': spec}


class ContractV2Tests(unittest.TestCase):
    def manifest(self, **overrides):
        m = copy.deepcopy(MANIFEST)
        m.update(overrides)
        return m

    def test_v1_manifest_still_validates_unchanged(self):
        v1 = json.loads((ROOT / 'examples/telecom/ran.json').read_text())
        validated = validate(v1)
        self.assertEqual(validated['schema_version'], 1)
        self.assertNotIn('images', validated)

    def test_v2_positive_manifest_validates(self):
        validated = validate(self.manifest())
        self.assertEqual(validated['schema_version'], 2)
        self.assertEqual({e['identity'] for e in validated['images']},
                          {'amf', 'smf', 'upf', 'mongodb', 'probe'})

    def test_unsupported_schema_version_rejected(self):
        for version in (0, 3, '1', None):
            with self.subTest(version=version), self.assertRaises(ValueError):
                validate(self.manifest(schema_version=version))

    def test_v1_and_v2_fields_are_mutually_exclusive(self):
        m = self.manifest()
        m['image'] = {'repository': 'a/b', 'digest': 'sha256:' + '0' * 64, 'platform': 'linux/amd64'}
        with self.assertRaises(ValueError):
            validate(m)

    def test_claimed_role_without_approved_image_rejected(self):
        m = self.manifest()
        m['roles'] = m['roles'] + ['nrf']
        m['interfaces']['SBI'] = m['interfaces']['SBI']  # nrf only needs SBI, already present
        with self.assertRaises(ValueError):
            validate(m)

    def test_duplicate_image_identity_rejected(self):
        m = self.manifest()
        m['images'] = m['images'] + [dict(m['images'][0])]
        with self.assertRaises(ValueError):
            validate(m)

    def test_floating_tag_pin_rejected(self):
        for tag in ('latest', 'LATEST', 'master', 'main', 'head'):
            m = self.manifest()
            m['images'][0] = dict(m['images'][0], tag_pin=tag)
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                validate(m)

    def test_missing_digest_rejected(self):
        m = self.manifest()
        del m['images'][0]['digest']
        with self.assertRaises(ValueError):
            validate(m)

    def test_conflicting_digest_for_same_tag_rejected(self):
        m = self.manifest()
        clash = dict(m['images'][1], identity='amf-clash', repository=m['images'][0]['repository'],
                     tag_pin=m['images'][0]['tag_pin'])
        m['images'].append(clash)
        with self.assertRaises(ValueError):
            validate(m)

    def test_network_function_and_supporting_container_fields_are_exclusive(self):
        m = self.manifest()
        m['images'][0]['purpose'] = 'database'  # amf is a network-function; purpose must be null
        with self.assertRaises(ValueError):
            validate(m)
        m = self.manifest()
        m['images'][3]['role'] = 'upf'  # mongodb is a supporting-container; role must be null
        with self.assertRaises(ValueError):
            validate(m)

    def test_unknown_support_purpose_rejected(self):
        m = self.manifest()
        m['images'][3]['purpose'] = 'arbitrary-junk'
        with self.assertRaises(ValueError):
            validate(m)

    def test_images_list_bounds(self):
        m = self.manifest()
        m['images'] = []
        with self.assertRaises(ValueError):
            validate(m)
        m['images'] = [dict(MANIFEST['images'][0], identity='x' + str(i)) for i in range(65)]
        with self.assertRaises(ValueError):
            validate(m)

    def test_elevated_capabilities_require_hardware_mode(self):
        m = self.manifest()
        m['hardware'] = {'mode': 'simulation', 'devices': [], 'hugepages_mib': 0,
                          'elevated_capabilities': {'upf': ['NET_ADMIN']}}
        with self.assertRaises(ValueError):
            validate(m)

    def test_elevated_capabilities_unknown_capability_rejected(self):
        m = self.manifest()
        m['hardware']['elevated_capabilities'] = {'upf': ['SYS_ADMIN']}
        with self.assertRaises(ValueError):
            validate(m)

    def test_hardware_rejects_unknown_v1_style_extra_key_even_in_v2(self):
        m = self.manifest()
        m['hardware']['bogus_extra'] = True
        with self.assertRaises(ValueError):
            validate(m)


class MultiImageDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.manifest = validate(json.loads(json.dumps(MANIFEST)))

    def test_positive_end_to_end_render_matches_inventory(self):
        docs = render()
        discovery = multi_image.verify(self.manifest, docs)
        self.assertEqual(set(discovery), {'amf', 'smf', 'upf', 'mongodb', 'probe'})
        # This chart family concatenates `repository:tag` directly in its
        # templates (confirmed against the real free5gc charts too): every
        # rendered reference is therefore tag-based, never digest-pinned.
        self.assertTrue(all(record['kind'] == 'tag' for record in discovery.values()))
        self.assertFalse(any(o['hook'] for record in discovery.values() for o in record['occurrences']))

    def test_undeclared_helm_hook_image_rejected(self):
        """A Helm test/lifecycle hook still executes real content; its image
        must be declared and approved too -- it is never silently skipped."""
        docs = render() + [{
            'kind': 'Pod', 'metadata': {'name': 'conn-test', 'annotations': {'helm.sh/hook': 'test'}},
            'spec': {'containers': [{'name': 'x', 'image': 'bitnami/kubectl:1.22.0'}]},
        }]
        with self.assertRaises(ValueError):
            multi_image.verify(self.manifest, docs)

    def test_approved_helm_hook_image_discovered_and_marked_hook_only(self):
        """A hook using an APPROVED, hardened image is accepted and tracked
        as hook-only; it does not by itself satisfy role-completeness."""
        docs = render() + [{
            'kind': 'Pod', 'metadata': {'name': 'conn-test', 'annotations': {'helm.sh/hook': 'test'}},
            'spec': {'automountServiceAccountToken': False, 'containers': [fixture_container('probe')]},
        }]
        discovery = multi_image.verify(self.manifest, docs)
        self.assertIn('probe', discovery)
        self.assertTrue(any(o['hook'] for o in discovery['probe']['occurrences']))
        self.assertFalse(all(o['hook'] for o in discovery['probe']['occurrences']))  # also rendered by probe-job.yaml

    def test_helm_hook_with_unhardened_container_rejected(self):
        """A hook's container must meet the exact same security bar as any other."""
        docs = render() + [{
            'kind': 'Pod', 'metadata': {'name': 'conn-test', 'annotations': {'helm.sh/hook': 'test'}},
            'spec': {'containers': [{'name': 'x', 'image': 'example.invalid/research/probe:v1.0.0'}]},
        }]
        with self.assertRaises(ValueError):
            multi_image.verify(self.manifest, docs)

    def test_helm_hook_forbidden_rbac_kind_rejected(self):
        """A hook-annotated ClusterRoleBinding is still forbidden, not skipped
        (the real free5gc/UERANSIM test hooks use exactly this annotation)."""
        docs = render() + [{
            'kind': 'ClusterRoleBinding',
            'metadata': {'name': 'hook-escalate', 'annotations': {'helm.sh/hook': 'test'}},
        }]
        with self.assertRaises(ValueError):
            multi_image.verify(self.manifest, docs)

    def test_undeclared_init_container_image_rejected(self):
        docs = [fixture_deployment('free-rider', fixture_container('amf'), init=fixture_container('unapproved'))]
        with self.assertRaises(ValueError):
            multi_image.verify(self.manifest, docs)

    def test_rendered_secret_rejected(self):
        docs = render(values=[('unsafe.includeSecret', 'true')])
        with self.assertRaises(ValueError):
            multi_image.verify(self.manifest, docs)

    def test_rendered_network_attachment_definition_rejected(self):
        docs = render(values=[('unsafe.includeMultus', 'true')])
        with self.assertRaises(ValueError):
            multi_image.verify(self.manifest, docs)

    def test_undeclared_image_rejected(self):
        docs = [fixture_deployment('free-rider', fixture_container('unapproved'))]
        with self.assertRaises(ValueError):
            multi_image.verify(self.manifest, docs)

    def test_mismatched_tag_rejected(self):
        docs = render(values=[('smf.tag', 'v9.9.9')])
        with self.assertRaises(ValueError):
            multi_image.verify(self.manifest, docs)

    def test_unapproved_elevated_capability_rejected(self):
        manifest_no_elevation = copy.deepcopy(dict(self.manifest))
        manifest_no_elevation['hardware'] = dict(manifest_no_elevation['hardware'])
        manifest_no_elevation['hardware'].pop('elevated_capabilities')
        docs = render()
        with self.assertRaises(ValueError):
            multi_image.verify(manifest_no_elevation, docs)

    def test_elevated_capability_not_matching_declared_identity_rejected(self):
        manifest_wrong_identity = copy.deepcopy(dict(self.manifest))
        manifest_wrong_identity['hardware'] = dict(manifest_wrong_identity['hardware'],
                                                     elevated_capabilities={'amf': ['NET_ADMIN']})
        docs = render()  # NET_ADMIN is actually added by upf, not amf
        with self.assertRaises(ValueError):
            multi_image.verify(manifest_wrong_identity, docs)

    def test_missing_rendered_role_detected(self):
        docs = [d for d in render() if d.get('metadata', {}).get('name') != 'research-fixture-upf']
        with self.assertRaises(ValueError):
            multi_image.verify(self.manifest, docs)

    def test_host_namespace_sharing_rejected(self):
        docs = render()
        for doc in docs:
            if doc.get('kind') == 'Deployment':
                doc['spec']['template']['spec']['hostNetwork'] = True
        with self.assertRaises(ValueError):
            multi_image.verify(self.manifest, docs)

    def test_missing_automount_service_account_token_rejected(self):
        docs = render()
        for doc in docs:
            if doc.get('kind') == 'Deployment':
                del doc['spec']['template']['spec']['automountServiceAccountToken']
        with self.assertRaises(ValueError):
            multi_image.verify(self.manifest, docs)

    def test_automount_service_account_token_true_rejected(self):
        docs = render()
        for doc in docs:
            if doc.get('kind') == 'Deployment':
                doc['spec']['template']['spec']['automountServiceAccountToken'] = True
        with self.assertRaises(ValueError):
            multi_image.verify(self.manifest, docs)

    def test_custom_service_account_name_rejected(self):
        docs = render()
        for doc in docs:
            if doc.get('kind') == 'Deployment':
                doc['spec']['template']['spec']['serviceAccountName'] = 'custom-nf-sa'
        with self.assertRaises(ValueError):
            multi_image.verify(self.manifest, docs)

    def test_host_port_in_container_rejected(self):
        docs = [fixture_deployment('free-rider', dict(
            fixture_container('amf'), ports=[{'containerPort': 8080, 'hostPort': 8080}]))]
        with self.assertRaises(ValueError):
            multi_image.verify(self.manifest, docs)

    def test_host_port_in_init_container_rejected(self):
        docs = [fixture_deployment('free-rider', fixture_container('amf'), init=dict(
            fixture_container('probe'), ports=[{'containerPort': 9090, 'hostPort': 9090}]))]
        with self.assertRaises(ValueError):
            multi_image.verify(self.manifest, docs)

    def test_host_port_in_helm_hook_job_rejected(self):
        """The real free5gc/UERANSIM test hooks use exactly this annotation;
        an executable hook container is checked for hostPort like any other."""
        docs = render() + [{
            'kind': 'Job', 'metadata': {'name': 'conn-test-job', 'annotations': {'helm.sh/hook': 'test'}},
            'spec': {'template': {'spec': {
                'automountServiceAccountToken': False,
                'containers': [dict(fixture_container('probe'), ports=[{'containerPort': 7070, 'hostPort': 7070}])],
            }}},
        }]
        with self.assertRaises(ValueError):
            multi_image.verify(self.manifest, docs)

    def test_missing_automount_in_cronjob_rejected(self):
        docs = render() + [{
            'kind': 'CronJob', 'metadata': {'name': 'probe-cron'},
            'spec': {'jobTemplate': {'spec': {'template': {'spec': {
                'containers': [fixture_container('probe')],
            }}}}},
        }]
        with self.assertRaises(ValueError):
            multi_image.verify(self.manifest, docs)

    def test_custom_service_account_in_standalone_pod_rejected(self):
        docs = render() + [{
            'kind': 'Pod', 'metadata': {'name': 'conn-test'},
            'spec': {
                'automountServiceAccountToken': False, 'serviceAccountName': 'custom-nf-sa',
                'containers': [fixture_container('probe')],
            },
        }]
        with self.assertRaises(ValueError):
            multi_image.verify(self.manifest, docs)

    def test_compliant_pod_specs_across_controllers_pass(self):
        """A compliant Deployment/CronJob/Pod -- automount disabled, no
        custom service account, no hostPort -- is accepted on every
        supported controller kind, not only Deployment."""
        manifest = copy.deepcopy(dict(self.manifest))
        docs = [
            fixture_deployment('nf-amf', fixture_container('amf')),
            {
                'kind': 'CronJob', 'metadata': {'name': 'nf-smf-cron'},
                'spec': {'jobTemplate': {'spec': {'template': {'spec': {
                    'automountServiceAccountToken': False,
                    'containers': [fixture_container('smf')],
                }}}}},
            },
            {
                'kind': 'Pod', 'metadata': {'name': 'nf-upf'},
                'spec': {'automountServiceAccountToken': False, 'containers': [fixture_container('upf')]},
            },
        ]
        discovery = multi_image.verify(manifest, docs)
        self.assertEqual(set(discovery), {'amf', 'smf', 'upf'})

    def test_forbidden_cluster_rbac_kind_rejected(self):
        docs = render() + [{'kind': 'ClusterRole', 'metadata': {'name': 'escalate'}, 'rules': []}]
        with self.assertRaises(ValueError):
            multi_image.verify(self.manifest, docs)

    def test_hostpath_persistent_volume_rejected(self):
        docs = render() + [{
            'kind': 'PersistentVolume', 'metadata': {'name': 'local-pv'},
            'spec': {'capacity': {'storage': '10Gi'}, 'accessModes': ['ReadWriteOnce'],
                     'hostPath': {'path': '/data'}},
        }]
        with self.assertRaises(ValueError):
            multi_image.verify(self.manifest, docs)

    def test_nfs_persistent_volume_rejected(self):
        docs = render() + [{
            'kind': 'PersistentVolume', 'metadata': {'name': 'nfs-pv'},
            'spec': {'capacity': {'storage': '10Gi'}, 'accessModes': ['ReadWriteMany'],
                     'nfs': {'server': 'nfs.example.invalid', 'path': '/export'}},
        }]
        with self.assertRaises(ValueError):
            multi_image.verify(self.manifest, docs)

    def test_csi_persistent_volume_rejected(self):
        docs = render() + [{
            'kind': 'PersistentVolume', 'metadata': {'name': 'csi-pv'},
            'spec': {'capacity': {'storage': '10Gi'}, 'accessModes': ['ReadWriteOnce'],
                     'csi': {'driver': 'csi.example.invalid', 'volumeHandle': 'vol-1'}},
        }]
        with self.assertRaises(ValueError):
            multi_image.verify(self.manifest, docs)

    def test_persistent_volume_without_hostpath_still_rejected(self):
        """PersistentVolume is forbidden by kind alone, regardless of backend
        -- even one declaring no storage backend at all is rejected."""
        docs = render() + [{
            'kind': 'PersistentVolume', 'metadata': {'name': 'bare-pv'},
            'spec': {'capacity': {'storage': '10Gi'}, 'accessModes': ['ReadWriteOnce']},
        }]
        with self.assertRaises(ValueError):
            multi_image.verify(self.manifest, docs)

    def test_compliant_pvc_accepted(self):
        docs = render() + [{
            'kind': 'PersistentVolumeClaim', 'metadata': {'name': 'mongodb-data'},
            'spec': {'accessModes': ['ReadWriteOnce'], 'resources': {'requests': {'storage': '10Gi'}}},
        }]
        discovery = multi_image.verify(self.manifest, docs)
        self.assertEqual(set(discovery), {'amf', 'smf', 'upf', 'mongodb', 'probe'})

    def test_pvc_with_explicit_volume_name_rejected(self):
        docs = render() + [{
            'kind': 'PersistentVolumeClaim', 'metadata': {'name': 'mongodb-data'},
            'spec': {'accessModes': ['ReadWriteOnce'], 'resources': {'requests': {'storage': '10Gi'}},
                     'volumeName': 'local-pv'},
        }]
        with self.assertRaises(ValueError):
            multi_image.verify(self.manifest, docs)

    def test_pvc_with_unapproved_storage_class_rejected(self):
        """No offline mechanism exists to verify operator approval of a
        StorageClass, so any explicit value fails closed (see module docstring)."""
        docs = render() + [{
            'kind': 'PersistentVolumeClaim', 'metadata': {'name': 'mongodb-data'},
            'spec': {'accessModes': ['ReadWriteOnce'], 'resources': {'requests': {'storage': '10Gi'}},
                     'storageClassName': 'fast-ssd'},
        }]
        with self.assertRaises(ValueError):
            multi_image.verify(self.manifest, docs)

    def test_pvc_data_source_rejected(self):
        docs = render() + [{
            'kind': 'PersistentVolumeClaim', 'metadata': {'name': 'mongodb-data'},
            'spec': {'accessModes': ['ReadWriteOnce'], 'resources': {'requests': {'storage': '10Gi'}},
                     'dataSource': {'kind': 'VolumeSnapshot', 'name': 'attacker-snapshot'}},
        }]
        with self.assertRaises(ValueError):
            multi_image.verify(self.manifest, docs)

    def test_pvc_selector_rejected(self):
        docs = render() + [{
            'kind': 'PersistentVolumeClaim', 'metadata': {'name': 'mongodb-data'},
            'spec': {'accessModes': ['ReadWriteOnce'], 'resources': {'requests': {'storage': '10Gi'}},
                     'selector': {'matchLabels': {'name': 'local-pv'}}},
        }]
        with self.assertRaises(ValueError):
            multi_image.verify(self.manifest, docs)

    def test_pvc_block_volume_mode_rejected(self):
        docs = render() + [{
            'kind': 'PersistentVolumeClaim', 'metadata': {'name': 'mongodb-data'},
            'spec': {'accessModes': ['ReadWriteOnce'], 'resources': {'requests': {'storage': '10Gi'}},
                     'volumeMode': 'Block'},
        }]
        with self.assertRaises(ValueError):
            multi_image.verify(self.manifest, docs)

    def test_pvc_storage_request_out_of_bounds_rejected(self):
        docs = render() + [{
            'kind': 'PersistentVolumeClaim', 'metadata': {'name': 'mongodb-data'},
            'spec': {'accessModes': ['ReadWriteOnce'], 'resources': {'requests': {'storage': '5000Ti'}}},
        }]
        with self.assertRaises(ValueError):
            multi_image.verify(self.manifest, docs)

    def test_pvc_wrong_namespace_rejected(self):
        docs = render() + [{
            'kind': 'PersistentVolumeClaim', 'metadata': {'name': 'mongodb-data', 'namespace': 'other-namespace'},
            'spec': {'accessModes': ['ReadWriteOnce'], 'resources': {'requests': {'storage': '10Gi'}}},
        }]
        with self.assertRaises(ValueError):
            multi_image.verify(self.manifest, docs)

    def test_pod_referencing_validated_pvc_accepted(self):
        docs = render() + [
            {'kind': 'PersistentVolumeClaim', 'metadata': {'name': 'extra-data'},
             'spec': {'accessModes': ['ReadWriteOnce'], 'resources': {'requests': {'storage': '5Gi'}}}},
            {'kind': 'Pod', 'metadata': {'name': 'extra-consumer'},
             'spec': {'automountServiceAccountToken': False, 'containers': [fixture_container('probe')],
                      'volumes': [{'name': 'data', 'persistentVolumeClaim': {'claimName': 'extra-data'}}]}},
        ]
        discovery = multi_image.verify(self.manifest, docs)
        self.assertEqual(set(discovery), {'amf', 'smf', 'upf', 'mongodb', 'probe'})

    def test_pod_referencing_undeclared_pvc_rejected(self):
        docs = render() + [
            {'kind': 'Pod', 'metadata': {'name': 'extra-consumer'},
             'spec': {'automountServiceAccountToken': False, 'containers': [fixture_container('probe')],
                      'volumes': [{'name': 'data', 'persistentVolumeClaim': {'claimName': 'never-declared'}}]}},
        ]
        with self.assertRaises(ValueError):
            multi_image.verify(self.manifest, docs)

    def test_pod_referencing_mismatched_pvc_name_rejected(self):
        docs = render() + [
            {'kind': 'PersistentVolumeClaim', 'metadata': {'name': 'extra-data'},
             'spec': {'accessModes': ['ReadWriteOnce'], 'resources': {'requests': {'storage': '5Gi'}}}},
            {'kind': 'Pod', 'metadata': {'name': 'extra-consumer'},
             'spec': {'automountServiceAccountToken': False, 'containers': [fixture_container('probe')],
                      'volumes': [{'name': 'data', 'persistentVolumeClaim': {'claimName': 'wrong-name'}}]}},
        ]
        with self.assertRaises(ValueError):
            multi_image.verify(self.manifest, docs)

    def test_pod_in_different_namespace_referencing_pvc_rejected(self):
        """The PVC itself is only ever valid in research-sandbox (_check_pvc),
        so this proves the pod-side namespace match is independently enforced,
        not merely inherited from the PVC's own namespace check."""
        docs = render() + [
            {'kind': 'PersistentVolumeClaim', 'metadata': {'name': 'extra-data'},
             'spec': {'accessModes': ['ReadWriteOnce'], 'resources': {'requests': {'storage': '5Gi'}}}},
            {'kind': 'Pod', 'metadata': {'name': 'extra-consumer', 'namespace': 'other-namespace'},
             'spec': {'automountServiceAccountToken': False, 'containers': [fixture_container('probe')],
                      'volumes': [{'name': 'data', 'persistentVolumeClaim': {'claimName': 'extra-data'}}]}},
        ]
        with self.assertRaises(ValueError):
            multi_image.verify(self.manifest, docs)

    def test_empty_claim_name_rejected(self):
        docs = render() + [
            {'kind': 'PersistentVolumeClaim', 'metadata': {'name': 'extra-data'},
             'spec': {'accessModes': ['ReadWriteOnce'], 'resources': {'requests': {'storage': '5Gi'}}}},
            {'kind': 'Pod', 'metadata': {'name': 'extra-consumer'},
             'spec': {'automountServiceAccountToken': False, 'containers': [fixture_container('probe')],
                      'volumes': [{'name': 'data', 'persistentVolumeClaim': {'claimName': ''}}]}},
        ]
        with self.assertRaises(ValueError):
            multi_image.verify(self.manifest, docs)

    def test_malformed_pvc_volume_reference_rejected(self):
        docs = render() + [
            {'kind': 'PersistentVolumeClaim', 'metadata': {'name': 'extra-data'},
             'spec': {'accessModes': ['ReadWriteOnce'], 'resources': {'requests': {'storage': '5Gi'}}}},
            {'kind': 'Pod', 'metadata': {'name': 'extra-consumer'},
             'spec': {'automountServiceAccountToken': False, 'containers': [fixture_container('probe')],
                      'volumes': [{'name': 'data',
                                   'persistentVolumeClaim': {'claimName': 'extra-data', 'unexpected': 'x'}}]}},
        ]
        with self.assertRaises(ValueError):
            multi_image.verify(self.manifest, docs)

    def test_deployment_referencing_undeclared_pvc_rejected(self):
        docs = render()
        for doc in docs:
            if doc.get('kind') == 'Deployment':
                doc['spec']['template']['spec']['volumes'] = [
                    {'name': 'data', 'persistentVolumeClaim': {'claimName': 'never-declared'}}]
        with self.assertRaises(ValueError):
            multi_image.verify(self.manifest, docs)

    def test_helm_hook_job_referencing_undeclared_pvc_rejected(self):
        docs = render() + [{
            'kind': 'Job', 'metadata': {'name': 'conn-test-job', 'annotations': {'helm.sh/hook': 'test'}},
            'spec': {'template': {'spec': {
                'automountServiceAccountToken': False,
                'containers': [fixture_container('probe')],
                'volumes': [{'name': 'data', 'persistentVolumeClaim': {'claimName': 'never-declared'}}],
            }}},
        }]
        with self.assertRaises(ValueError):
            multi_image.verify(self.manifest, docs)

    def test_statefulset_volume_claim_templates_rejected(self):
        """volumeClaimTemplates mint PVCs through a binding path (volumeMounts)
        this verifier does not resolve; explicitly rejected rather than ignored."""
        docs = render()
        for doc in docs:
            if doc.get('kind') == 'StatefulSet':
                doc['spec']['volumeClaimTemplates'] = [{
                    'metadata': {'name': 'data'},
                    'spec': {'accessModes': ['ReadWriteOnce'], 'resources': {'requests': {'storage': '10Gi'}}},
                }]
        with self.assertRaises(ValueError):
            multi_image.verify(self.manifest, docs)

    def test_hostpath_volume_rejected_after_pvc_refactor(self):
        docs = render()
        for doc in docs:
            if doc.get('kind') == 'Deployment':
                doc['spec']['template']['spec']['volumes'] = [{'name': 'h', 'hostPath': {'path': '/'}}]
        with self.assertRaises(ValueError):
            multi_image.verify(self.manifest, docs)

    def test_secret_volume_rejected_after_pvc_refactor(self):
        docs = render()
        for doc in docs:
            if doc.get('kind') == 'Deployment':
                doc['spec']['template']['spec']['volumes'] = [{'name': 's', 'secret': {'secretName': 'x'}}]
        with self.assertRaises(ValueError):
            multi_image.verify(self.manifest, docs)

    def test_unrecognized_kind_requires_review(self):
        docs = render() + [{'kind': 'CustomResourceDefinition', 'metadata': {'name': 'x'}}]
        with self.assertRaises(ValueError):
            multi_image.verify(self.manifest, docs)

    def test_duplicate_resource_name_rejected_in_package_prepare(self):
        with tempfile.TemporaryDirectory() as t:
            workspace = Path(t)
            shutil.copytree(CHART, workspace / 'chart')
            (workspace / 'chart/templates/dup.yaml').write_text(
                'apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: {{ .Release.Name }}-config\n')
            with self.assertRaises(ValueError):
                prepare(workspace, 'chart', self.manifest, helm='helm')

    def test_real_free5gc_default_shape_rejected_missing_hardening(self):
        """Compact reproduction of the real upstream free5gc AMF container shape:
        only allowPrivilegeEscalation is set, no runAsNonRoot/capabilities.drop,
        and its wait-nrf init container has an empty securityContext entirely --
        independently confirmed against the real chart at the pinned commit.
        """
        manifest = copy.deepcopy(dict(self.manifest))
        docs = [fixture_deployment(
            'free5gc-amf',
            {'name': 'amf', 'image': 'example.invalid/research/amf:v1.0.0',
             'securityContext': {'allowPrivilegeEscalation': False},
             'resources': {'limits': {'cpu': '300m', 'memory': '256Mi'}}},
            init={'name': 'wait-nrf', 'image': 'example.invalid/research/probe:v1.0.0',
                  'securityContext': {}, 'resources': {}},
        )]
        with self.assertRaises(ValueError):
            multi_image.verify(manifest, docs)

    def test_real_upf_shape_with_net_admin_and_no_capability_drop_rejected_without_approval(self):
        """Real upstream free5gc UPF shape: NET_ADMIN added, but capabilities.drop
        is never set (unlike this repo's fixture chart, which sets both)."""
        manifest = copy.deepcopy(dict(self.manifest))
        docs = [fixture_deployment(
            'free5gc-upf',
            {'name': 'upf', 'image': 'example.invalid/research/upf:v1.0.0',
             'securityContext': {'allowPrivilegeEscalation': False, 'capabilities': {'add': ['NET_ADMIN']}},
             'resources': {'limits': {'cpu': '1000m', 'memory': '1024Mi'}}},
        )]
        with self.assertRaises(ValueError):
            multi_image.verify(manifest, docs)


class PrepareV2PackagingTests(unittest.TestCase):
    def setUp(self):
        self.manifest = validate(json.loads(json.dumps(MANIFEST)))
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Path(self.tmp.name)
        shutil.copytree(CHART, self.workspace / 'chart')

    def test_end_to_end_prepare_builds_valid_osm_package(self):
        result = prepare(self.workspace, 'chart', self.manifest, helm='helm')
        self.assertTrue(result['namespace_portable'])
        self.assertEqual(result['helm_validation']['outcome'], 'PASS')
        self.assertEqual(set(result['package_digests']),
                          {result['scenario']['knf_package'] + '.tar.gz',
                           result['scenario']['nsd_package'] + '.tar.gz'})

    def test_dockerfile_build_binding_rejected_for_v2(self):
        with self.assertRaises(ValueError):
            prepare(self.workspace, 'chart', self.manifest, helm='helm',
                    verified_image='example.invalid/research/amf@sha256:' + 'a' * 64)

    def test_reviewed_inputs_only_accepted_for_v2(self):
        v1 = validate(json.loads((ROOT / 'examples/telecom/ran.json').read_text()))
        with self.assertRaises(ValueError):
            prepare(self.workspace, 'chart', v1, helm='helm', reviewed_inputs={})

    def test_prepare_rejects_chart_with_rendered_secret(self):
        overlay = self.workspace / 'chart/values.yaml'
        values = overlay.read_text().replace('includeSecret: false', 'includeSecret: true')
        overlay.write_text(values)
        with self.assertRaises(ValueError):
            prepare(self.workspace, 'chart', self.manifest, helm='helm')


if __name__ == '__main__':
    unittest.main()
