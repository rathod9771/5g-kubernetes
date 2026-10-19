"""Discovery/verification of rendered multi-image Helm output (schema_version=2).

Offline only: operates on documents already produced by `helm template`
elsewhere (see package.py); never contacts a registry, a cluster, or Helm
itself, and never deploys anything. Enforces the exact same non-privileged
container bar the single-image MVP already enforces (package.py), with one
narrow, explicit escape hatch: a capability a manifest's
hardware.elevated_capabilities block names for a specific declared image
identity (see contract.py). Everything else that the single-image MVP
already forbids (hostNetwork/hostPID/hostIPC, implicit service-account-token
automount, custom service accounts, hostPath/secret volumes, hostPort
container bindings, Secret resources, missing resource limits, implicit
root, missing capability drop) stays forbidden here too; schema_version=2
only widens *which kinds of resources and container groups* are inspected,
never *how strict* a single container's security posture must be.

PersistentVolume is unconditionally forbidden (see FORBIDDEN_KINDS): unlike
a PersistentVolumeClaim, a PV is cluster-scoped and carries its own storage
backend directly in its spec (hostPath/nfs/csi/local/...), which this
offline verifier has no way to inspect or bound safely, so the whole kind
is rejected regardless of backend -- there is no bypass. PersistentVolumeClaim
is accepted but narrowly constrained (see _check_pvc): no explicit
storageClassName, since this offline, cluster-free verifier has no
mechanism to confirm a class was actually operator-approved, so any
explicit value fails closed rather than trusting an unverifiable name. A
pod's own `volumes[].persistentVolumeClaim.claimName` reference is resolved
against the exact set of PVC documents that passed _check_pvc() in the same
render (matched on namespace+name) -- an undeclared, mismatched, or
cross-namespace claim is rejected, not silently trusted as pre-existing
cluster state. StatefulSet `volumeClaimTemplates` are rejected outright:
they mint PVCs through a different binding path (volumeMounts, not
spec.volumes) that this verifier does not resolve, so it fails closed
rather than silently approving an unreviewed claim.
"""

import re

# Workload kinds whose pod template is walked for containers/init containers.
POD_TEMPLATE_KINDS = {'Deployment', 'StatefulSet', 'DaemonSet', 'Job', 'CronJob', 'Pod'}
# Non-workload kinds a multi-component package may legitimately render.
ACCEPTED_KINDS = POD_TEMPLATE_KINDS | {
    'Service', 'ConfigMap', 'ServiceAccount', 'PodDisruptionBudget', 'NetworkPolicy',
    'PersistentVolumeClaim',
}
# Never accepted, regardless of schema version or declared hardware mode:
# Secrets (existing MVP rule, unchanged), cluster networking changes (Multus
# NetworkAttachmentDefinition), cluster-wide RBAC escalation, and raw
# PersistentVolume objects (cluster-scoped, carry their own storage backend
# -- hostPath/nfs/csi/local/anything else -- entirely outside what this
# offline verifier can inspect; rejected by kind alone, no backend is ever
# approved).
FORBIDDEN_KINDS = {
    'Secret', 'NetworkAttachmentDefinition', 'ClusterRole', 'ClusterRoleBinding', 'Role', 'RoleBinding',
    'PersistentVolume',
}

# A PersistentVolumeClaim's spec may declare ONLY these fields: volumeName
# (binds to a specific, possibly attacker-planted PV), storageClassName (no
# offline mechanism to verify operator approval -- see module docstring),
# dataSource/dataSourceRef (clones from a snapshot or another PVC, a
# cross-tenant data path) and selector (binds to an arbitrary existing PV by
# label, same risk as volumeName) are all rejected by their absence from
# this allowlist, not by individually naming each one.
PVC_SPEC_FIELDS = {'accessModes', 'resources', 'volumeMode'}
ACCESS_MODES = {'ReadWriteOnce', 'ReadOnlyMany', 'ReadWriteMany', 'ReadWriteOncePod'}
STORAGE_QUANTITY = re.compile(r'([1-9][0-9]{0,15})(Ei|Pi|Ti|Gi|Mi|Ki|E|P|T|G|M|k)?')
STORAGE_MULTIPLIERS = {'Ki': 1024, 'Mi': 1024**2, 'Gi': 1024**3, 'Ti': 1024**4, 'Pi': 1024**5, 'Ei': 1024**6,
                        'k': 1000, 'M': 1000**2, 'G': 1000**3, 'T': 1000**4, 'P': 1000**5, 'E': 1000**6}
MIN_STORAGE_BYTES = 1024 * 1024            # 1Mi
MAX_STORAGE_BYTES = 2 * 1024 ** 4          # 2Ti


def _storage_bytes(quantity):
    match = STORAGE_QUANTITY.fullmatch(quantity) if isinstance(quantity, str) else None
    if not match:
        raise ValueError('Invalid storage quantity: ' + str(quantity))
    return int(match.group(1)) * STORAGE_MULTIPLIERS.get(match.group(2), 1)


def _check_pvc(document):
    metadata = document.get('metadata', {}) or {}
    if not isinstance(metadata.get('name'), str) or not metadata.get('name'):
        raise ValueError('PersistentVolumeClaim requires a name')
    if metadata.get('namespace', 'research-sandbox') != 'research-sandbox':
        raise ValueError('PersistentVolumeClaim namespace is not authorized')
    spec = document.get('spec', {}) or {}
    if not isinstance(spec, dict) or set(spec) - PVC_SPEC_FIELDS:
        raise ValueError('Unsupported PersistentVolumeClaim field requires review')
    modes = spec.get('accessModes')
    if not isinstance(modes, list) or not modes or any(mode not in ACCESS_MODES for mode in modes):
        raise ValueError('Invalid PersistentVolumeClaim access modes')
    if spec.get('volumeMode') not in (None, 'Filesystem'):
        raise ValueError('Only Filesystem volume mode is supported')
    resources = spec.get('resources')
    if not isinstance(resources, dict) or set(resources) != {'requests'}:
        raise ValueError('Bounded storage request required')
    requests = resources['requests']
    if not isinstance(requests, dict) or set(requests) != {'storage'}:
        raise ValueError('Bounded storage request required')
    if not MIN_STORAGE_BYTES <= _storage_bytes(requests['storage']) <= MAX_STORAGE_BYTES:
        raise ValueError('Storage request out of bounds')


# A pod's `persistentVolumeClaim` volume source may declare ONLY claimName
# (required) and readOnly (optional); any other field is rejected outright
# rather than silently passed through.
PVC_VOLUME_FIELDS = {'claimName', 'readOnly'}


def _check_pvc_reference(claim, namespace, approved_pvcs):
    if (not isinstance(claim, dict) or 'claimName' not in claim or set(claim) - PVC_VOLUME_FIELDS or
            ('readOnly' in claim and not isinstance(claim['readOnly'], bool))):
        raise ValueError('Malformed PersistentVolumeClaim volume reference')
    name = claim['claimName']
    if not isinstance(name, str) or not name:
        raise ValueError('Malformed PersistentVolumeClaim volume reference: empty claim name')
    if (namespace, name) not in approved_pvcs:
        # Covers a missing, undeclared, mismatched-name, or cross-namespace
        # claim identically -- there is no reviewed PVC with this exact
        # (namespace, name) pair in the same rendered package, so the pod
        # would be binding to storage this pipeline never inspected, which
        # may include a pre-existing, out-of-band PVC/PV in the real cluster.
        raise ValueError('Undeclared, mismatched, or cross-namespace PersistentVolumeClaim reference: ' + name)


def pod_spec(document):
    """Return a workload document's pod template spec, or None for non-workloads."""
    kind = document.get('kind')
    spec = document.get('spec', {}) or {}
    if kind == 'Pod':
        return spec
    if kind == 'CronJob':
        return spec.get('jobTemplate', {}).get('spec', {}).get('template', {}).get('spec', {}) or {}
    if kind in ('Deployment', 'StatefulSet', 'DaemonSet', 'Job'):
        return spec.get('template', {}).get('spec', {}) or {}
    return None


def _check_container(container, entry, elevated):
    identity = entry['identity']
    security = container.get('securityContext', {}) or {}
    added = set(security.get('capabilities', {}).get('add') or [])
    allowed_added = set(elevated.get(identity, []))
    if added - allowed_added:
        raise ValueError('Unapproved elevated capability for image: ' + identity)
    if security.get('privileged'):
        raise ValueError('Privileged containers are not supported by this pipeline: ' + identity)
    if security.get('allowPrivilegeEscalation') is not False:
        raise ValueError('Explicit nonprivileged container required: ' + identity)
    if security.get('runAsNonRoot') is not True:
        raise ValueError('Explicit nonprivileged container required: ' + identity)
    if security.get('capabilities', {}).get('drop') != ['ALL']:
        raise ValueError('Explicit capability drop required: ' + identity)
    if container.get('envFrom') or any('valueFrom' in v for v in (container.get('env') or [])):
        raise ValueError('Secret/environment references need separate review: ' + identity)
    if any(p.get('hostPort') for p in (container.get('ports') or [])):
        raise ValueError('Host port binding requires separate qualification: ' + identity)
    limits = container.get('resources', {}).get('limits', {}) if container.get('resources') else {}
    if not limits.get('cpu') or not limits.get('memory'):
        raise ValueError('Container resource limits required: ' + identity)


def verify(manifest, documents):
    """Compare rendered documents against manifest['images']; fail closed.

    A Helm test/lifecycle hook (`helm.sh/hook` annotation) still renders
    executable or cluster-mutating content, so it is never exempted from
    the forbidden/accepted-kind check, the pod-security bar or the
    declared-image check below -- only from the "every claimed role must be
    rendered" completeness requirement at the end, since a hook is
    transient, not the always-running package.

    Returns a per-identity discovery record for every declared image
    actually found in the render:
        {identity: {'kind': 'tag'|'digest', 'occurrences': [
            {'reference': <rendered image string>, 'kind': 'tag'|'digest',
             'hook': bool, 'document': [kind, name]}, ...]}}
    'kind' at the record level is 'tag' if ANY occurrence of that identity
    used a mutable repository:tag_pin reference rather than an immutable
    repository@sha256 reference -- callers (pipeline.verify_images) must
    never record such an identity as digest-pinned execution, since the
    rendered Kubernetes object itself does not pin content by digest.
    """
    images = manifest['images']
    by_tag = {}
    by_digest = {}
    for entry in images:
        by_tag[entry['repository'] + ':' + entry['tag_pin']] = entry
        by_digest[entry['repository'] + '@' + entry['digest']] = entry
    elevated = (manifest.get('hardware') or {}).get('elevated_capabilities') or {}

    # Pass 1: kind-level checks and the approved-PVC inventory. A pod that
    # references a PersistentVolumeClaim may be rendered before OR after the
    # PVC document itself in `helm template` output, so the inventory must
    # be complete before any pod's volumes are checked against it (pass 2).
    approved_pvcs = set()
    pod_documents = []
    for document in documents:
        if not isinstance(document, dict) or 'kind' not in document or 'metadata' not in document:
            raise ValueError('Invalid/empty rendered resource')
        kind = document['kind']
        metadata = document.get('metadata', {}) or {}
        if kind in FORBIDDEN_KINDS:
            raise ValueError('Forbidden rendered resource kind: ' + kind)
        if kind not in ACCEPTED_KINDS:
            raise ValueError('Unrecognized rendered resource kind requires review: ' + kind)
        if kind == 'PersistentVolumeClaim':
            _check_pvc(document)
            approved_pvcs.add((metadata.get('namespace', 'research-sandbox'), metadata.get('name')))
            continue
        if kind == 'StatefulSet' and (document.get('spec', {}) or {}).get('volumeClaimTemplates'):
            # volumeClaimTemplates mint PVCs per-replica outside this chart's
            # own rendered resources and are referenced by containers via
            # volumeMounts, not spec.volumes[].persistentVolumeClaim -- an
            # entirely different binding path this verifier does not resolve.
            # Fail closed rather than silently approve an unreviewed claim.
            raise ValueError('StatefulSet volumeClaimTemplates are not supported by this pipeline')
        if pod_spec(document) is not None:
            pod_documents.append(document)

    # Pass 2: pod-security checks, PVC-reference resolution and image discovery.
    discovery = {}
    for document in pod_documents:
        kind = document['kind']
        metadata = document.get('metadata', {}) or {}
        annotations = metadata.get('annotations', {}) or {}
        is_hook = bool(annotations.get('helm.sh/hook'))
        namespace = metadata.get('namespace', 'research-sandbox')
        spec = pod_spec(document)
        if any(spec.get(flag) for flag in ('hostNetwork', 'hostPID', 'hostIPC')):
            raise ValueError('Host namespace sharing requires separate cluster-networking qualification')
        if spec.get('automountServiceAccountToken') is not False:
            raise ValueError('Disable service account token mounting')
        if spec.get('serviceAccountName'):
            raise ValueError('Custom service accounts require separate qualification')
        for volume in (spec.get('volumes') or []):
            if not isinstance(volume, dict):
                raise ValueError('Malformed rendered volume')
            if 'hostPath' in volume or 'secret' in volume:
                raise ValueError('Host-path/secret volume mounts require separate qualification')
            if 'persistentVolumeClaim' in volume:
                _check_pvc_reference(volume['persistentVolumeClaim'], namespace, approved_pvcs)
        for group in ('initContainers', 'containers'):
            for container in (spec.get(group) or []):
                reference = container.get('image')
                entry = by_digest.get(reference) or by_tag.get(reference)
                if entry is None:
                    raise ValueError('Undeclared or mismatched rendered image: ' + str(reference))
                _check_container(container, entry, elevated)
                reference_kind = 'digest' if reference in by_digest else 'tag'
                record = discovery.setdefault(entry['identity'], {'kind': 'digest', 'occurrences': []})
                if reference_kind == 'tag':
                    record['kind'] = 'tag'
                record['occurrences'].append({
                    'reference': reference, 'kind': reference_kind, 'hook': is_hook,
                    'document': [kind, metadata.get('name')],
                })
    claimed_roles = set(manifest['roles'])
    missing = {e['role'] for e in images
               if e['kind'] == 'network-function' and e['role'] in claimed_roles and e['identity'] not in discovery}
    if missing:
        raise ValueError('Declared role was never rendered: ' + ', '.join(sorted(missing)))
    return discovery
