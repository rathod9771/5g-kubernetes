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
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from telecom.contract import identity, validate
from telecom import chart_adapters, multi_image, subcharts
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


HOOK_ORIGINAL = (b'apiVersion: v1\nkind: Pod\nmetadata:\n  name: {{ .Release.Name }}-connectivity-test\n'
                  b'  annotations:\n    helm.sh/hook: test\nspec:\n  containers:\n    - name: test\n'
                  b'      image: busybox\n      command: ["sh", "-c", "echo ok"]\n')

BUSYBOX_IMAGE_ENTRY = {
    'identity': 'busybox-test', 'kind': 'supporting-container', 'role': None, 'purpose': 'test-utility',
    'repository': 'example.invalid/research/busybox', 'tag_pin': 'v1.36.1',
    'digest': 'sha256:' + 'b' * 64, 'platform': 'linux/amd64',
}


class ChartAdapterTests(unittest.TestCase):
    """Synthetic reproduction of the documented free5GC defect: a Helm test
    hook hardcodes `image: busybox` and omits pod-security settings,
    bypassing the chart's own values.yaml-driven image contract entirely
    (a literal image string cannot be fixed by --set/a values overlay).
    This exercises chart_adapters.py's own offline, byte-digest-gated patch
    mechanism against a purpose-built fixture matching its one registered
    SYNTHETIC entry. It never fetches, clones, or asserts anything about the
    real upstream free5GC chart, which exists only on the Dell and was not
    accessed from this (HP) environment."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Path(self.tmp.name)
        shutil.copytree(CHART, self.workspace / 'chart')
        manifest = json.loads(json.dumps(MANIFEST))
        manifest['source'] = {'kind': 'local-git', 'path': 'chart', 'commit': '0' * 40}
        manifest['images'] = manifest['images'] + [BUSYBOX_IMAGE_ENTRY]
        self.manifest = validate(manifest)
        # The evidence execution.acquire() would have produced for THIS
        # commit, had pipeline.run() actually acquired it. Threaded through
        # explicitly here because these tests call prepare() directly,
        # bypassing pipeline.run()/acquire() -- see
        # test_telecom_multi_image_pipeline.py for the real, end-to-end
        # acquire()-backed path.
        self.evidence = {'authority': 'LOCAL_OBJECTS_VERIFIED', 'commit': '0' * 40}

    def write_hook(self, content):
        (self.workspace / 'chart/templates/test-hook.yaml').write_bytes(content)

    def test_hardcoded_busybox_hook_rejected_without_matching_adapter(self):
        """A different source commit means no registered adapter matches --
        the hook is rejected on its own merits (Option B), never silently
        passed through just because an adapter mechanism exists somewhere.
        No evidence is passed at all, proving it's irrelevant when nothing
        matches the registry in the first place."""
        self.write_hook(HOOK_ORIGINAL)
        manifest = copy.deepcopy(dict(self.manifest))
        manifest['source'] = {'kind': 'local-git', 'path': 'chart', 'commit': '1' * 40}
        with self.assertRaises(ValueError):
            prepare(self.workspace, 'chart', manifest, helm='helm')

    def test_hardcoded_busybox_hook_adapted_to_digest_pinned_and_compliant(self):
        self.write_hook(HOOK_ORIGINAL)
        result = prepare(self.workspace, 'chart', self.manifest, helm='helm', source_evidence=self.evidence)
        self.assertEqual([a['path'] for a in result['adapted_files']], ['templates/test-hook.yaml'])
        discovery = result['image_discovery']
        self.assertIn('busybox-test', discovery)
        self.assertEqual(discovery['busybox-test']['kind'], 'digest')
        self.assertTrue(all(o['hook'] for o in discovery['busybox-test']['occurrences']))

    def test_prepare_never_produces_registry_verification_evidence(self):
        """Adaptation only gets a hook PAST packaging; it never substitutes
        for the pipeline's own independent, live registry verification
        (pipeline.verify_images), which prepare() alone never runs."""
        self.write_hook(HOOK_ORIGINAL)
        result = prepare(self.workspace, 'chart', self.manifest, helm='helm', source_evidence=self.evidence)
        self.assertNotIn('image_verification', result)

    def test_adapter_rejects_stale_review_when_hook_content_drifted(self):
        self.write_hook(HOOK_ORIGINAL.replace(b'echo ok', b'echo mutated'))
        with self.assertRaisesRegex(ValueError, 'stale'):
            prepare(self.workspace, 'chart', self.manifest, helm='helm', source_evidence=self.evidence)

    def test_adapter_target_file_missing_rejected(self):
        # No templates/test-hook.yaml written at all; the registered adapter
        # has nothing to patch, so it must fail closed rather than no-op.
        with self.assertRaisesRegex(ValueError, 'missing'):
            prepare(self.workspace, 'chart', self.manifest, helm='helm', source_evidence=self.evidence)

    def test_adapter_does_not_mask_unrelated_undeclared_image(self):
        """Adaptation only ever touches its own registered target file; an
        unrelated undeclared image elsewhere in the same chart is still
        caught by the unweakened multi_image.verify() check."""
        self.write_hook(HOOK_ORIGINAL)
        bad = self.workspace / 'chart/templates/rogue.yaml'
        bad.write_text('apiVersion: v1\nkind: Pod\nmetadata:\n  name: rogue\nspec:\n'
                        '  automountServiceAccountToken: false\n  containers:\n'
                        '    - name: x\n      image: attacker.invalid/payload:v1\n')
        with self.assertRaises(ValueError):
            prepare(self.workspace, 'chart', self.manifest, helm='helm', source_evidence=self.evidence)

    def test_adapted_hook_still_requires_its_image_to_be_declared(self):
        """A badly-reviewed adapter patch -- one whose replacement image
        isn't actually declared in manifest['images'] -- is still caught by
        the normal image-declaration check; adaptation doesn't get a free
        pass just because the content came from a reviewed patch, verified
        evidence and all."""
        self.write_hook(HOOK_ORIGINAL)
        manifest = json.loads(json.dumps(MANIFEST))
        manifest['source'] = {'kind': 'local-git', 'path': 'chart', 'commit': '0' * 40}
        # busybox-test is deliberately NOT declared here.
        manifest = validate(manifest)
        with self.assertRaises(ValueError):
            prepare(self.workspace, 'chart', manifest, helm='helm', source_evidence=self.evidence)

    def test_vendored_subchart_without_reviewed_inventory_rejected(self):
        """A `charts/` vendored subchart tree is now a supported shape (see
        subcharts.py and VendoredSubchartTests below) -- but only with an
        explicit, exact, reviewed per-file digest inventory. Without one,
        it is still rejected, just via subcharts.validate()'s own gate
        rather than safe_chart_inputs()'s path allowlist. The hook is
        adapted successfully first (valid evidence), isolating this as the
        one and only remaining failure."""
        self.write_hook(HOOK_ORIGINAL)
        sub = self.workspace / 'chart/charts/mongodb/templates'
        sub.mkdir(parents=True)
        (self.workspace / 'chart/charts/mongodb/Chart.yaml').write_text(
            'apiVersion: v2\nname: mongodb\nversion: 1.0.0\n')
        with self.assertRaisesRegex(ValueError, 'explicit reviewed inventory'):
            prepare(self.workspace, 'chart', self.manifest, helm='helm', source_evidence=self.evidence)

    def test_embedded_private_key_in_template_rejected(self):
        self.write_hook(HOOK_ORIGINAL)
        bad = self.workspace / 'chart/templates/keyed.yaml'
        bad.write_text('# -----BEGIN RSA PRIVATE KEY-----\n# fake\n# -----END RSA PRIVATE KEY-----\n')
        with self.assertRaises(ValueError):
            prepare(self.workspace, 'chart', self.manifest, helm='helm', source_evidence=self.evidence)

    def test_adapter_is_noop_for_schema_v1(self):
        v1 = validate(json.loads((ROOT / 'examples/telecom/ran.json').read_text()))
        files = {'Chart.yaml': b'name: multi-nf\nversion: 0.1.0\n'}
        adapted, provenance = chart_adapters.adapt(files, v1)
        self.assertEqual(adapted, files)
        self.assertEqual(provenance, [])

    def test_missing_source_evidence_rejects_adaptation(self):
        """Direct prepare() call, bypassing pipeline.run()/acquire() entirely
        -- no source_evidence at all -- cannot trigger adaptation even
        though the chart identity otherwise matches a registered entry."""
        self.write_hook(HOOK_ORIGINAL)
        with self.assertRaisesRegex(ValueError, 'verified source acquisition evidence'):
            prepare(self.workspace, 'chart', self.manifest, helm='helm')

    def test_provider_declared_evidence_rejected_for_adaptation(self):
        """A source that was never actually acquired (oci/helm, or any
        source whose evidence is merely self-reported) must not unlock
        adaptation just because a dict happens to be present."""
        self.write_hook(HOOK_ORIGINAL)
        declared = {'authority': 'PROVIDER_DECLARED', 'commit': '0' * 40}
        with self.assertRaisesRegex(ValueError, 'verified source acquisition evidence'):
            prepare(self.workspace, 'chart', self.manifest, helm='helm', source_evidence=declared)

    def test_evidence_commit_mismatch_rejected(self):
        """A verified authority whose OWN commit field disagrees with the
        manifest's declared commit must not authorize adaptation -- the
        evidence has to attest to the EXACT commit being packaged."""
        self.write_hook(HOOK_ORIGINAL)
        mismatched = {'authority': 'LOCAL_OBJECTS_VERIFIED', 'commit': '9' * 40}
        with self.assertRaisesRegex(ValueError, 'verified source acquisition evidence'):
            prepare(self.workspace, 'chart', self.manifest, helm='helm', source_evidence=mismatched)

    def test_fabricated_evidence_still_blocked_by_content_drift(self):
        """A hand-constructed dict that LOOKS exactly like genuine acquire()
        evidence (right shape, right authority, right commit) -- never
        produced by any real acquisition -- still cannot make adaptation
        accept drifted content. The real security boundary is the per-file
        byte-exact digest match, not the evidence dict's shape; a caller
        able to fabricate the latter still gains nothing without the
        former (see chart_adapters.adapt()'s documented limitation)."""
        fabricated = {'authority': 'LOCAL_OBJECTS_VERIFIED', 'commit': '0' * 40}
        self.write_hook(HOOK_ORIGINAL.replace(b'echo ok', b'echo mutated'))
        with self.assertRaisesRegex(ValueError, 'stale'):
            prepare(self.workspace, 'chart', self.manifest, helm='helm', source_evidence=fabricated)

    def test_unreviewed_chart_identity_remains_unchanged_regardless_of_evidence(self):
        """Even picture-perfect evidence cannot force adaptation onto a
        chart identity with no registered adapter entry at all."""
        files = {'Chart.yaml': b'name: some-other-chart\nversion: 9.9.9\n'}
        manifest = dict(self.manifest, source={'kind': 'local-git', 'path': 'chart', 'commit': '0' * 40})
        adapted, provenance = chart_adapters.adapt(files, manifest, self.evidence)
        self.assertEqual(adapted, files)
        self.assertEqual(provenance, [])


SUBCHART_META = b'apiVersion: v2\nname: redis-cache\nversion: 1.0.0\n'
SUBCHART_DEPLOYMENT = (
    b'apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: {{ .Release.Name }}-redis-cache\n'
    b'spec:\n  replicas: 1\n  selector:\n    matchLabels:\n      app: redis-cache\n'
    b'  template:\n    metadata:\n      labels:\n        app: redis-cache\n'
    b'    spec:\n      automountServiceAccountToken: false\n      containers:\n'
    b'        - name: redis-cache\n          image: example.invalid/research/redis-cache:v1.0.0\n'
    b'          securityContext:\n            runAsNonRoot: true\n            allowPrivilegeEscalation: false\n'
    b'            capabilities:\n              drop: ["ALL"]\n'
    b'          resources:\n            limits:\n              cpu: 50m\n              memory: 64Mi\n'
)
PARENT_CHART_WITH_DEPENDENCY = (
    b'apiVersion: v2\nname: multi-nf\n'
    b'description: Synthetic multi-NF fixture chart for schema_version=2 verification tests\n'
    b'version: 0.1.0\nappVersion: "1.0"\ndependencies:\n  - name: redis-cache\n    version: 1.0.0\n'
)
PARENT_CHART_NO_DEPENDENCY = (
    b'apiVersion: v2\nname: multi-nf\n'
    b'description: Synthetic multi-NF fixture chart for schema_version=2 verification tests\n'
    b'version: 0.1.0\nappVersion: "1.0"\n'
)
REDIS_IMAGE_ENTRY = {
    'identity': 'redis-cache', 'kind': 'supporting-container', 'role': None, 'purpose': 'database',
    'repository': 'example.invalid/research/redis-cache', 'tag_pin': 'v1.0.0',
    'digest': 'sha256:' + 'c' * 64, 'platform': 'linux/amd64',
}


class VendoredSubchartTests(unittest.TestCase):
    """Synthetic vendored Helm dependency (charts/<name>/...) tests, modeled
    on how a real free5GC-style chart bundles subcharts under charts/ --
    never touching, fetching, or asserting anything about the real upstream
    chart, which exists only on the Dell and was not accessed from here."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Path(self.tmp.name)
        shutil.copytree(CHART, self.workspace / 'chart')
        (self.workspace / 'chart/Chart.yaml').write_bytes(PARENT_CHART_WITH_DEPENDENCY)
        manifest = json.loads(json.dumps(MANIFEST))
        manifest['source'] = {'kind': 'local-git', 'path': 'chart', 'commit': 'f' * 40}
        manifest['images'] = manifest['images'] + [REDIS_IMAGE_ENTRY]
        self.manifest = validate(manifest)

    def write_subchart(self, root='chart/charts/redis-cache', meta=SUBCHART_META, deployment=SUBCHART_DEPLOYMENT):
        sub = self.workspace / root
        (sub / 'templates').mkdir(parents=True)
        (sub / 'Chart.yaml').write_bytes(meta)
        (sub / 'templates/deployment.yaml').write_bytes(deployment)

    def digests(self, extra=()):
        base = {'charts/redis-cache/Chart.yaml': hashlib.sha256(SUBCHART_META).hexdigest(),
                'charts/redis-cache/templates/deployment.yaml': hashlib.sha256(SUBCHART_DEPLOYMENT).hexdigest()}
        base.update(extra)
        return base

    def test_vendored_subchart_with_exact_reviewed_inventory_accepted(self):
        self.write_subchart()
        result = prepare(self.workspace, 'chart', self.manifest, helm='helm', vendored_dependencies=self.digests())
        self.assertIn('redis-cache', result['image_discovery'])
        self.assertTrue(all(o['hook'] is False for o in result['image_discovery']['redis-cache']['occurrences']))

    def test_vendored_subchart_undeclared_dependency_rejected(self):
        """Vendored on disk, but the parent Chart.yaml never declares it."""
        self.write_subchart()
        (self.workspace / 'chart/Chart.yaml').write_bytes(PARENT_CHART_NO_DEPENDENCY)
        with self.assertRaisesRegex(ValueError, 'not declared as a dependency'):
            prepare(self.workspace, 'chart', self.manifest, helm='helm', vendored_dependencies=self.digests())

    def test_declared_dependency_not_vendored_at_all_rejected(self):
        """Parent declares redis-cache, but charts/ has nothing at all."""
        with self.assertRaisesRegex(ValueError, 'is not vendored locally'):
            prepare(self.workspace, 'chart', self.manifest, helm='helm', vendored_dependencies={})

    def test_missing_reviewed_inventory_rejected(self):
        self.write_subchart()
        with self.assertRaisesRegex(ValueError, 'explicit reviewed inventory'):
            prepare(self.workspace, 'chart', self.manifest, helm='helm')

    def test_unreviewed_dependency_content_change_rejected(self):
        self.write_subchart()
        drifted = self.digests()
        (self.workspace / 'chart/charts/redis-cache/templates/deployment.yaml').write_bytes(
            SUBCHART_DEPLOYMENT.replace(b'v1.0.0', b'v9.9.9'))
        with self.assertRaisesRegex(ValueError, 'Unreviewed vendored dependency content change'):
            prepare(self.workspace, 'chart', self.manifest, helm='helm', vendored_dependencies=drifted)

    def test_extra_undeclared_vendored_file_rejected(self):
        """A file present on disk but absent from the reviewed inventory --
        the inventory must match the chart exactly, not just be a subset."""
        self.write_subchart()
        (self.workspace / 'chart/charts/redis-cache/templates/extra.yaml').write_text(
            'apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: extra\n')
        with self.assertRaisesRegex(ValueError, 'does not match the chart exactly'):
            prepare(self.workspace, 'chart', self.manifest, helm='helm', vendored_dependencies=self.digests())

    def test_subchart_directory_name_disagrees_with_chart_yaml_rejected(self):
        self.write_subchart(root='chart/charts/not-redis-cache')
        digests = {'charts/not-redis-cache/Chart.yaml': hashlib.sha256(SUBCHART_META).hexdigest(),
                   'charts/not-redis-cache/templates/deployment.yaml': hashlib.sha256(SUBCHART_DEPLOYMENT).hexdigest()}
        with self.assertRaisesRegex(ValueError, 'directory name disagrees'):
            prepare(self.workspace, 'chart', self.manifest, helm='helm', vendored_dependencies=digests)

    def test_malformed_subchart_chart_yaml_rejected(self):
        bad_meta = b'apiVersion: v2\n'  # no name/version
        self.write_subchart(meta=bad_meta)
        digests = {'charts/redis-cache/Chart.yaml': hashlib.sha256(bad_meta).hexdigest(),
                   'charts/redis-cache/templates/deployment.yaml': hashlib.sha256(SUBCHART_DEPLOYMENT).hexdigest()}
        with self.assertRaisesRegex(ValueError, 'Invalid vendored subchart metadata'):
            prepare(self.workspace, 'chart', self.manifest, helm='helm', vendored_dependencies=digests)

    def test_embedded_private_key_in_vendored_subchart_rejected(self):
        self.write_subchart()
        keyed = b'# -----BEGIN RSA PRIVATE KEY-----\n# fake\n# -----END RSA PRIVATE KEY-----\n'
        (self.workspace / 'chart/charts/redis-cache/templates/keyed.yaml').write_bytes(keyed)
        digests = self.digests({'charts/redis-cache/templates/keyed.yaml': hashlib.sha256(keyed).hexdigest()})
        with self.assertRaisesRegex(ValueError, 'Private key material'):
            prepare(self.workspace, 'chart', self.manifest, helm='helm', vendored_dependencies=digests)

    def test_unapproved_file_type_in_vendored_subchart_rejected(self):
        self.write_subchart()
        script = b'#!/bin/sh\necho hi\n'
        (self.workspace / 'chart/charts/redis-cache/templates/setup.sh').write_bytes(script)
        digests = self.digests({'charts/redis-cache/templates/setup.sh': hashlib.sha256(script).hexdigest()})
        with self.assertRaisesRegex(ValueError, 'Unexpected vendored dependency input'):
            prepare(self.workspace, 'chart', self.manifest, helm='helm', vendored_dependencies=digests)

    def test_inline_credential_in_vendored_values_rejected(self):
        self.write_subchart()
        values = b'password: hardcoded-secret\n'
        (self.workspace / 'chart/charts/redis-cache/values.yaml').write_bytes(values)
        digests = self.digests({'charts/redis-cache/values.yaml': hashlib.sha256(values).hexdigest()})
        with self.assertRaises(ValueError):
            prepare(self.workspace, 'chart', self.manifest, helm='helm', vendored_dependencies=digests)

    def test_nested_template_image_still_requires_manifest_declaration(self):
        """Proves nested (vendored subchart) templates get the SAME
        image-declaration check as top-level ones: redis-cache is vendored
        and declared as a dependency, but deliberately NOT added to
        manifest['images'] -- multi_image.verify() still catches it."""
        self.write_subchart()
        manifest = json.loads(json.dumps(MANIFEST))
        manifest['source'] = {'kind': 'local-git', 'path': 'chart', 'commit': 'f' * 40}
        # REDIS_IMAGE_ENTRY deliberately omitted.
        manifest = validate(manifest)
        with self.assertRaisesRegex(ValueError, 'Undeclared or mismatched rendered image'):
            prepare(self.workspace, 'chart', manifest, helm='helm', vendored_dependencies=self.digests())

    def test_nested_template_unsafe_pod_security_still_rejected(self):
        """Proves nested templates get the SAME pod-security bar as
        top-level ones: this subchart's Deployment omits
        automountServiceAccountToken entirely."""
        unsafe_deployment = SUBCHART_DEPLOYMENT.replace(b'      automountServiceAccountToken: false\n', b'')
        self.write_subchart(deployment=unsafe_deployment)
        digests = {'charts/redis-cache/Chart.yaml': hashlib.sha256(SUBCHART_META).hexdigest(),
                   'charts/redis-cache/templates/deployment.yaml': hashlib.sha256(unsafe_deployment).hexdigest()}
        with self.assertRaisesRegex(ValueError, 'service account token'):
            prepare(self.workspace, 'chart', self.manifest, helm='helm', vendored_dependencies=digests)

    def test_nested_helm_hook_with_undeclared_image_rejected(self):
        """Proves a Helm hook template vendored inside a subchart gets the
        SAME hook scrutiny as a top-level one -- never silently skipped."""
        hook = (b'apiVersion: v1\nkind: Pod\nmetadata:\n  name: {{ .Release.Name }}-redis-check\n'
                b'  annotations:\n    helm.sh/hook: test\nspec:\n  automountServiceAccountToken: false\n'
                b'  containers:\n    - name: check\n      image: busybox\n')
        self.write_subchart()
        (self.workspace / 'chart/charts/redis-cache/templates/hook.yaml').write_bytes(hook)
        digests = self.digests({'charts/redis-cache/templates/hook.yaml': hashlib.sha256(hook).hexdigest()})
        with self.assertRaisesRegex(ValueError, 'Undeclared or mismatched rendered image'):
            prepare(self.workspace, 'chart', self.manifest, helm='helm', vendored_dependencies=digests)

    def test_nested_undeclared_init_container_rejected(self):
        """Proves an init container inside a vendored subchart template gets
        the SAME image-declaration check as a top-level init container --
        not just regular containers."""
        deployment_with_init = SUBCHART_DEPLOYMENT.replace(
            b'      containers:\n',
            b'      initContainers:\n'
            b'        - name: wait-for-db\n          image: attacker.invalid/payload:v1\n'
            b'          securityContext:\n            runAsNonRoot: true\n'
            b'            allowPrivilegeEscalation: false\n            capabilities:\n              drop: ["ALL"]\n'
            b'          resources:\n            limits:\n              cpu: 50m\n              memory: 32Mi\n'
            b'      containers:\n',
        )
        self.write_subchart(deployment=deployment_with_init)
        digests = {'charts/redis-cache/Chart.yaml': hashlib.sha256(SUBCHART_META).hexdigest(),
                   'charts/redis-cache/templates/deployment.yaml': hashlib.sha256(deployment_with_init).hexdigest()}
        with self.assertRaisesRegex(ValueError, 'Undeclared or mismatched rendered image'):
            prepare(self.workspace, 'chart', self.manifest, helm='helm', vendored_dependencies=digests)

    def test_nested_forbidden_kind_rejected(self):
        """Proves a forbidden resource kind (e.g. a Secret) rendered from a
        vendored subchart template is rejected exactly like one rendered
        from a top-level template -- the forbidden-kind check in
        multi_image.verify() has no separate, weaker path for subcharts."""
        # Named deliberately NOT to start with "secret" -- that filename
        # heuristic (shared with safe_chart_inputs(), see
        # _relative_sensitive_or_unapproved) would reject it one check
        # earlier, before ever reaching the KIND-based check this test
        # means to isolate.
        extra = (b'apiVersion: v1\nkind: Secret\nmetadata:\n  name: {{ .Release.Name }}-redis-extra\n'
                 b'type: Opaque\ndata:\n  x: eA==\n')
        self.write_subchart()
        (self.workspace / 'chart/charts/redis-cache/templates/extra-resource.yaml').write_bytes(extra)
        digests = self.digests({'charts/redis-cache/templates/extra-resource.yaml': hashlib.sha256(extra).hexdigest()})
        with self.assertRaisesRegex(ValueError, 'Forbidden rendered resource kind'):
            prepare(self.workspace, 'chart', self.manifest, helm='helm', vendored_dependencies=digests)

    def test_nesting_depth_exceeds_supported_bound_rejected(self):
        """charts/<a>/charts/<b>/charts/<c>/ -- three levels deep -- exceeds
        MAX_SUBCHART_DEPTH and is rejected outright, not silently traversed."""
        inner_meta = b'apiVersion: v2\nname: inner\nversion: 1.0.0\n'
        files = {
            'Chart.yaml': PARENT_CHART_WITH_DEPENDENCY,
            'charts/redis-cache/Chart.yaml': (
                b'apiVersion: v2\nname: redis-cache\nversion: 1.0.0\n'
                b'dependencies:\n  - name: middle\n    version: 1.0.0\n'),
            'charts/redis-cache/charts/middle/Chart.yaml': (
                b'apiVersion: v2\nname: middle\nversion: 1.0.0\n'
                b'dependencies:\n  - name: inner\n    version: 1.0.0\n'),
            'charts/redis-cache/charts/middle/charts/inner/Chart.yaml': inner_meta,
        }
        parent_meta = {'name': 'multi-nf', 'version': '0.1.0',
                        'dependencies': [{'name': 'redis-cache', 'version': '1.0.0'}]}
        reviewed = {n: hashlib.sha256(v).hexdigest() for n, v in files.items() if n != 'Chart.yaml'}
        with self.assertRaisesRegex(ValueError, 'exceeds the supported depth'):
            subcharts.validate(files, parent_meta, reviewed)

    def test_two_level_nesting_within_bound_accepted_by_validator(self):
        """charts/<a>/charts/<b>/ -- exactly MAX_SUBCHART_DEPTH -- is
        accepted by the validator itself (a direct unit test, since
        constructing a real two-level-nested Helm chart that `helm
        lint --strict` also accepts is beyond what this synthetic fixture
        needs to prove: the dependency-relationship/digest-pin logic is the
        part specific to this change, and it is exercised end-to-end at
        depth 1 by every other test in this class)."""
        files = {
            'Chart.yaml': PARENT_CHART_WITH_DEPENDENCY,
            'charts/redis-cache/Chart.yaml': (
                b'apiVersion: v2\nname: redis-cache\nversion: 1.0.0\n'
                b'dependencies:\n  - name: exporter\n    version: 2.0.0\n'),
            'charts/redis-cache/templates/deployment.yaml': SUBCHART_DEPLOYMENT,
            'charts/redis-cache/charts/exporter/Chart.yaml': b'apiVersion: v2\nname: exporter\nversion: 2.0.0\n',
            'charts/redis-cache/charts/exporter/templates/deployment.yaml': b'kind: ConfigMap\n',
        }
        parent_meta = {'name': 'multi-nf', 'version': '0.1.0',
                        'dependencies': [{'name': 'redis-cache', 'version': '1.0.0'}]}
        reviewed = {n: hashlib.sha256(v).hexdigest() for n, v in files.items() if n != 'Chart.yaml'}
        subcharts.validate(files, parent_meta, reviewed)  # must not raise

    def test_v1_chart_with_vendored_subchart_directory_still_rejected(self):
        """Regression guard: vendored dependency support is schema_version=2
        ONLY. A v1 chart with charts/ content must still be rejected by
        safe_chart_inputs()'s existing path allowlist, exactly as before
        this feature existed -- never silently exempted."""
        v1 = validate(json.loads((ROOT / 'examples/telecom/ran.json').read_text()))
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        workspace = Path(tmp.name)
        shutil.copytree(ROOT / 'examples/telecom/ran-chart', workspace / 'chart')
        (workspace / 'chart/charts/sub').mkdir(parents=True)
        (workspace / 'chart/charts/sub/Chart.yaml').write_text('apiVersion: v2\nname: sub\nversion: 1.0.0\n')
        with self.assertRaises(ValueError):
            prepare(workspace, 'chart', v1, helm='helm')

    def test_packaged_tgz_dependency_rejected(self):
        """PHASE 2 (Option B): a packaged archive dependency -- how the real
        free5GC chart vendors MongoDB -- is rejected outright, never
        opened or inspected, with a clear, specific reason."""
        (self.workspace / 'chart/charts').mkdir(parents=True)
        (self.workspace / 'chart/charts/mongodb-1.0.0.tgz').write_bytes(b'not a real archive')
        with self.assertRaisesRegex(ValueError, 'Packaged Helm dependency archives'):
            prepare(self.workspace, 'chart', self.manifest, helm='helm', vendored_dependencies={})

    def test_nested_packaged_archive_rejected(self):
        """The same rejection applies to an archive nested inside an
        otherwise-legitimate vendored subchart directory, not just a
        top-level charts/*.tgz."""
        self.write_subchart()
        (self.workspace / 'chart/charts/redis-cache/charts').mkdir(parents=True)
        (self.workspace / 'chart/charts/redis-cache/charts/nested.tar.gz').write_bytes(b'not a real archive')
        digests = self.digests({'charts/redis-cache/charts/nested.tar.gz': hashlib.sha256(b'not a real archive').hexdigest()})
        with self.assertRaisesRegex(ValueError, 'Packaged Helm dependency archives'):
            prepare(self.workspace, 'chart', self.manifest, helm='helm', vendored_dependencies=digests)


class TrustedDependencyRegistryTests(unittest.TestCase):
    """PHASE 3: subcharts.TRUSTED_DEPENDENCIES is the operator-reviewed
    registry (in-repo, code-reviewed, exactly like chart_adapters.ADAPTERS)
    -- gated by source_evidence proving independently verified acquisition,
    never by a caller merely asserting matching hashes. Keyed to a
    synthetic commit ('3'*40) that cannot collide with any real git object
    id; no real free5GC entry exists."""

    TRUSTED_COMMIT = '3' * 40

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Path(self.tmp.name)
        shutil.copytree(CHART, self.workspace / 'chart')
        (self.workspace / 'chart/Chart.yaml').write_bytes(PARENT_CHART_WITH_DEPENDENCY)
        (self.workspace / 'chart/charts/redis-cache/templates').mkdir(parents=True)
        (self.workspace / 'chart/charts/redis-cache/Chart.yaml').write_bytes(SUBCHART_META)
        (self.workspace / 'chart/charts/redis-cache/templates/deployment.yaml').write_bytes(SUBCHART_DEPLOYMENT)
        manifest = json.loads(json.dumps(MANIFEST))
        manifest['source'] = {'kind': 'local-git', 'path': 'chart', 'commit': self.TRUSTED_COMMIT}
        manifest['images'] = manifest['images'] + [REDIS_IMAGE_ENTRY]
        self.manifest = validate(manifest)
        self.evidence = {'authority': 'LOCAL_OBJECTS_VERIFIED', 'commit': self.TRUSTED_COMMIT}

    def test_trusted_registry_resolved_automatically_with_verified_evidence(self):
        """No vendored_dependencies passed at all -- the trusted, in-repo
        registry is resolved automatically once source_evidence proves this
        exact commit was really acquired."""
        result = prepare(self.workspace, 'chart', self.manifest, helm='helm', source_evidence=self.evidence)
        self.assertIn('redis-cache', result['image_discovery'])

    def test_trusted_registry_requires_verified_evidence_not_just_a_commit_match(self):
        """The chart identity + commit matches the registry, but no
        source_evidence is supplied at all -- rejected, not silently
        trusted just because the lookup key matched."""
        with self.assertRaisesRegex(ValueError, 'requires independently verified source'):
            prepare(self.workspace, 'chart', self.manifest, helm='helm')

    def test_trusted_registry_rejects_declaration_only_evidence(self):
        declared = {'authority': 'PROVIDER_DECLARED', 'commit': self.TRUSTED_COMMIT}
        with self.assertRaisesRegex(ValueError, 'requires independently verified source'):
            prepare(self.workspace, 'chart', self.manifest, helm='helm', source_evidence=declared)

    def test_trusted_registry_rejects_evidence_commit_mismatch(self):
        mismatched = {'authority': 'LOCAL_OBJECTS_VERIFIED', 'commit': '9' * 40}
        with self.assertRaisesRegex(ValueError, 'requires independently verified source'):
            prepare(self.workspace, 'chart', self.manifest, helm='helm', source_evidence=mismatched)

    def test_caller_supplied_inventory_matching_trusted_registry_accepted(self):
        matching = dict(subcharts.TRUSTED_DEPENDENCIES[('multi-nf', '0.1.0', self.TRUSTED_COMMIT)])
        result = prepare(self.workspace, 'chart', self.manifest, helm='helm',
                          source_evidence=self.evidence, vendored_dependencies=matching)
        self.assertIn('redis-cache', result['image_discovery'])

    def test_caller_supplied_inventory_conflicting_with_trusted_registry_rejected(self):
        """A researcher-provided hash list is never accepted as operator
        approval -- not even when it otherwise looks plausible and the
        evidence is genuinely verified -- if it disagrees with the
        trusted, reviewed registry for this exact chart identity."""
        conflicting = dict(subcharts.TRUSTED_DEPENDENCIES[('multi-nf', '0.1.0', self.TRUSTED_COMMIT)])
        conflicting['charts/redis-cache/Chart.yaml'] = 'f' * 64
        with self.assertRaisesRegex(ValueError, 'conflicts with the trusted, reviewed registry'):
            prepare(self.workspace, 'chart', self.manifest, helm='helm',
                    source_evidence=self.evidence, vendored_dependencies=conflicting)

    def test_unregistered_chart_identity_falls_back_to_consistency_only_path(self):
        """A different commit has no trusted registry entry at all --
        falls back to today's existing, lower-trust, caller-supplied-
        inventory path (still requires an explicit, internally-consistent
        dict; not silently approved just because evidence looks verified)."""
        manifest = copy.deepcopy(dict(self.manifest))
        manifest['source'] = {'kind': 'local-git', 'path': 'chart', 'commit': '4' * 40}
        evidence = {'authority': 'LOCAL_OBJECTS_VERIFIED', 'commit': '4' * 40}
        with self.assertRaisesRegex(ValueError, 'explicit reviewed inventory'):
            prepare(self.workspace, 'chart', manifest, helm='helm', source_evidence=evidence)


PAD_TEMPLATE = 'apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: {{{{ .Release.Name }}}}-pad-{index}\ndata:\n  x: "1"\n'


class ChartProfileTests(unittest.TestCase):
    """The large chart-capture profile is opt-in only: a caller must
    explicitly request chart_profile='large'. Default-profile behavior
    (today's exact budgets: 128 files/4MiB/256 entries/depth 8) is
    unaffected for every caller that doesn't ask for the larger one."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Path(self.tmp.name)
        shutil.copytree(CHART, self.workspace / 'chart')
        self.manifest = validate(json.loads(json.dumps(MANIFEST)))

    def pad(self, count):
        for i in range(count):
            (self.workspace / f'chart/templates/pad-{i}.yaml').write_text(PAD_TEMPLATE.format(index=i))

    def test_oversized_chart_rejected_under_default_profile(self):
        """9 base files + 150 padding files (159 total) exceeds the default
        128-file cap -- and the large profile was never requested."""
        self.pad(150)
        with self.assertRaisesRegex(ValueError, 'budget exceeded'):
            prepare(self.workspace, 'chart', self.manifest, helm='helm')

    def test_oversized_chart_accepted_under_large_profile(self):
        """The identical chart succeeds once the large profile is
        explicitly requested -- proving it is the SAME chart, not a
        different, more lenient validation path."""
        self.pad(150)
        result = prepare(self.workspace, 'chart', self.manifest, helm='helm', chart_profile='large')
        self.assertTrue(result['helm_validation']['outcome'], 'PASS')

    def test_large_profile_still_enforces_its_own_file_count_cap(self):
        """chart_profile='large' is a bound (512 files), not 'unlimited' --
        600 padding files exceeds even the large cap."""
        self.pad(600)
        with self.assertRaisesRegex(ValueError, 'budget exceeded'):
            prepare(self.workspace, 'chart', self.manifest, helm='helm', chart_profile='large')

    def test_large_profile_rejected_for_schema_v1(self):
        v1 = validate(json.loads((ROOT / 'examples/telecom/ran.json').read_text()))
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        workspace = Path(tmp.name)
        shutil.copytree(ROOT / 'examples/telecom/ran-chart', workspace / 'chart')
        with self.assertRaisesRegex(ValueError, 'only accepted for multi-image manifests'):
            prepare(workspace, 'chart', v1, helm='helm', chart_profile='large')

    def test_unknown_chart_profile_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Unknown chart capture profile'):
            prepare(self.workspace, 'chart', self.manifest, helm='helm', chart_profile='gigantic')

    def test_default_profile_is_the_implicit_default(self):
        """Not passing chart_profile at all behaves identically to passing
        'default' explicitly -- no silent change for any existing caller."""
        self.pad(150)
        with self.assertRaisesRegex(ValueError, 'budget exceeded'):
            prepare(self.workspace, 'chart', self.manifest, helm='helm', chart_profile='default')


LEGACY_MONGO_CONTENT = b'not-a-real-archive-synthetic-fixture-for-removal-tests\n'


class ChartAdapterRemovalTests(unittest.TestCase):
    """PHASE 1: operator-controlled, source-verified file removal in
    chart_adapters.py -- e.g. dropping a redundant packaged MongoDB
    dependency archive (see the pipeline design report, "MongoDB
    dependencies"). Keyed to a synthetic commit ('5'*40) that cannot
    collide with any real git object id; no real free5GC entry exists."""

    REMOVAL_COMMIT = '5' * 40

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Path(self.tmp.name)
        shutil.copytree(CHART, self.workspace / 'chart')
        manifest = json.loads(json.dumps(MANIFEST))
        manifest['source'] = {'kind': 'local-git', 'path': 'chart', 'commit': self.REMOVAL_COMMIT}
        self.manifest = validate(manifest)
        self.evidence = {'authority': 'LOCAL_OBJECTS_VERIFIED', 'commit': self.REMOVAL_COMMIT}

    def write_archive(self, content=LEGACY_MONGO_CONTENT):
        (self.workspace / 'chart/charts').mkdir(parents=True, exist_ok=True)
        (self.workspace / 'chart/charts/legacy-mongodb-4.2.1.tgz').write_bytes(content)

    def base_files(self, include_archive=True):
        files = {name: (self.workspace / 'chart' / name).read_bytes() for name in ('Chart.yaml', 'values.yaml')}
        if include_archive:
            files['charts/legacy-mongodb-4.2.1.tgz'] = LEGACY_MONGO_CONTENT
        return files

    def test_correct_removal_applied_with_full_provenance(self):
        files = self.base_files()
        adapted, provenance = chart_adapters.adapt(files, self.manifest, self.evidence)
        self.assertNotIn('charts/legacy-mongodb-4.2.1.tgz', adapted)
        self.assertEqual(provenance, [{
            'action': 'remove', 'path': 'charts/legacy-mongodb-4.2.1.tgz',
            'original_sha256': hashlib.sha256(LEGACY_MONGO_CONTENT).hexdigest(),
            'reason': chart_adapters.REMOVALS[('multi-nf', '0.1.0', self.REMOVAL_COMMIT)]
                      ['charts/legacy-mongodb-4.2.1.tgz']['reason'],
        }])
        self.assertIn('charts/legacy-mongodb-4.2.1.tgz', files)  # original dict never mutated

    def test_removal_end_to_end_bypasses_archive_rejection(self):
        """Proves removal happens BEFORE subcharts.validate()'s Option-B
        archive check -- the chart packages successfully even though it
        contained a .tgz, because it was removed first, never silently
        exempted from the archive rule."""
        self.write_archive()
        result = prepare(self.workspace, 'chart', self.manifest, helm='helm', source_evidence=self.evidence)
        self.assertEqual([p['path'] for p in result['adapted_files']], ['charts/legacy-mongodb-4.2.1.tgz'])
        self.assertEqual(result['adapted_files'][0]['action'], 'remove')
        self.assertEqual(result['adaptation_provenance']['removed_files'], result['adapted_files'])

    def test_wrong_source_commit_no_removal_still_rejected_by_archive_check(self):
        """A different commit has no matching REMOVALS entry -- the
        archive is left untouched, and still trips subcharts.py's
        unconditional Option-B rejection; never silently removed for an
        unreviewed commit."""
        self.write_archive()
        manifest = copy.deepcopy(dict(self.manifest))
        manifest['source'] = {'kind': 'local-git', 'path': 'chart', 'commit': '6' * 40}
        with self.assertRaisesRegex(ValueError, 'Packaged Helm dependency archives'):
            prepare(self.workspace, 'chart', manifest, helm='helm')

    def test_wrong_acquisition_authority_rejected(self):
        self.write_archive()
        declared = {'authority': 'PROVIDER_DECLARED', 'commit': self.REMOVAL_COMMIT}
        with self.assertRaisesRegex(ValueError, 'verified source acquisition evidence'):
            prepare(self.workspace, 'chart', self.manifest, helm='helm', source_evidence=declared)

    def test_source_content_drift_rejected(self):
        files = self.base_files()
        files['charts/legacy-mongodb-4.2.1.tgz'] = LEGACY_MONGO_CONTENT + b'tampered'
        with self.assertRaisesRegex(ValueError, 'Removal review is stale'):
            chart_adapters.adapt(files, self.manifest, self.evidence)

    def test_unapproved_removal_target_rejected(self):
        """There is no caller-defined deletion list: with NOTHING
        registered for this chart identity at all (registries cleared),
        an arbitrary extra file survives completely untouched -- adapt()
        has no parameter through which a caller could ask for it to be
        removed."""
        files = self.base_files(include_archive=False)
        files['templates/extra-unregistered.yaml'] = b'kind: ConfigMap\n'
        with mock.patch.dict(chart_adapters.ADAPTERS, {}, clear=True), \
                mock.patch.dict(chart_adapters.REMOVALS, {}, clear=True):
            adapted, provenance = chart_adapters.adapt(files, self.manifest, self.evidence)
        self.assertIn('templates/extra-unregistered.yaml', adapted)
        self.assertEqual(provenance, [])

    def test_unsupported_path_rejected_for_removal(self):
        """Removal targeting Chart.yaml/values.yaml is rejected even if a
        registry entry mistakenly names one -- exercised via
        mock.patch.dict with a deliberately bad entry, never via a
        caller-supplied removal-list argument (adapt() has none)."""
        files = self.base_files(include_archive=False)
        bad_key = ('multi-nf', '0.1.0', self.REMOVAL_COMMIT)
        bad_removals = {bad_key: {'values.yaml': {
            'original_sha256': hashlib.sha256(files['values.yaml']).hexdigest(),
            'reason': 'should never be allowed',
        }}}
        with mock.patch.dict(chart_adapters.REMOVALS, bad_removals, clear=True):
            with self.assertRaisesRegex(ValueError, 'may not target chart metadata/values'):
                chart_adapters.adapt(files, self.manifest, self.evidence)

    def test_missing_removal_target_file_rejected(self):
        """Once a chart identity has a REMOVALS entry naming a path, that
        path must actually be present to remove -- a stale/mistaken
        registry entry is a loud failure, never a silent no-op. (Package
        digest changing after adaptation is proven by
        UnifiedAdaptationProvenanceTests.test_package_digest_changes_after_adaptation.)"""
        files = self.base_files(include_archive=False)
        with self.assertRaisesRegex(ValueError, 'Removal target file missing'):
            chart_adapters.adapt(files, self.manifest, self.evidence)


class ChartAdapterValuesOverrideTests(unittest.TestCase):
    """PHASE 2a: narrow, source-verified values.yaml override contract --
    a FIXED set of already-existing key paths flipped to FIXED, reviewed
    scalar values. Keyed to the same synthetic commit ('5'*40) as the
    removal tests; no real free5GC entry exists."""

    OVERRIDE_COMMIT = '8' * 40

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Path(self.tmp.name)
        shutil.copytree(CHART, self.workspace / 'chart')
        manifest = json.loads(json.dumps(MANIFEST))
        manifest['source'] = {'kind': 'local-git', 'path': 'chart', 'commit': self.OVERRIDE_COMMIT}
        self.manifest = validate(manifest)
        self.evidence = {'authority': 'LOCAL_OBJECTS_VERIFIED', 'commit': self.OVERRIDE_COMMIT}
        self.files = {name: (self.workspace / 'chart' / name).read_bytes() for name in ('Chart.yaml', 'values.yaml')}

    def test_correct_override_applied_with_full_provenance(self):
        updated, provenance = chart_adapters.apply_values_overrides(self.files, self.manifest, self.evidence)
        parsed = yaml.safe_load(updated['values.yaml'])
        self.assertEqual(parsed['probe']['tag'], 'v1.0.1')
        registered = chart_adapters.VALUES_OVERRIDES[('multi-nf', '0.1.0', self.OVERRIDE_COMMIT)][0]['overrides']['probe.tag']
        self.assertEqual(provenance, [{'target_path': 'values.yaml', 'key_path': 'probe.tag', 'value': 'v1.0.1',
                                        'purpose': registered['purpose'], 'security_impact': registered['security_impact']}])
        # unrelated keys preserved
        self.assertEqual(parsed['amf']['tag'], 'v1.0.0')
        self.assertEqual(parsed['mongodb']['repository'], 'example.invalid/research/mongodb')

    def test_original_files_dict_never_mutated(self):
        original = dict(self.files)
        chart_adapters.apply_values_overrides(self.files, self.manifest, self.evidence)
        self.assertEqual(self.files, original)

    def test_wrong_source_commit_no_override_applied(self):
        manifest = copy.deepcopy(dict(self.manifest))
        manifest['source'] = {'kind': 'local-git', 'path': 'chart', 'commit': '6' * 40}
        updated, provenance = chart_adapters.apply_values_overrides(self.files, manifest, self.evidence)
        self.assertEqual(updated, self.files)
        self.assertEqual(provenance, [])

    def test_wrong_acquisition_authority_rejected(self):
        declared = {'authority': 'PROVIDER_DECLARED', 'commit': self.OVERRIDE_COMMIT}
        with self.assertRaisesRegex(ValueError, 'verified source acquisition evidence'):
            chart_adapters.apply_values_overrides(self.files, self.manifest, declared)

    def test_source_content_drift_rejected(self):
        drifted = dict(self.files)
        drifted['values.yaml'] = drifted['values.yaml'] + b'\nextra: true\n'
        with self.assertRaisesRegex(ValueError, 'values.yaml review is stale'):
            chart_adapters.apply_values_overrides(drifted, self.manifest, self.evidence)

    def test_unknown_values_key_rejected(self):
        """'mongodb.enabled' is not an existing key in this fixture's
        values.yaml -- exercised via mock.patch.dict with a deliberately
        bad registry entry, never a caller-supplied override."""
        key = ('multi-nf', '0.1.0', self.OVERRIDE_COMMIT)
        bad_entry = {key: [{'target_path': 'values.yaml',
                             'original_sha256': hashlib.sha256(self.files['values.yaml']).hexdigest(),
                             'overrides': {'mongodb.enabled': {'value': False, 'purpose': 'x', 'security_impact': 'x'}}}]}
        with mock.patch.dict(chart_adapters.VALUES_OVERRIDES, bad_entry, clear=True):
            with self.assertRaisesRegex(ValueError, 'does not exist in original values.yaml'):
                chart_adapters.apply_values_overrides(self.files, self.manifest, self.evidence)

    def test_non_scalar_override_value_rejected(self):
        """No arbitrary YAML edits: an override value that is a dict/list
        is rejected outright, never applied."""
        key = ('multi-nf', '0.1.0', self.OVERRIDE_COMMIT)
        bad_entry = {key: [{'target_path': 'values.yaml',
                             'original_sha256': hashlib.sha256(self.files['values.yaml']).hexdigest(),
                             'overrides': {'probe.tag': {'value': {'nested': 'structure'},
                                                          'purpose': 'x', 'security_impact': 'x'}}}]}
        with mock.patch.dict(chart_adapters.VALUES_OVERRIDES, bad_entry, clear=True):
            with self.assertRaisesRegex(ValueError, 'must be a scalar'):
                chart_adapters.apply_values_overrides(self.files, self.manifest, self.evidence)

    def test_override_result_is_deterministic(self):
        updated1, _ = chart_adapters.apply_values_overrides(self.files, self.manifest, self.evidence)
        updated2, _ = chart_adapters.apply_values_overrides(self.files, self.manifest, self.evidence)
        self.assertEqual(updated1['values.yaml'], updated2['values.yaml'])

    def test_duplicate_yaml_key_fails_closed(self):
        """safe_load() silently resolves a duplicate mapping key to
        whichever occurrence it sees last; this pipeline must not act on
        an ambiguity it cannot correctly resolve -- fails closed instead."""
        duplicated = b'probe:\n  tag: v1.0.0\nprobe:\n  tag: v2.0.0\n'
        key = ('multi-nf', '0.1.0', self.OVERRIDE_COMMIT)
        bad_entry = {key: [{'target_path': 'values.yaml',
                             'original_sha256': hashlib.sha256(duplicated).hexdigest(),
                             'overrides': {'probe.tag': {'value': 'v3.0.0', 'purpose': 'x', 'security_impact': 'x'}}}]}
        files = {'Chart.yaml': self.files['Chart.yaml'], 'values.yaml': duplicated}
        with mock.patch.dict(chart_adapters.VALUES_OVERRIDES, bad_entry, clear=True):
            with self.assertRaisesRegex(ValueError, 'Invalid values.yaml content'):
                chart_adapters.apply_values_overrides(files, self.manifest, self.evidence)

    def test_override_target_not_scalar_in_original_rejected(self):
        """The EXISTING value at the override path is a list, not a
        scalar -- rejected, never silently overwritten."""
        original = b'probe:\n  tag:\n    - v1.0.0\n    - v2.0.0\n'
        key = ('multi-nf', '0.1.0', self.OVERRIDE_COMMIT)
        bad_entry = {key: [{'target_path': 'values.yaml',
                             'original_sha256': hashlib.sha256(original).hexdigest(),
                             'overrides': {'probe.tag': {'value': 'v3.0.0', 'purpose': 'x', 'security_impact': 'x'}}}]}
        files = {'Chart.yaml': self.files['Chart.yaml'], 'values.yaml': original}
        with mock.patch.dict(chart_adapters.VALUES_OVERRIDES, bad_entry, clear=True):
            with self.assertRaisesRegex(ValueError, 'is not a scalar in original values.yaml'):
                chart_adapters.apply_values_overrides(files, self.manifest, self.evidence)

    def test_ambiguous_dotted_key_path_rejected(self):
        """A sibling key in the original values.yaml literally contains a
        '.' -- the dotted-path addressing scheme could be confused with
        that literal key, so the whole override is rejected rather than
        guessing which interpretation is correct."""
        original = b'probe:\n  tag: v1.0.0\n  "extra.key": true\n'
        key = ('multi-nf', '0.1.0', self.OVERRIDE_COMMIT)
        bad_entry = {key: [{'target_path': 'values.yaml',
                             'original_sha256': hashlib.sha256(original).hexdigest(),
                             'overrides': {'probe.tag': {'value': 'v3.0.0', 'purpose': 'x', 'security_impact': 'x'}}}]}
        files = {'Chart.yaml': self.files['Chart.yaml'], 'values.yaml': original}
        with mock.patch.dict(chart_adapters.VALUES_OVERRIDES, bad_entry, clear=True):
            with self.assertRaisesRegex(ValueError, 'Ambiguous dotted-key path'):
                chart_adapters.apply_values_overrides(files, self.manifest, self.evidence)


class ChartAdapterDependencyRemovalTests(unittest.TestCase):
    """PHASE 2b: narrow, source-verified Chart.yaml dependency-removal
    contract -- drops ONE named (name, version) entry from
    `dependencies:`, preserving every other field/dependency."""

    DEP_REMOVAL_COMMIT = '9' * 40

    def setUp(self):
        manifest = json.loads(json.dumps(MANIFEST))
        manifest['source'] = {'kind': 'local-git', 'path': 'chart', 'commit': self.DEP_REMOVAL_COMMIT}
        self.manifest = validate(manifest)
        self.evidence = {'authority': 'LOCAL_OBJECTS_VERIFIED', 'commit': self.DEP_REMOVAL_COMMIT}
        self.files = {'Chart.yaml': PARENT_CHART_WITH_DEPENDENCY}

    def test_correct_removal_applied_with_full_provenance(self):
        updated, provenance = chart_adapters.apply_dependency_removals(self.files, self.manifest, self.evidence)
        parsed = yaml.safe_load(updated['Chart.yaml'])
        self.assertEqual(parsed['dependencies'], [])
        self.assertEqual(parsed['name'], 'multi-nf')    # unrelated metadata preserved
        self.assertEqual(parsed['version'], '0.1.0')
        registered = chart_adapters.DEPENDENCY_REMOVALS[('multi-nf', '0.1.0', self.DEP_REMOVAL_COMMIT)][0]['removals'][0]
        self.assertEqual(provenance, [{'target_path': 'Chart.yaml', 'name': 'redis-cache', 'version': '1.0.0',
                                        'reason': registered['reason']}])

    def test_original_files_dict_never_mutated(self):
        original = dict(self.files)
        chart_adapters.apply_dependency_removals(self.files, self.manifest, self.evidence)
        self.assertEqual(self.files, original)

    def test_wrong_source_commit_no_removal_applied(self):
        manifest = copy.deepcopy(dict(self.manifest))
        manifest['source'] = {'kind': 'local-git', 'path': 'chart', 'commit': '6' * 40}
        updated, provenance = chart_adapters.apply_dependency_removals(self.files, manifest, self.evidence)
        self.assertEqual(updated, self.files)
        self.assertEqual(provenance, [])

    def test_wrong_acquisition_authority_rejected(self):
        declared = {'authority': 'PROVIDER_DECLARED', 'commit': self.DEP_REMOVAL_COMMIT}
        with self.assertRaisesRegex(ValueError, 'verified source acquisition evidence'):
            chart_adapters.apply_dependency_removals(self.files, self.manifest, declared)

    def test_source_content_drift_rejected(self):
        drifted = {'Chart.yaml': PARENT_CHART_WITH_DEPENDENCY + b'# tampered\n'}
        with self.assertRaisesRegex(ValueError, 'Chart.yaml review is stale'):
            chart_adapters.apply_dependency_removals(drifted, self.manifest, self.evidence)

    def test_dependency_identity_mismatch_rejected(self):
        """Registered to remove a name that Chart.yaml's dependencies:
        list does not actually declare -- raises, never a silent no-op."""
        key = ('multi-nf', '0.1.0', self.DEP_REMOVAL_COMMIT)
        bad_entry = {key: [{'target_path': 'Chart.yaml',
                             'original_sha256': hashlib.sha256(PARENT_CHART_WITH_DEPENDENCY).hexdigest(),
                             'removals': [{'name': 'other-cache', 'version': '1.0.0', 'reason': 'x'}]}]}
        with mock.patch.dict(chart_adapters.DEPENDENCY_REMOVALS, bad_entry, clear=True):
            with self.assertRaisesRegex(ValueError, 'Dependency removal target not found'):
                chart_adapters.apply_dependency_removals(self.files, self.manifest, self.evidence)

    def test_dependency_version_mismatch_rejected(self):
        """Right name, wrong version -- still not a match, still raises."""
        key = ('multi-nf', '0.1.0', self.DEP_REMOVAL_COMMIT)
        bad_entry = {key: [{'target_path': 'Chart.yaml',
                             'original_sha256': hashlib.sha256(PARENT_CHART_WITH_DEPENDENCY).hexdigest(),
                             'removals': [{'name': 'redis-cache', 'version': '9.9.9', 'reason': 'x'}]}]}
        with mock.patch.dict(chart_adapters.DEPENDENCY_REMOVALS, bad_entry, clear=True):
            with self.assertRaisesRegex(ValueError, 'Dependency removal target not found'):
                chart_adapters.apply_dependency_removals(self.files, self.manifest, self.evidence)

    def test_removal_result_is_deterministic(self):
        updated1, _ = chart_adapters.apply_dependency_removals(self.files, self.manifest, self.evidence)
        updated2, _ = chart_adapters.apply_dependency_removals(self.files, self.manifest, self.evidence)
        self.assertEqual(updated1['Chart.yaml'], updated2['Chart.yaml'])

    def test_duplicate_yaml_key_in_chart_yaml_fails_closed(self):
        duplicated = (b'apiVersion: v2\nname: multi-nf\nversion: 0.1.0\n'
                      b'dependencies:\n  - name: redis-cache\n    version: 1.0.0\n'
                      b'dependencies:\n  - name: other\n    version: 2.0.0\n')
        key = ('multi-nf', '0.1.0', self.DEP_REMOVAL_COMMIT)
        bad_entry = {key: [{'target_path': 'Chart.yaml',
                             'original_sha256': hashlib.sha256(duplicated).hexdigest(),
                             'removals': [{'name': 'redis-cache', 'version': '1.0.0', 'reason': 'x'}]}]}
        with mock.patch.dict(chart_adapters.DEPENDENCY_REMOVALS, bad_entry, clear=True):
            with self.assertRaisesRegex(ValueError, 'Invalid Chart.yaml content'):
                chart_adapters.apply_dependency_removals({'Chart.yaml': duplicated}, self.manifest, self.evidence)

    def test_malformed_dependency_entry_rejected(self):
        """A `dependencies:` list entry that is not a mapping at all (e.g.
        a bare string) is rejected cleanly, never a crash."""
        malformed = (b'apiVersion: v2\nname: multi-nf\nversion: 0.1.0\n'
                     b'dependencies:\n  - "just-a-string"\n')
        key = ('multi-nf', '0.1.0', self.DEP_REMOVAL_COMMIT)
        bad_entry = {key: [{'target_path': 'Chart.yaml',
                             'original_sha256': hashlib.sha256(malformed).hexdigest(),
                             'removals': [{'name': 'redis-cache', 'version': '1.0.0', 'reason': 'x'}]}]}
        with mock.patch.dict(chart_adapters.DEPENDENCY_REMOVALS, bad_entry, clear=True):
            with self.assertRaisesRegex(ValueError, 'Invalid Chart.yaml dependency entry'):
                chart_adapters.apply_dependency_removals({'Chart.yaml': malformed}, self.manifest, self.evidence)


REDIS_CHART_WITH_NESTED_DEP = (
    b'apiVersion: v2\nname: redis-cache\nversion: 1.0.0\n'
    b'dependencies:\n  - name: cache-backend\n    version: 1.0.0\n'
)
CACHE_BACKEND_ARCHIVE = b'synthetic-nested-packaged-dependency-for-subchart-scoped-removal-tests\n'
REDIS_VALUES = b'cacheMode: standalone\nmaxMemoryMb: 256\n'


class SubchartScopedAdaptationTests(unittest.TestCase):
    """Extends apply_values_overrides()/apply_dependency_removals() to
    target a vendored subchart's own values.yaml/Chart.yaml, not only the
    top-level chart's -- modeled directly on the real free5GC finding that
    NRF/UDR/WebUI each declare their OWN MongoDB dependency inside their
    OWN Chart.yaml, one level below the top-level free5gc chart. "redis-cache"
    here stands in for that pattern; "cache-backend" stands in for the
    vendored MongoDB archive. Keyed to synthetic commit ('a'*40); no real
    free5GC entry exists."""

    SUBCHART_COMMIT = 'a' * 40

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Path(self.tmp.name)
        shutil.copytree(CHART, self.workspace / 'chart')
        (self.workspace / 'chart/Chart.yaml').write_bytes(PARENT_CHART_WITH_DEPENDENCY)
        redis_root = self.workspace / 'chart/charts/redis-cache'
        (redis_root / 'templates').mkdir(parents=True)
        (redis_root / 'charts').mkdir(parents=True)
        (redis_root / 'Chart.yaml').write_bytes(REDIS_CHART_WITH_NESTED_DEP)
        (redis_root / 'values.yaml').write_bytes(REDIS_VALUES)
        (redis_root / 'templates/deployment.yaml').write_bytes(SUBCHART_DEPLOYMENT)
        (redis_root / 'charts/cache-backend-1.0.0.tgz').write_bytes(CACHE_BACKEND_ARCHIVE)
        manifest = json.loads(json.dumps(MANIFEST))
        manifest['source'] = {'kind': 'local-git', 'path': 'chart', 'commit': self.SUBCHART_COMMIT}
        manifest['images'] = manifest['images'] + [REDIS_IMAGE_ENTRY]
        self.manifest = validate(manifest)
        self.evidence = {'authority': 'LOCAL_OBJECTS_VERIFIED', 'commit': self.SUBCHART_COMMIT}

    def read_files(self, extra=()):
        names = ['Chart.yaml', 'values.yaml', 'charts/redis-cache/Chart.yaml', 'charts/redis-cache/values.yaml',
                 'charts/redis-cache/templates/deployment.yaml', 'charts/redis-cache/charts/cache-backend-1.0.0.tgz']
        files = {name: (self.workspace / 'chart' / name).read_bytes() for name in names}
        files.update(extra)
        return files

    def vendored_dependencies_after_adaptation(self):
        """redis-cache itself remains a (now smaller) vendored subchart of
        the top-level chart after adaptation -- separately, and
        pre-existing from an earlier milestone, subcharts.py still
        requires an explicit reviewed inventory for whatever vendored
        content remains. Computed here from the SAME chart_adapters
        functions prepare() calls internally, so it exactly matches what
        prepare() will independently produce."""
        files = self.read_files()
        files, _ = chart_adapters.adapt(files, self.manifest, self.evidence)
        files, _ = chart_adapters.apply_values_overrides(files, self.manifest, self.evidence)
        files, _ = chart_adapters.apply_dependency_removals(files, self.manifest, self.evidence)
        return {n: hashlib.sha256(v).hexdigest() for n, v in files.items() if n.startswith('charts/')}

    def test_subchart_scoped_values_override_and_dependency_removal_end_to_end(self):
        """Full package.prepare() run: a subchart-scoped values override,
        a subchart-scoped dependency removal, and the paired nested-archive
        removal all compose correctly, in the right order, and
        subcharts.validate() sees a fully consistent post-adaptation state
        (dependency/archive consistency)."""
        result = prepare(self.workspace, 'chart', self.manifest, helm='helm', source_evidence=self.evidence,
                          vendored_dependencies=self.vendored_dependencies_after_adaptation())
        provenance = result['adaptation_provenance']
        self.assertEqual(provenance['values_overrides_applied'],
                          [{'target_path': 'charts/redis-cache/values.yaml', 'key_path': 'cacheMode',
                            'value': 'cluster',
                            'purpose': chart_adapters.VALUES_OVERRIDES[('multi-nf', '0.1.0', self.SUBCHART_COMMIT)]
                                       [0]['overrides']['cacheMode']['purpose'],
                            'security_impact': chart_adapters.VALUES_OVERRIDES[
                                ('multi-nf', '0.1.0', self.SUBCHART_COMMIT)][0]['overrides']['cacheMode'][
                                'security_impact']}])
        self.assertEqual([d['target_path'] for d in provenance['dependency_declarations_removed']],
                          ['charts/redis-cache/Chart.yaml'])
        self.assertEqual([p['path'] for p in provenance['removed_files']],
                          ['charts/redis-cache/charts/cache-backend-1.0.0.tgz'])
        self.assertIn('redis-cache', result['image_discovery'])

    def test_post_adaptation_digest_reflects_nested_modified_files(self):
        """chart_digest is computed (by package.prepare(), using the exact
        same formula reproduced here) from the FULLY adapted file set,
        including the nested subchart files this round's mechanism
        touches -- not just the top-level ones."""
        root = self.workspace / 'chart'
        raw_files = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
        raw_digest = 'sha256:' + identity({n: hashlib.sha256(v).hexdigest() for n, v in raw_files.items()})
        result = prepare(self.workspace, 'chart', self.manifest, helm='helm', source_evidence=self.evidence,
                          vendored_dependencies=self.vendored_dependencies_after_adaptation())
        self.assertNotEqual(result['chart_digest'], raw_digest)

    def test_wrong_target_path_rejected(self):
        for bad_path in ('/etc/values.yaml', '../values.yaml', 'charts/redis-cache/secrets.yaml',
                          'charts/../values.yaml', 'charts/redis-cache/charts/a/charts/b/values.yaml'):
            key = ('multi-nf', '0.1.0', self.SUBCHART_COMMIT)
            bad_entry = {key: [{'target_path': bad_path, 'original_sha256': 'f' * 64,
                                 'overrides': {'x': {'value': 1, 'purpose': 'x', 'security_impact': 'x'}}}]}
            with self.subTest(bad_path=bad_path), mock.patch.dict(chart_adapters.VALUES_OVERRIDES, bad_entry, clear=True):
                with self.assertRaisesRegex(ValueError, 'Unsupported or unreviewed adaptation target path'):
                    chart_adapters.apply_values_overrides(self.read_files(), self.manifest, self.evidence)

    def test_bounded_nesting_depth_exactly_two_accepted(self):
        self.assertEqual(chart_adapters._validate_target_path('charts/redis-cache/charts/x/values.yaml'),
                          'charts/redis-cache/charts/x/values.yaml')

    def test_missing_subchart_target_rejected(self):
        files = self.read_files()
        del files['charts/redis-cache/values.yaml']
        with self.assertRaisesRegex(ValueError, 'Values override target file missing'):
            chart_adapters.apply_values_overrides(files, self.manifest, self.evidence)

    def test_nested_duplicate_yaml_key_fails_closed(self):
        duplicated = b'cacheMode: a\ncacheMode: b\n'
        key = ('multi-nf', '0.1.0', self.SUBCHART_COMMIT)
        bad_entry = {key: [{'target_path': 'charts/redis-cache/values.yaml',
                             'original_sha256': hashlib.sha256(duplicated).hexdigest(),
                             'overrides': {'cacheMode': {'value': 'c', 'purpose': 'x', 'security_impact': 'x'}}}]}
        files = self.read_files({'charts/redis-cache/values.yaml': duplicated})
        with mock.patch.dict(chart_adapters.VALUES_OVERRIDES, bad_entry, clear=True):
            with self.assertRaisesRegex(ValueError, 'Invalid charts/redis-cache/values.yaml content'):
                chart_adapters.apply_values_overrides(files, self.manifest, self.evidence)

    def test_nested_ambiguous_dotted_key_path_rejected(self):
        original = b'cacheMode: standalone\n"extra.key": true\n'
        key = ('multi-nf', '0.1.0', self.SUBCHART_COMMIT)
        bad_entry = {key: [{'target_path': 'charts/redis-cache/values.yaml',
                             'original_sha256': hashlib.sha256(original).hexdigest(),
                             'overrides': {'cacheMode': {'value': 'c', 'purpose': 'x', 'security_impact': 'x'}}}]}
        files = self.read_files({'charts/redis-cache/values.yaml': original})
        with mock.patch.dict(chart_adapters.VALUES_OVERRIDES, bad_entry, clear=True):
            with self.assertRaisesRegex(ValueError, 'Ambiguous dotted-key path'):
                chart_adapters.apply_values_overrides(files, self.manifest, self.evidence)

    def test_duplicate_target_path_in_values_override_registry_rejected(self):
        key = ('multi-nf', '0.1.0', self.SUBCHART_COMMIT)
        bad_entry = {key: [
            {'target_path': 'charts/redis-cache/values.yaml',
             'original_sha256': hashlib.sha256(REDIS_VALUES).hexdigest(),
             'overrides': {'cacheMode': {'value': 'a', 'purpose': 'x', 'security_impact': 'x'}}},
            {'target_path': 'charts/redis-cache/values.yaml',
             'original_sha256': hashlib.sha256(REDIS_VALUES).hexdigest(),
             'overrides': {'maxMemoryMb': {'value': 512, 'purpose': 'x', 'security_impact': 'x'}}},
        ]}
        with mock.patch.dict(chart_adapters.VALUES_OVERRIDES, bad_entry, clear=True):
            with self.assertRaisesRegex(ValueError, 'Duplicate values-override target path'):
                chart_adapters.apply_values_overrides(self.read_files(), self.manifest, self.evidence)

    def test_duplicate_dependency_identity_in_registry_rejected(self):
        key = ('multi-nf', '0.1.0', self.SUBCHART_COMMIT)
        bad_entry = {key: [{'target_path': 'charts/redis-cache/Chart.yaml',
                             'original_sha256': hashlib.sha256(REDIS_CHART_WITH_NESTED_DEP).hexdigest(),
                             'removals': [{'name': 'cache-backend', 'version': '1.0.0', 'reason': 'x'},
                                          {'name': 'cache-backend', 'version': '1.0.0', 'reason': 'y'}]}]}
        with mock.patch.dict(chart_adapters.DEPENDENCY_REMOVALS, bad_entry, clear=True):
            with self.assertRaisesRegex(ValueError, 'Duplicate dependency removal identity'):
                chart_adapters.apply_dependency_removals(self.read_files(), self.manifest, self.evidence)

    def test_ambiguous_dependency_identity_in_actual_file_rejected(self):
        """The ACTUAL target file (not the registry) declares the same
        (name, version) dependency twice -- ambiguous, rejected rather
        than silently removing just one occurrence."""
        ambiguous = (b'apiVersion: v2\nname: redis-cache\nversion: 1.0.0\n'
                     b'dependencies:\n  - name: cache-backend\n    version: 1.0.0\n'
                     b'  - name: cache-backend\n    version: 1.0.0\n')
        key = ('multi-nf', '0.1.0', self.SUBCHART_COMMIT)
        bad_entry = {key: [{'target_path': 'charts/redis-cache/Chart.yaml',
                             'original_sha256': hashlib.sha256(ambiguous).hexdigest(),
                             'removals': [{'name': 'cache-backend', 'version': '1.0.0', 'reason': 'x'}]}]}
        files = self.read_files({'charts/redis-cache/Chart.yaml': ambiguous})
        with mock.patch.dict(chart_adapters.DEPENDENCY_REMOVALS, bad_entry, clear=True):
            with self.assertRaisesRegex(ValueError, 'Ambiguous dependency identity'):
                chart_adapters.apply_dependency_removals(files, self.manifest, self.evidence)

    def test_duplicate_target_path_in_dependency_removal_registry_rejected(self):
        key = ('multi-nf', '0.1.0', self.SUBCHART_COMMIT)
        bad_entry = {key: [
            {'target_path': 'charts/redis-cache/Chart.yaml',
             'original_sha256': hashlib.sha256(REDIS_CHART_WITH_NESTED_DEP).hexdigest(),
             'removals': [{'name': 'cache-backend', 'version': '1.0.0', 'reason': 'x'}]},
            {'target_path': 'charts/redis-cache/Chart.yaml',
             'original_sha256': hashlib.sha256(REDIS_CHART_WITH_NESTED_DEP).hexdigest(),
             'removals': [{'name': 'cache-backend', 'version': '1.0.0', 'reason': 'y'}]},
        ]}
        with mock.patch.dict(chart_adapters.DEPENDENCY_REMOVALS, bad_entry, clear=True):
            with self.assertRaisesRegex(ValueError, 'Duplicate dependency-removal target path'):
                chart_adapters.apply_dependency_removals(self.read_files(), self.manifest, self.evidence)

    def test_subchart_target_original_sha_mismatch_rejected(self):
        """Upstream drift in the SUBCHART file (not the top-level chart)
        must fail closed exactly like top-level drift does."""
        files = self.read_files()
        files['charts/redis-cache/values.yaml'] = files['charts/redis-cache/values.yaml'] + b'\nextra: true\n'
        with self.assertRaisesRegex(ValueError, 'charts/redis-cache/values.yaml review is stale'):
            chart_adapters.apply_values_overrides(files, self.manifest, self.evidence)

    def test_subchart_wrong_source_commit_no_adaptation_applied(self):
        """A chart identity mismatch (wrong source commit) silently
        applies no subchart-scoped adaptation at all -- not an error,
        just an absent match, exactly mirroring the top-level behavior."""
        manifest = copy.deepcopy(dict(self.manifest))
        manifest['source'] = {'kind': 'local-git', 'path': 'chart', 'commit': '6' * 40}
        files = self.read_files()
        updated, provenance = chart_adapters.apply_values_overrides(files, manifest, self.evidence)
        self.assertEqual(updated, files)
        self.assertEqual(provenance, [])
        updated, provenance = chart_adapters.apply_dependency_removals(files, manifest, self.evidence)
        self.assertEqual(updated, files)
        self.assertEqual(provenance, [])

    def test_subchart_unverified_source_evidence_rejected(self):
        """A declared-but-unverified acquisition authority is rejected for
        subchart-scoped targets exactly like it is for top-level ones."""
        declared = {'authority': 'PROVIDER_DECLARED', 'commit': self.SUBCHART_COMMIT}
        with self.assertRaisesRegex(ValueError, 'verified source acquisition evidence'):
            chart_adapters.apply_values_overrides(self.read_files(), self.manifest, declared)
        with self.assertRaisesRegex(ValueError, 'verified source acquisition evidence'):
            chart_adapters.apply_dependency_removals(self.read_files(), self.manifest, declared)

    def test_missing_archive_removal_rejected_even_with_dependency_declaration_removed(self):
        """The paired REMOVALS entry is the ONLY thing that deletes the
        vendored packaged archive -- if it is absent, subcharts.py's
        unconditional Option B rejection fires regardless of the
        Chart.yaml dependency-declaration removal having been correctly
        applied. One of the two reviewed operations being missing must
        still fail closed, never package a partially-adapted chart."""
        with mock.patch.dict(chart_adapters.REMOVALS, {}, clear=True):
            vendored = self.vendored_dependencies_after_adaptation()
            with self.assertRaisesRegex(ValueError, 'Packaged Helm dependency archives'):
                prepare(self.workspace, 'chart', self.manifest, helm='helm', source_evidence=self.evidence,
                        vendored_dependencies=vendored)

    def test_missing_dependency_declaration_removal_rejected_even_with_archive_removed(self):
        """The paired DEPENDENCY_REMOVALS entry is the ONLY thing that
        drops the Chart.yaml declaration -- if it is absent,
        subcharts.validate() catches the resulting declared-but-not-
        vendored inconsistency even though the archive itself was
        correctly removed, so a half-applied adaptation pair still fails
        closed rather than packaging successfully."""
        with mock.patch.dict(chart_adapters.DEPENDENCY_REMOVALS, {}, clear=True):
            vendored = self.vendored_dependencies_after_adaptation()
            with self.assertRaisesRegex(ValueError, 'Declared dependency is not vendored locally'):
                prepare(self.workspace, 'chart', self.manifest, helm='helm', source_evidence=self.evidence,
                        vendored_dependencies=vendored)


class RealFree5gcMongoDBRegistryTests(unittest.TestCase):
    """Registry-metadata regression guard for the first real, source-bound
    free5GC entries (github.com/free5gc/free5gc-helm, commit
    d22485f5a95fd6bb97ec1252f0933ef5da17f96d, chart charts/free5gc v1.1.7).
    Never embeds real upstream file bytes -- the actual chart was fetched
    directly from the public upstream repository and independently
    verified byte-for-byte against the supplied hash inventory during
    review, but nothing from it is vendored into this repo. This class
    only guards the REVIEWED REGISTRY's own structure, hashes and commit
    binding, plus (via synthetic, non-upstream fixtures) that
    `version: None` matches ONLY a dependency declaration that truly
    omits the version field, never a wildcard."""

    KEY = ('free5gc', '1.1.7', 'd22485f5a95fd6bb97ec1252f0933ef5da17f96d')
    ARCHIVE_SHA = 'd71d7c67505a6f8fcfe4808e89966a5b99f649f0b11318653e993beacdaa6998'
    NRF_CHART_SHA = '4f6cb9e59632688957e9aca5c878a5dba1afa80d729dd0064086bbfd7c555983'
    UDR_CHART_SHA = 'dc93bd47fa07d8a4a816d3aa2e543e2ffeaf894c41e7a249ff7e079fe98e3727'
    WEBUI_CHART_SHA = 'a7c56c5b0816d411db832a63bd03e2023d86ee4570948e00be7fd8600c1fb590'

    def test_removals_entries_exact_paths_and_hashes(self):
        entry = chart_adapters.REMOVALS[self.KEY]
        self.assertEqual(set(entry.keys()), {
            'charts/free5gc-nrf/charts/mongodb-15.6.0.tgz',
            'charts/free5gc-udr/charts/mongodb-15.6.0.tgz',
            'charts/free5gc-webui/charts/mongodb-15.6.0.tgz',
        })
        for path, e in entry.items():
            self.assertEqual(e['original_sha256'], self.ARCHIVE_SHA)
            self.assertTrue(e['reason'])

    def test_dependency_removals_entries_exact_targets_hashes_and_version_none(self):
        entries = chart_adapters.DEPENDENCY_REMOVALS[self.KEY]
        by_target = {e['target_path']: e for e in entries}
        self.assertEqual(set(by_target), {
            'charts/free5gc-nrf/Chart.yaml',
            'charts/free5gc-udr/Chart.yaml',
            'charts/free5gc-webui/Chart.yaml',
        })
        expected_sha = {
            'charts/free5gc-nrf/Chart.yaml': self.NRF_CHART_SHA,
            'charts/free5gc-udr/Chart.yaml': self.UDR_CHART_SHA,
            'charts/free5gc-webui/Chart.yaml': self.WEBUI_CHART_SHA,
        }
        for target_path, e in by_target.items():
            self.assertEqual(e['original_sha256'], expected_sha[target_path])
            self.assertEqual(len(e['removals']), 1)
            self.assertEqual(e['removals'][0]['name'], 'mongodb')
            self.assertIsNone(e['removals'][0]['version'])
            self.assertTrue(e['removals'][0]['reason'])

    def test_real_target_paths_pass_bounded_validation(self):
        for target_path in ('charts/free5gc-nrf/Chart.yaml', 'charts/free5gc-udr/Chart.yaml',
                             'charts/free5gc-webui/Chart.yaml'):
            self.assertEqual(chart_adapters._validate_target_path(target_path), target_path)

    def test_parent_mongodb_dependency_untouched(self):
        """The PARENT chart's own top-level mongodb dependency
        (condition: deployMongoDb) is a separate, already-provisioned
        shared instance -- never targeted by either registry for this
        chart identity."""
        self.assertNotIn('Chart.yaml', chart_adapters.REMOVALS.get(self.KEY, {}))
        dep_targets = {e['target_path'] for e in chart_adapters.DEPENDENCY_REMOVALS.get(self.KEY, [])}
        self.assertNotIn('Chart.yaml', dep_targets)
        self.assertNotIn('values.yaml', dep_targets)

    def test_no_image_template_or_values_adaptation_registered(self):
        """TLS, NF images, UPF and Helm hooks are unchanged this
        milestone: no ADAPTERS (whole-file template substitution) and no
        VALUES_OVERRIDES entry exists for this chart identity."""
        self.assertNotIn(self.KEY, chart_adapters.ADAPTERS)
        self.assertNotIn(self.KEY, chart_adapters.VALUES_OVERRIDES)

    def test_version_none_matches_only_a_dependency_missing_the_version_field(self):
        """Synthetic, non-upstream fixture: 'version': None is an exact
        match for a dependency declaration that truly omits `version:`,
        reproducing the real free5GC NF Chart.yaml shape structurally
        without embedding any real upstream bytes."""
        no_version = (b'apiVersion: v2\nname: synthetic-version-check\nversion: 0.0.1\n'
                      b'dependencies:\n  - name: mongodb\n    condition: db.enabled\n')
        key = ('synthetic-version-check', '0.0.1', 'c' * 40)
        entry = {key: [{'target_path': 'Chart.yaml',
                         'original_sha256': hashlib.sha256(no_version).hexdigest(),
                         'removals': [{'name': 'mongodb', 'version': None, 'reason': 'x'}]}]}
        manifest = {'schema_version': 2, 'source': {'kind': 'local-git', 'path': 'chart', 'commit': 'c' * 40}}
        evidence = {'authority': 'LOCAL_OBJECTS_VERIFIED', 'commit': 'c' * 40}
        files = {'Chart.yaml': no_version}
        with mock.patch.dict(chart_adapters.DEPENDENCY_REMOVALS, entry, clear=True):
            updated, provenance = chart_adapters.apply_dependency_removals(files, manifest, evidence)
        self.assertEqual(yaml.safe_load(updated['Chart.yaml'])['dependencies'], [])
        self.assertEqual(provenance, [{'target_path': 'Chart.yaml', 'name': 'mongodb', 'version': None,
                                        'reason': 'x'}])

    def test_version_none_does_not_match_a_dependency_that_declares_a_real_version(self):
        """'version': None must never act as a wildcard: a dependency
        entry that DOES declare an explicit version is not matched, and
        the stale-registry-entry guard fires instead of silently
        removing the wrong thing."""
        with_version = (b'apiVersion: v2\nname: synthetic-version-check\nversion: 0.0.1\n'
                        b'dependencies:\n  - name: mongodb\n    version: 15.6.0\n    condition: db.enabled\n')
        key = ('synthetic-version-check', '0.0.1', 'c' * 40)
        entry = {key: [{'target_path': 'Chart.yaml',
                         'original_sha256': hashlib.sha256(with_version).hexdigest(),
                         'removals': [{'name': 'mongodb', 'version': None, 'reason': 'x'}]}]}
        manifest = {'schema_version': 2, 'source': {'kind': 'local-git', 'path': 'chart', 'commit': 'c' * 40}}
        evidence = {'authority': 'LOCAL_OBJECTS_VERIFIED', 'commit': 'c' * 40}
        files = {'Chart.yaml': with_version}
        with mock.patch.dict(chart_adapters.DEPENDENCY_REMOVALS, entry, clear=True):
            with self.assertRaisesRegex(ValueError, 'Dependency removal target not found'):
                chart_adapters.apply_dependency_removals(files, manifest, evidence)


class UnifiedAdaptationProvenanceTests(unittest.TestCase):
    """PHASE 3: package.prepare()'s unified adaptation_provenance record.
    Reuses the hook-adapter identity ('multi-nf', '0.1.0', '0'*40) from
    ChartAdapterTests, since it is the one chart identity in this file
    with a REAL end-to-end adapted-and-packaged positive path."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Path(self.tmp.name)
        shutil.copytree(CHART, self.workspace / 'chart')
        manifest = json.loads(json.dumps(MANIFEST))
        manifest['source'] = {'kind': 'local-git', 'path': 'chart', 'commit': '0' * 40}
        manifest['images'] = manifest['images'] + [BUSYBOX_IMAGE_ENTRY]
        self.manifest = validate(manifest)
        self.evidence = {'authority': 'LOCAL_OBJECTS_VERIFIED', 'commit': '0' * 40}
        (self.workspace / 'chart/templates/test-hook.yaml').write_bytes(HOOK_ORIGINAL)

    def test_provenance_present_and_complete_for_v2(self):
        result = prepare(self.workspace, 'chart', self.manifest, helm='helm', source_evidence=self.evidence)
        provenance = result['adaptation_provenance']
        self.assertEqual(provenance['source_commit'], '0' * 40)
        self.assertEqual(provenance['chart_name'], 'multi-nf')
        self.assertEqual(provenance['chart_version'], '0.1.0')
        self.assertEqual(provenance['adapter_registry_key'], ('multi-nf', '0.1.0', '0' * 40))
        self.assertIsNone(provenance['values_override_registry_key'])
        self.assertIsNone(provenance['dependency_removal_registry_key'])
        self.assertIsNone(provenance['dependency_inventory_registry_key'])
        self.assertEqual([m['path'] for m in provenance['modified_files']], ['templates/test-hook.yaml'])
        self.assertEqual(provenance['removed_files'], [])
        self.assertEqual(provenance['values_overrides_applied'], [])
        self.assertEqual(provenance['dependency_declarations_removed'], [])
        self.assertEqual(provenance['chart_digest'], result['chart_digest'])
        self.assertEqual(provenance['chart_profile'], 'default')
        self.assertEqual(provenance['validation_results'],
                          {'safe_chart_inputs': 'PASS', 'subcharts_validate': 'PASS', 'multi_image_verify': 'PASS'})

    def test_provenance_reflects_caller_supplied_only_dependency_path(self):
        """When a trusted registry entry is NOT used (the lower-trust,
        caller-supplied-consistency-only path from an earlier milestone),
        provenance honestly records that with None, not a fabricated key."""
        sub = self.workspace / 'chart/charts/redis-cache/templates'
        sub.mkdir(parents=True)
        (self.workspace / 'chart/charts/redis-cache/Chart.yaml').write_bytes(SUBCHART_META)
        (self.workspace / 'chart/charts/redis-cache/templates/deployment.yaml').write_bytes(SUBCHART_DEPLOYMENT)
        manifest = json.loads(json.dumps(MANIFEST))
        manifest['source'] = {'kind': 'local-git', 'path': 'chart', 'commit': '0' * 40}
        manifest['images'] = manifest['images'] + [BUSYBOX_IMAGE_ENTRY, REDIS_IMAGE_ENTRY]
        (self.workspace / 'chart/Chart.yaml').write_bytes(
            b'apiVersion: v2\nname: multi-nf\n'
            b'description: Synthetic multi-NF fixture chart for schema_version=2 verification tests\n'
            b'version: 0.1.0\nappVersion: "1.0"\ndependencies:\n  - name: redis-cache\n    version: 1.0.0\n')
        manifest = validate(manifest)
        digests = {'charts/redis-cache/Chart.yaml': hashlib.sha256(SUBCHART_META).hexdigest(),
                   'charts/redis-cache/templates/deployment.yaml': hashlib.sha256(SUBCHART_DEPLOYMENT).hexdigest()}
        result = prepare(self.workspace, 'chart', manifest, helm='helm', source_evidence=self.evidence,
                          vendored_dependencies=digests)
        self.assertIsNone(result['adaptation_provenance']['dependency_inventory_registry_key'])

    def test_provenance_absent_adaptation_for_unregistered_identity(self):
        """A chart identity with no registered adapter at all still gets a
        complete, honestly-empty provenance record -- never omitted."""
        manifest = copy.deepcopy(dict(self.manifest))
        manifest['source'] = {'kind': 'local-git', 'path': 'chart', 'commit': '7' * 40}
        shutil.rmtree(self.workspace / 'chart/templates/test-hook.yaml', ignore_errors=True)
        (self.workspace / 'chart/templates/test-hook.yaml').unlink()
        result = prepare(self.workspace, 'chart', manifest, helm='helm')
        provenance = result['adaptation_provenance']
        self.assertIsNone(provenance['adapter_registry_key'])
        self.assertEqual(provenance['modified_files'], [])
        self.assertEqual(provenance['removed_files'], [])

    def test_package_digest_changes_after_adaptation(self):
        """The SAME upstream content, with vs. without the hook adapter
        matching (different commit => no match), produces a DIFFERENT
        chart_digest -- the adapted artifact is provably distinct from
        the raw upstream tree."""
        unadapted_manifest = copy.deepcopy(dict(self.manifest))
        unadapted_manifest['source'] = {'kind': 'local-git', 'path': 'chart', 'commit': '7' * 40}
        (self.workspace / 'chart/templates/test-hook.yaml').unlink()
        unadapted_manifest['images'] = [e for e in unadapted_manifest['images'] if e['identity'] != 'busybox-test']
        unadapted = prepare(self.workspace, 'chart', unadapted_manifest, helm='helm')

        self.setUp()  # fresh workspace with the hook present again
        adapted = prepare(self.workspace, 'chart', self.manifest, helm='helm', source_evidence=self.evidence)
        self.assertNotEqual(unadapted['chart_digest'], adapted['chart_digest'])

    def test_provenance_deterministic_across_repeated_preparation(self):
        result1 = prepare(self.workspace, 'chart', self.manifest, helm='helm', source_evidence=self.evidence)
        result2 = prepare(self.workspace, 'chart', self.manifest, helm='helm', source_evidence=self.evidence)
        self.assertEqual(result1['adaptation_provenance'], result2['adaptation_provenance'])

    def test_v1_return_shape_still_includes_empty_provenance(self):
        """Schema v1's return dict is unaffected: adaptation_provenance is
        present (uniform shape, like adapted_files already was) but every
        field correctly reflects that nothing v2-specific ran."""
        v1 = validate(json.loads((ROOT / 'examples/telecom/ran.json').read_text()))
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        workspace = Path(tmp.name)
        shutil.copytree(ROOT / 'examples/telecom/ran-chart', workspace / 'chart')
        result = prepare(workspace, 'chart', v1, helm='helm')
        provenance = result['adaptation_provenance']
        self.assertIsNone(provenance['adapter_registry_key'])
        self.assertIsNone(provenance['dependency_inventory_registry_key'])
        self.assertEqual(provenance['modified_files'], [])
        self.assertEqual(provenance['removed_files'], [])
        self.assertEqual(provenance['validation_results']['subcharts_validate'], 'NOT_APPLICABLE')


if __name__ == '__main__':
    unittest.main()
