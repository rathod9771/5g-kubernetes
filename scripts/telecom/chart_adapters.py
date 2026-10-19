"""Reviewed, offline adaptation of specific upstream Helm chart files.

External RAN/Core charts (e.g. free5GC) sometimes hardcode a container image
or omit required pod-security settings in a Helm test/lifecycle hook
template, bypassing the chart's own `values.yaml`-driven image contract
entirely (a literal `image: busybox` line cannot be fixed by `--set` or a
values overlay, since the template never references `.Values` for it at
all). This module is NOT a general template engine and does not parse or
understand Helm templating -- every mechanism below performs a small number
of byte-exact, individually reviewed, source-controlled operations, each
scoped to an EXACT (chart name, chart version, source commit).

Three mechanisms, all sharing the same fail-closed discipline:
  1. `adapt()` -- whole-file TEMPLATE SUBSTITUTION (`templates/*` only,
     unchanged from before) and whole-file REMOVAL (`templates/*` or
     `charts/*`, e.g. a redundant packaged dependency archive; never
     `Chart.yaml`/`values.yaml`/`values.schema.json`). Registries: ADAPTERS
     (substitute), REMOVALS (remove).
  2. `apply_values_overrides()` -- a NARROW values.yaml (or a vendored
     subchart's own values.yaml) contract: flips a FIXED SET of
     already-existing, enumerated key paths to fixed, reviewed scalar
     values. Never introduces a new key, never accepts a dict/list value,
     never rewrites the file freely. Registry: VALUES_OVERRIDES.
  3. `apply_dependency_removals()` -- a NARROW Chart.yaml (or a vendored
     subchart's own Chart.yaml) contract: drops ONE OR MORE named
     (name, version) entries from `dependencies:`, leaving every other
     field and every other dependency byte-for-byte represented (modulo
     YAML re-serialization). Registry: DEPENDENCY_REMOVALS.

Mechanisms 2 and 3 each target an explicit, reviewed `target_path` per
registry entry -- the top-level `values.yaml`/`Chart.yaml` (the only shape
supported before this), or a bounded, vendored subchart's own
`charts/<name>/values.yaml`/`charts/<name>/Chart.yaml` (up to
_MAX_TARGET_DEPTH levels of nesting, matching subcharts.MAX_SUBCHART_DEPTH).
This exists because a real chart (e.g. free5GC) can declare a dependency
-- like a packaged MongoDB subchart -- inside a VENDORED SUBCHART's own
Chart.yaml, not the top-level one; removing only the vendored archive
without also reaching that subchart's own dependency declaration would
leave subcharts.validate()'s bidirectional check correctly rejecting the
package ("declared dependency is not vendored locally"). `target_path` is
validated against a strict allowlist (see `_validate_target_path`) --
never an absolute path, `../` traversal, or any filename other than
`values.yaml`/`Chart.yaml` at an allowed depth -- and is itself a
registry-authored field, never caller-supplied.

Mechanisms 2 and 3 parse and ACT on YAML structure (1 does not -- it never
inspects content, only digests). Plain `yaml.safe_load()` silently resolves
a duplicate mapping key to whichever occurrence comes last, with no error;
a Go-based renderer (e.g. the real `helm template`) could legitimately
disagree about which one wins. Rather than act on an ambiguity neither this
module nor the reviewer can correctly resolve, both functions use a
duplicate-key-detecting loader (`_strict_yaml_load`) and refuse an override
whose dotted key path could be confused with a literal, dotted key at the
same level (`_dotted_lookup`) -- see their docstrings.

Fail-closed properties shared by all three:
  * Opt-in per chart identity. A chart whose (name, version, commit) has no
    matching registry entry is returned byte-for-byte unchanged by every
    function here -- this pipeline never assumes an arbitrary third-party
    chart can be adapted automatically.
  * A registered operation only ever acts on a file whose CURRENT content
    matches the EXACT digest it was reviewed against. If the upstream file
    is missing, moved, or has changed at all, the operation raises rather
    than silently applying a stale review to different content.
  * The content involved (replacement bytes, override values, removal
    targets, dependency identities) is literal, final, and reviewed --
    checked into this module, never fetched, generated, derived from
    caller input, or templated.
  * NONE of this is exempt from anything downstream: the resulting file set
    still passes through the exact same, unweakened `safe_chart_inputs()`
    extension/path allowlist, `subcharts.validate()`, and
    `multi_image.verify()` security checks as any organically authored
    chart (see package.prepare()). Nothing here bypasses those checks; it
    only changes what the SAME checks see.
  * A chart/version/commit match alone is NEVER sufficient: every operation
    also requires `source_evidence` (threaded from pipeline.run(), which
    got it from execution.acquire()/helm_source() for THIS job) to show one
    of VERIFIED_ACQUISITION_AUTHORITIES and the exact matching commit --
    declaration-only evidence, or none at all, is rejected outright, even
    if a registry entry's (name, version, commit) key otherwise matches.
    There is NO caller-supplied removal list, override list, or dependency
    list anywhere in this module -- every one of those lives only in the
    in-repo registries below, reviewed through the normal code-review
    process (see adapt()'s docstring for the one honestly-disclosed
    residual limitation this does NOT close).

NOTE: no real free5GC entries ship in this change. The actual upstream
free5GC checkout exists only on the Dell workstation and was never fetched,
cloned, or inspected from this (HP) environment. Every registry below
contains only SYNTHETIC demonstration entries matching this repository's
own offline test fixtures (see tests/test_telecom_multi_image.py) -- none
of them are validated patches for any real upstream chart and none must be
read as one.
"""

import hashlib
import re

import yaml

# {(chart_name, chart_version, source_commit): {relative_template_path: {
#     'original_sha256': <exact upstream content this patch was reviewed against>,
#     'replacement': <final, literal, reviewed bytes>,
# }}}
ADAPTERS = {
    ('multi-nf', '0.1.0', '0' * 40): {
        # A synthetic reproduction of the documented free5GC defect --
        # hardcoded `image: busybox`, no pod-security settings, on a Helm
        # test hook -- used only by this repo's own offline test fixtures
        # (tests/test_telecom_multi_image.py: ChartAdapterTests). Never the
        # real upstream chart; this entry exists solely to demonstrate and
        # test the adaptation mechanism itself.
        'templates/test-hook.yaml': {
            'original_sha256': 'f31f1a86d6c732514ab63b7425e9794c5ee3e8c24fd7a7a387f9aaf772813b31',
            'replacement': (
                b'apiVersion: v1\n'
                b'kind: Pod\n'
                b'metadata:\n'
                b'  name: {{ .Release.Name }}-connectivity-test\n'
                b'  annotations:\n'
                b'    helm.sh/hook: test\n'
                b'spec:\n'
                b'  automountServiceAccountToken: false\n'
                b'  containers:\n'
                b'    - name: test\n'
                b'      image: example.invalid/research/busybox@sha256:'
                b'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n'
                b'      command: ["sh", "-c", "echo ok"]\n'
                b'      securityContext:\n'
                b'        runAsNonRoot: true\n'
                b'        allowPrivilegeEscalation: false\n'
                b'        capabilities:\n'
                b'          drop: ["ALL"]\n'
                b'      resources:\n'
                b'        limits:\n'
                b'          cpu: 50m\n'
                b'          memory: 32Mi\n'
            ),
        },
    },
}

# {(chart_name, chart_version, source_commit): {relative_path: {
#     'original_sha256': <exact upstream content this removal was reviewed against>,
#     'reason': <human-readable, auditable justification -- required, non-empty>,
# }}}
# PHASE 1: operator-controlled file removal. Scoped to templates/ and
# charts/ (never Chart.yaml/values.yaml/values.schema.json -- those get
# the separate, narrower contracts below). There is deliberately no
# "action" field inside ADAPTERS entries: substitution and removal are
# kept in separate registries with separate, non-overlapping shapes so
# neither can be mistaken for the other at review time.
REMOVALS = {
    ('multi-nf', '0.1.0', '5' * 40): {
        # Synthetic reproduction of a redundant, packaged dependency
        # archive (free5GC vendors MongoDB this way -- see the pipeline
        # design report "MongoDB dependencies"). Used only by
        # tests/test_telecom_multi_image.py: ChartAdapterRemovalTests.
        'charts/legacy-mongodb-4.2.1.tgz': {
            'original_sha256': 'f1bef5285e09e5c1a5531ad563e32b07cdb77771e05551b99a1910508add7f53',
            'reason': 'Redundant packaged MongoDB dependency archive; superseded by an '
                      'independently provisioned MongoDB instance (see pipeline design report, '
                      '"MongoDB dependencies"). Packaged archives are never inspected by this '
                      'pipeline (see subcharts.py Option B) -- removal, not extraction, is the '
                      'only supported path for one that is no longer needed.',
        },
    },
    # Paired with the matching DEPENDENCY_REMOVALS entry above: a packaged
    # archive vendored ONE LEVEL INSIDE a subchart (redis-cache standing in
    # for e.g. free5GC's NRF), modeling the real free5GC MongoDB finding
    # exactly. REMOVALS already supports any charts/ path regardless of
    # nesting depth -- no code change was needed here, only this entry.
    ('multi-nf', '0.1.0', 'a' * 40): {
        'charts/redis-cache/charts/cache-backend-1.0.0.tgz': {
            'original_sha256': 'a5074b8115423916cf9124318cd20b9c879bea90269c09c753de73d9e844630c',
            'reason': 'Synthetic reproduction of a redundant, subchart-vendored packaged '
                      'dependency archive (see pipeline design report, "MongoDB '
                      'dependencies") -- superseded by an independently provisioned instance.',
        },
    },
}

# Paths a removal may never target, regardless of registry content -- a
# chart must always keep real Chart.yaml/values.yaml; those get the
# narrower, dedicated contracts below instead of whole-file removal.
_REMOVAL_FORBIDDEN_TARGETS = {'Chart.yaml', 'values.yaml', 'values.schema.json'}

# {(chart_name, chart_version, source_commit): [{
#     'target_path': <'values.yaml' | 'Chart.yaml' | a bounded, vendored
#                      subchart equivalent -- see _validate_target_path>,
#     'original_sha256': <digest of the ENTIRE original target file>,
#     'overrides': {'dotted.key.path': {
#         'value': <fixed, reviewed, scalar value>,
#         'purpose': <required, non-empty>,
#         'security_impact': <required, non-empty>,
#     }},
# }, ...]}
# PHASE 2a: a FIXED set of already-existing key paths, flipped to a FIXED,
# reviewed scalar value, in ONE OR MORE explicitly reviewed target files
# (the top-level values.yaml and/or a vendored subchart's own). Never
# introduces a new key (the path must already resolve in the parsed
# original file); never accepts a non-scalar value (no smuggling
# structured content through a single "override"); the ENTIRE original
# file is digest-pinned per target, not just the touched keys.
VALUES_OVERRIDES = {
    ('multi-nf', '0.1.0', '8' * 40): [
        {
            'target_path': 'values.yaml',
            'original_sha256': '7ccceec13e02e4f673c961cff66240f9694a9138dfe791b1f8e45c10299db2ea',
            'overrides': {
                'probe.tag': {
                    'value': 'v1.0.1',
                    'purpose': 'Synthetic demonstration of the values-override contract itself '
                               '(tests/test_telecom_multi_image.py: ChartAdapterValuesOverrideTests) '
                               '-- pins the probe utility image to a newer reviewed tag.',
                    'security_impact': 'No security-relevant change: a tag-reference string only, '
                                        'still subject to the full existing image-declaration and '
                                        'live registry verification downstream.',
                },
            },
        },
    ],
    # Synthetic demonstration of the SUBCHART-SCOPED case -- modeled on the
    # real free5GC finding that a vendored subchart's own values.yaml can
    # carry configuration this pipeline needs to adjust (e.g. a database
    # connection toggle), not just the top-level chart's. Used only by
    # tests/test_telecom_multi_image.py: SubchartScopedAdaptationTests.
    ('multi-nf', '0.1.0', 'a' * 40): [
        {
            'target_path': 'charts/redis-cache/values.yaml',
            'original_sha256': 'f2427ebc6f8856753346846432abee232b4d1be41acc8a252fede03c9c0de921',
            'overrides': {
                'cacheMode': {
                    'value': 'cluster',
                    'purpose': 'Synthetic demonstration of a subchart-scoped values override '
                               '(tests/test_telecom_multi_image.py: SubchartScopedAdaptationTests).',
                    'security_impact': 'No security-relevant change: a plain configuration '
                                        'string, not a credential or capability.',
                },
            },
        },
    ],
}

# {(chart_name, chart_version, source_commit): [{
#     'target_path': <'values.yaml' | 'Chart.yaml' | a bounded, vendored
#                      subchart equivalent -- see _validate_target_path>,
#     'original_sha256': <digest of the ENTIRE original target file>,
#     'removals': [{'name': ..., 'version': ..., 'reason': <required, non-empty>}],
# }, ...]}
# PHASE 2b: drop ONE OR MORE named (name, version) dependency entries from
# a target Chart.yaml's `dependencies:` list -- the top-level one and/or a
# vendored subchart's own (e.g. free5GC's NRF/UDR/WebUI subcharts each
# declare their OWN MongoDB dependency in their OWN Chart.yaml, not the
# top-level free5gc chart's). Every other field and every other dependency
# is preserved (re-serialized, not hand-edited). Raises if a named
# (name, version) pair is not actually present in its target -- never
# silently a no-op for a stale or mistaken registry entry.
DEPENDENCY_REMOVALS = {
    ('multi-nf', '0.1.0', '9' * 40): [
        {
            'target_path': 'Chart.yaml',
            'original_sha256': 'e55e2f02b5b1f7d5b6cc9041bb4ace3e2a241052113a4638d6962e7775109dc4',
            'removals': [
                {'name': 'redis-cache', 'version': '1.0.0',
                 'reason': 'Synthetic demonstration of the dependency-removal contract itself '
                           '(tests/test_telecom_multi_image.py: ChartAdapterDependencyRemovalTests).'},
            ],
        },
    ],
    # Synthetic demonstration of the SUBCHART-SCOPED case, paired with the
    # matching REMOVALS entry below for the nested packaged archive --
    # together these model the real free5GC finding exactly: a vendored
    # subchart (here "redis-cache", standing in for e.g. free5GC's NRF)
    # declares its OWN dependency on a packaged archive (standing in for
    # the vendored MongoDB .tgz) in its OWN Chart.yaml, one level below the
    # top-level chart. Used only by
    # tests/test_telecom_multi_image.py: SubchartScopedAdaptationTests.
    ('multi-nf', '0.1.0', 'a' * 40): [
        {
            'target_path': 'charts/redis-cache/Chart.yaml',
            'original_sha256': '62af7521a785376d5cc794a64dc86771e4977d28315dd02b1b42f61191efeb4d',
            'removals': [
                {'name': 'cache-backend', 'version': '1.0.0',
                 'reason': 'Synthetic reproduction of a redundant, subchart-vendored packaged '
                           'dependency archive (see pipeline design report, "MongoDB '
                           'dependencies") -- superseded by an independently provisioned '
                           'instance; its vendored copy is removed by the paired REMOVALS entry.'},
            ],
        },
    ],
}


# Acquisition authorities that mean "this exact commit's tree was
# independently extracted and verified via git's own content-addressing"
# (see execution.acquire()). 'PROVIDER_DECLARED' (the fallback evidence for
# oci/helm sources, or any source never actually acquired) is deliberately
# excluded: it is a self-reported claim, not a verified one.
VERIFIED_ACQUISITION_AUTHORITIES = {'LOCAL_OBJECTS_VERIFIED', 'GITHUB_FETCH_VERIFIED'}


def _require_verified_evidence(commit, source_evidence):
    if (not commit or not isinstance(source_evidence, dict) or
            source_evidence.get('authority') not in VERIFIED_ACQUISITION_AUTHORITIES or
            source_evidence.get('commit') != commit):
        raise ValueError('Adaptation requires independently verified source acquisition evidence')


# How many 'charts/<name>/' prefixes a values.yaml/Chart.yaml target_path
# may carry before the final filename -- matches subcharts.MAX_SUBCHART_DEPTH
# (not imported, to avoid a needless cross-module coupling for one constant).
_MAX_TARGET_DEPTH = 2
_SUBCHART_NAME = r'[a-z][a-z0-9-]{0,62}'
_TARGET_PATH_RE = re.compile(
    r'^(?:charts/%s/){0,%d}(?:values\.yaml|Chart\.yaml)$' % (_SUBCHART_NAME, _MAX_TARGET_DEPTH)
)


def _validate_target_path(target_path):
    """A values/Chart.yaml adaptation target may ONLY be the top-level
    `values.yaml`/`Chart.yaml`, or a bounded, vendored subchart's own
    (`charts/<name>/values.yaml` or `.../Chart.yaml`, up to
    _MAX_TARGET_DEPTH levels of `charts/<name>/` nesting). The regex
    itself is the whole defense: it is fully anchored, allows no other
    filename, and has no character class that could ever match an
    absolute path (no leading `/`) or a `..` traversal segment (`..` is
    not a valid subchart name) -- so those are rejected by construction,
    not by a separate check. `target_path` is always a registry-authored
    field (see ADAPTERS/REMOVALS/VALUES_OVERRIDES/DEPENDENCY_REMOVALS'
    docstrings) -- there is no caller-supplied path anywhere in this
    module."""
    if not isinstance(target_path, str) or not _TARGET_PATH_RE.fullmatch(target_path):
        raise ValueError('Unsupported or unreviewed adaptation target path: ' + str(target_path))
    return target_path


class _NoDuplicateKeysLoader(yaml.SafeLoader):
    """A plain yaml.safe_load() silently resolves a duplicate mapping key to
    whichever occurrence it sees last -- PyYAML never raises for this. If
    the upstream file actually has a duplicate key, OUR parse and Helm's own
    (Go-based) renderer could legitimately disagree about which value wins,
    so acting on our own resolution here would be acting on an ambiguity we
    cannot actually resolve correctly. Used ONLY by apply_values_overrides()/
    apply_dependency_removals(), which ACT on the parsed structure (unlike
    _chart_identity_key(), which only reads two scalar fields and already
    fails safe -- a no-op, not a modification -- on any parse problem)."""


def _no_duplicate_keys(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ValueError('Duplicate YAML key: ' + str(key))
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_NoDuplicateKeysLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_duplicate_keys)


def _strict_yaml_load(data, invalid_message):
    """Parse YAML, failing closed (not just on syntax errors, but also on
    the duplicate-key ambiguity safe_load() silently tolerates)."""
    try:
        return yaml.load(data, Loader=_NoDuplicateKeysLoader)
    except Exception as error:
        raise ValueError(invalid_message + ': ' + str(error)) from None


def _dotted_lookup(parsed, key_path, target_path):
    """Resolve a dotted key path in an already-parsed mapping, requiring
    EVERY level to already exist and EVERY level's siblings to be
    unambiguous (no literal key containing '.' -- which could otherwise be
    confused with a path separator). Returns (parent_dict, final_segment,
    existing_value); raises ValueError on any violation. `target_path`
    (the file this mapping came from) is only used for error messages."""
    segments = key_path.split('.')
    cursor = parsed
    for segment in segments[:-1]:
        if not isinstance(cursor, dict) or segment not in cursor:
            raise ValueError('Values override key does not exist in original ' + target_path + ': ' + key_path)
        if any('.' in existing_key for existing_key in cursor if isinstance(existing_key, str)):
            raise ValueError('Ambiguous dotted-key path in original ' + target_path + ': ' + key_path)
        cursor = cursor[segment]
    if not isinstance(cursor, dict) or segments[-1] not in cursor:
        raise ValueError('Values override key does not exist in original ' + target_path + ': ' + key_path)
    if any('.' in existing_key for existing_key in cursor if isinstance(existing_key, str)):
        raise ValueError('Ambiguous dotted-key path in original ' + target_path + ': ' + key_path)
    return cursor, segments[-1], cursor[segments[-1]]


def _chart_identity_key(files, manifest):
    """Return (chart_name, chart_version, commit), or None if Chart.yaml is
    absent/unparseable -- callers then treat this as "no registry entry
    can possibly match" rather than raising, consistent with adapt()'s
    long-standing behavior of silently no-op'ing for an unrecognized or
    malformed chart rather than failing the whole pipeline on it (that job
    belongs to safe_chart_inputs()/multi_image.verify(), which still run
    unconditionally afterward)."""
    try:
        chart_meta = yaml.safe_load(files.get('Chart.yaml', b'')) or {}
    except Exception:
        return None
    if not isinstance(chart_meta, dict):
        return None
    commit = (manifest.get('source') or {}).get('commit')
    return (chart_meta.get('name'), chart_meta.get('version'), commit)


def adapt(files, manifest, source_evidence=None):
    """Return (adapted_files, provenance); `files` itself is never mutated.

    `adapted_files` is a NEW dict (a shallow copy of `files` with any
    matched ADAPTERS substitution applied, and any matched REMOVALS target
    deleted). `provenance` is a list of per-operation records, each tagged
    `'action': 'substitute'` ({'action','path','original_sha256',
    'replacement_sha256'}) or `'action': 'remove'` ({'action','path',
    'original_sha256','reason'}) -- empty when neither registry has an
    entry for this chart identity.

    `source_evidence` must be the evidence value `execution.acquire()` (or
    `execution.helm_source()`) itself produced for THIS job -- the caller
    (package.prepare(), threaded from pipeline.run()) is expected to pass
    through the same object it already computed from a real acquisition,
    never to construct one ad hoc. Every operation only proceeds when its
    'authority' is one of VERIFIED_ACQUISITION_AUTHORITIES and its 'commit'
    equals the manifest's declared source commit -- a declaration-only
    authority, or a missing/mismatched one, is rejected outright, even if a
    registry entry's (name, version, commit) key otherwise matches.

    LIMITATION (deliberately not papered over): within a single Python
    process there is no cryptographic separation between "a value
    execution.acquire() actually returned" and "a dict some other in-process
    caller constructed by hand with the same shape" -- this check narrows
    the trust surface (a caller must additionally know and assert the exact
    verified-looking shape) but it is not an unforgeable proof. Closing that
    fully would require acquisition evidence to be an opaque, sealed object
    type only constructible inside execution.py (or an actual process/
    privilege boundary between orchestration and packaging), which is a
    larger refactor than this change makes. What DOES remain unconditionally
    enforced regardless of how trustworthy `source_evidence` is: every
    operation can only ever act on a file whose CURRENT content matches the
    exact reviewed digest -- fabricated evidence alone, without that exact
    byte match, achieves nothing.
    """
    if manifest.get('schema_version') != 2:
        return files, []
    key = _chart_identity_key(files, manifest)
    if key is None:
        return files, []
    substitutions = ADAPTERS.get(key)
    removals = REMOVALS.get(key)
    if substitutions is None and removals is None:
        return files, []
    overlap = set(substitutions or {}) & set(removals or {})
    if overlap:
        raise ValueError('Adapter registry error: path registered for both substitution and removal: ' +
                          ', '.join(sorted(overlap)))
    commit = key[2]
    _require_verified_evidence(commit, source_evidence)
    adapted = dict(files)
    provenance = []
    for target, patch in (substitutions or {}).items():
        if not target.startswith('templates/'):
            raise ValueError('Adapter may only patch templates/: ' + target)
        if target not in files:
            raise ValueError('Adapter target file missing from chart: ' + target)
        if hashlib.sha256(files[target]).hexdigest() != patch['original_sha256']:
            raise ValueError('Adapter review is stale: upstream content changed for ' + target)
        adapted[target] = patch['replacement']
        provenance.append({'action': 'substitute', 'path': target, 'original_sha256': patch['original_sha256'],
                            'replacement_sha256': hashlib.sha256(patch['replacement']).hexdigest()})
    for target, removal in (removals or {}).items():
        if target in _REMOVAL_FORBIDDEN_TARGETS:
            raise ValueError('Removal may not target chart metadata/values: ' + target)
        if not (target.startswith('templates/') or target.startswith('charts/')):
            raise ValueError('Removal may only target templates/ or charts/: ' + target)
        if target not in files:
            raise ValueError('Removal target file missing from chart: ' + target)
        if hashlib.sha256(files[target]).hexdigest() != removal['original_sha256']:
            raise ValueError('Removal review is stale: upstream content changed for ' + target)
        if not isinstance(removal.get('reason'), str) or not removal['reason']:
            raise ValueError('Removal reason must be documented: ' + target)
        del adapted[target]
        provenance.append({'action': 'remove', 'path': target, 'original_sha256': removal['original_sha256'],
                            'reason': removal['reason']})
    return adapted, provenance


def apply_values_overrides(files, manifest, source_evidence=None):
    """Return (updated_files, provenance); `files` itself is never mutated.

    Applies every VALUES_OVERRIDES entry (if any) registered for this exact
    chart identity + commit + independently verified source_evidence, each
    acting on its own explicit, reviewed `target_path` (the top-level
    values.yaml, or a bounded, vendored subchart's own -- see
    _validate_target_path). No-ops (returns `files` unchanged, []) for
    schema_version != 2, an unparseable Chart.yaml, or no matching registry
    entry at all.

    Every override key path MUST already resolve to an existing value in
    the ORIGINAL (pre-override) parsed target file -- this never introduces
    a new, unreviewed configuration surface, only flips an existing,
    reviewed toggle to a fixed, reviewed scalar value. A non-scalar
    override value is rejected (no smuggling structured content through a
    single "override"). The ENTIRE original target file is digest-pinned;
    upstream drift in ANY part of it (not just the touched keys) fails
    closed. Two registry entries naming the same `target_path` under one
    chart identity is a registry-authoring error and is rejected outright.

    `provenance` is a list of {'target_path', 'key_path', 'value',
    'purpose', 'security_impact'} records, one per override actually
    applied, across every target_path.
    """
    if manifest.get('schema_version') != 2:
        return files, []
    key = _chart_identity_key(files, manifest)
    if key is None:
        return files, []
    entries = VALUES_OVERRIDES.get(key)
    if entries is None:
        return files, []
    _require_verified_evidence(key[2], source_evidence)
    updated = dict(files)
    provenance = []
    seen_targets = set()
    for entry in entries:
        target_path = _validate_target_path(entry.get('target_path'))
        if target_path in seen_targets:
            raise ValueError('Duplicate values-override target path in registry: ' + target_path)
        seen_targets.add(target_path)
        if target_path not in files:
            raise ValueError('Values override target file missing from chart: ' + target_path)
        if hashlib.sha256(files[target_path]).hexdigest() != entry['original_sha256']:
            raise ValueError(target_path + ' review is stale: upstream content changed')
        parsed = _strict_yaml_load(files[target_path], 'Invalid ' + target_path + ' content')
        if not isinstance(parsed, dict):
            raise ValueError('Invalid ' + target_path + ' content')
        for key_path, override in entry['overrides'].items():
            value = override['value']
            if value is not None and not isinstance(value, (bool, int, float, str)):
                raise ValueError('Values override value must be a scalar: ' + target_path + '#' + key_path)
            if not isinstance(override.get('purpose'), str) or not override['purpose']:
                raise ValueError('Values override purpose must be documented: ' + target_path + '#' + key_path)
            if not isinstance(override.get('security_impact'), str) or not override['security_impact']:
                raise ValueError('Values override security impact must be documented: ' +
                                  target_path + '#' + key_path)
            parent, final_segment, existing_value = _dotted_lookup(parsed, key_path, target_path)
            if existing_value is not None and not isinstance(existing_value, (bool, int, float, str)):
                raise ValueError('Values override target is not a scalar in original ' + target_path + ': ' + key_path)
            parent[final_segment] = value
            provenance.append({'target_path': target_path, 'key_path': key_path, 'value': value,
                                'purpose': override['purpose'], 'security_impact': override['security_impact']})
        updated[target_path] = yaml.safe_dump(parsed, sort_keys=True).encode()
    return updated, provenance


def apply_dependency_removals(files, manifest, source_evidence=None):
    """Return (updated_files, provenance); `files` itself is never mutated.

    Applies every DEPENDENCY_REMOVALS entry (if any) registered for this
    exact chart identity + commit + independently verified
    source_evidence, each acting on its own explicit, reviewed
    `target_path` (the top-level Chart.yaml, or a bounded, vendored
    subchart's own -- see _validate_target_path). No-ops for
    schema_version != 2, an unparseable Chart.yaml, or no matching registry
    entry at all.

    Drops each registered (name, version) pair from its target's
    `dependencies:` list; every other field and every other dependency
    entry in that target is preserved (re-serialized from the parsed
    structure, not hand-edited). Raises if a registered (name, version)
    pair is not actually present in its target's current `dependencies:`
    list -- a stale or mistaken registry entry is a loud failure, never a
    silent no-op. Two registry entries naming the same (name, version)
    within one target's removal list, two registry entries naming the same
    `target_path`, or the target's OWN `dependencies:` list actually
    declaring the same (name, version) pair twice are all rejected as
    ambiguous, never resolved by guessing which one "wins".

    `provenance` is a list of {'target_path', 'name', 'version', 'reason'}
    records, one per dependency actually removed, across every target_path.
    """
    if manifest.get('schema_version') != 2:
        return files, []
    key = _chart_identity_key(files, manifest)
    if key is None:
        return files, []
    entries = DEPENDENCY_REMOVALS.get(key)
    if entries is None:
        return files, []
    _require_verified_evidence(key[2], source_evidence)
    updated = dict(files)
    provenance = []
    seen_targets = set()
    for entry in entries:
        target_path = _validate_target_path(entry.get('target_path'))
        if target_path in seen_targets:
            raise ValueError('Duplicate dependency-removal target path in registry: ' + target_path)
        seen_targets.add(target_path)
        if target_path not in files:
            raise ValueError('Dependency removal target file missing from chart: ' + target_path)
        if hashlib.sha256(files[target_path]).hexdigest() != entry['original_sha256']:
            raise ValueError(target_path + ' review is stale: upstream content changed')
        parsed = _strict_yaml_load(files[target_path], 'Invalid ' + target_path + ' content')
        if not isinstance(parsed, dict) or not isinstance(parsed.get('dependencies'), list):
            raise ValueError(target_path + ' dependencies declaration missing or invalid')
        pending = {}
        for removal in entry['removals']:
            if not isinstance(removal.get('reason'), str) or not removal['reason']:
                raise ValueError('Dependency removal reason must be documented: ' +
                                  target_path + '#' + str(removal.get('name')))
            dep_key = (removal['name'], removal['version'])
            if dep_key in pending:
                raise ValueError('Duplicate dependency removal identity in registry: ' +
                                  target_path + '#' + str(dep_key))
            pending[dep_key] = removal['reason']
        remaining = []
        found = set()
        for dep in parsed['dependencies']:
            if not isinstance(dep, dict):
                raise ValueError('Invalid ' + target_path + ' dependency entry')
            dep_key = (dep.get('name'), dep.get('version'))
            if dep_key in pending:
                if dep_key in found:
                    raise ValueError('Ambiguous dependency identity in ' + target_path + ': ' + str(dep_key))
                found.add(dep_key)
                provenance.append({'target_path': target_path, 'name': dep_key[0], 'version': dep_key[1],
                                    'reason': pending[dep_key]})
                continue
            remaining.append(dep)
        missing = set(pending) - found
        if missing:
            raise ValueError('Dependency removal target not found in ' + target_path + ': ' +
                              ', '.join(sorted(name for name, version in missing)))
        parsed['dependencies'] = remaining
        updated[target_path] = yaml.safe_dump(parsed, sort_keys=True).encode()
    return updated, provenance
