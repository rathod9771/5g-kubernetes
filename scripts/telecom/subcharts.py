"""Bounded, reviewed validation of vendored Helm subchart dependencies
(`charts/<name>/...`) for schema_version=2.

Helm auto-discovers LOCALLY VENDORED subcharts under `charts/<name>/` at
`helm lint`/`helm template` time -- no network fetch, no `helm dependency
build` required -- so once this module approves a chart's `charts/`
entries, package.prepare()'s existing render step (which already writes
every captured file to its relative path and already recurses through
whatever Helm itself renders) picks them up for free: nested templates get
the IDENTICAL image-discovery, pod-security and Helm-hook scrutiny
multi_image.verify() already applies to every rendered document, with no
separate code path. This module's only job is deciding whether the raw
`charts/...` files themselves are safe to feed into that existing pipeline
at all.

This is deliberately NOT package.py's `reviewed_inputs` escape hatch, which
skips `safe_chart_inputs()`'s entire per-file content policy for whatever
it is handed. A vendored dependency file must pass BOTH an exact per-file
digest pin from the caller-supplied `reviewed_dependencies` inventory
(provenance: nothing here is a surprise; an extra, missing, or drifted file
is rejected) AND the identical structural/content checks (extension
allowlist, sensitive-path rejection, private-key scan, inline-credential
scan on values files) a top-level template already gets -- this module
only widens WHICH PATH SHAPES are recognized, never what content is
accepted at a given shape. Symlinks, path traversal and non-regular file
types are already rejected before this module ever runs, by
package.prepare()'s existing capture() walker (O_NOFOLLOW + real directory
file descriptors + regular-file-only checks) -- nothing here needs to
re-implement that.

TRUST MODEL -- read before calling validate() from anywhere new: there are
now TWO ways `reviewed_dependencies` can be satisfied, with very different
trust weight.
  1. TRUSTED_DEPENDENCIES (below): an in-repo, code-reviewed registry keyed
     by (chart name, chart version, source commit), exactly like
     chart_adapters.ADAPTERS. This is what actually deserves the word
     "operator-reviewed" -- it lives in source control, goes through the
     normal code-review process, and `validate()` only honors it when
     `source_evidence` independently proves (via the SAME authority+commit
     binding chart_adapters.adapt() requires) that this exact commit was
     really acquired, not merely asserted.
  2. A bare, caller-supplied `reviewed_dependencies` dict: checked for
     INTERNAL CONSISTENCY ONLY -- that the supplied digests match the
     chart content the SAME caller also supplies. Nothing here
     independently establishes WHO computed those digests. A caller that
     controls both the chart content and this dict (e.g. by hashing its
     own files and calling that "reviewed") gains nothing extra from this
     module -- the content/structural checks below still apply in full --
     but this path does NOT constitute operator approval. It exists for
     direct, offline test/package.prepare() usage; it is NOT what
     `vendored_dependencies` should become if ever wired past
     package.prepare() into a less-trusted caller.
This is also why `vendored_dependencies`/`chart_profile='large'` are NOT
wired into pipeline.run()/queue() (see package.prepare()): no untrusted
caller can reach either parameter today. A future wiring should resolve
the inventory via TRUSTED_DEPENDENCIES + source_evidence (path 1 above),
never by threading an arbitrary caller-supplied dict through to a
live/external-chart-onboarding path.

Bounded recursion: a vendored subchart may itself vendor ONE further level
of subcharts (`charts/<a>/charts/<b>/...`); deeper nesting is rejected
outright (MAX_SUBCHART_DEPTH) rather than silently traversed. This is a
validation-shape bound, separate from (and additional to) the pre-existing
filesystem-level depth/entry/size budgets in package.prepare()'s capture()
walker, which already apply uniformly to every file including these.

PACKAGED DEPENDENCY ARCHIVES (Option B, not Option A -- see
package.prepare()'s pipeline REPORT "Dependency archive policy"): a
`.tgz`/`.tar`/`.tar.gz`/`.tar.bz2`/`.zip` file anywhere under `charts/` is
rejected outright, with a clear, specific reason naming the file -- never
opened, decompressed, or otherwise inspected. Real charts commonly vendor
a dependency as a packaged archive instead of an unpacked directory (the
real free5GC chart's MongoDB dependency does exactly this, per the
pipeline REPORT); onboarding one requires re-vendoring it as an unpacked
`charts/<name>/` directory first. Safely inspecting an arbitrary archive
(decompression-bomb bounds, in-archive path-traversal/symlink rejection,
per-entry budgets) is its own dedicated hardening problem that has not
been built or validated here, and building it partially would be worse
than not supporting it at all.

SCALE NOTE (see pipeline REPORT "Chart-size and resource-limit
decisions"): the REAL free5GC chart, with its vendored subcharts, is
independently measured (per the pinned commit's stated characteristics)
at roughly 306 files / 2.7MB -- comfortably within package.prepare()'s
NEW, explicitly opt-in 'large' capture profile (512 files / 8MiB / 768
entries), but still well beyond the untouched 'default' profile (128
files / 4MiB / 256 entries) every existing caller keeps. Neither profile
is selected automatically; a caller must explicitly request 'large'. This
module does not change either profile's numbers itself -- see
package.prepare() for the two-profile mechanism and its own documented
memory/CPU/concurrency safeguards.
"""

import hashlib
import re

import yaml

from osm_packages import reject_inline_credentials
from . import chart_adapters

MAX_SUBCHART_DEPTH = 2  # charts/<a>/ and charts/<a>/charts/<b>/ -- not deeper.

# PHASE 3: the operator-reviewed dependency registry. Exactly the same
# shape and review discipline as chart_adapters.ADAPTERS -- keyed by
# (chart name, chart version, source commit), containing final, literal,
# reviewed {relative_path: sha256} digests checked into this module. This
# is the thing that actually deserves to be called "operator-reviewed": it
# lives in source control, goes through the normal code-review process,
# and (per validate()) can only be used when `source_evidence` independently
# proves the exact commit it is keyed to was really acquired -- never
# because a packaging-time caller happened to assert matching hashes.
#
# NOTE: no real free5GC entry ships in this change (see the pipeline
# REPORT "Do not claim real free5GC compatibility unless the actual pinned
# chart has been evaluated" -- the real chart was never fetched or
# inspected from this (HP) environment). The one entry below is a
# SYNTHETIC demonstration matching this repo's own offline test fixture
# (tests/test_telecom_multi_image.py: TrustedDependencyTests), keyed to a
# commit ('3'*40) that cannot collide with any real git object id.
TRUSTED_DEPENDENCIES = {
    ('multi-nf', '0.1.0', '3' * 40): {
        'charts/redis-cache/Chart.yaml':
            '194abec2aa0ba367194c70eb28b0673a2b953ffe1409c32b9f312ece87d6d063',
        'charts/redis-cache/templates/deployment.yaml':
            '270d2afe95a9a6c361652cda646e6b9ae71c2bbd29515367c7260be0feb8d09f',
    },
}

_APPROVED_TOP_NAMES = {'Chart.yaml', 'Chart.lock', 'values.schema.json', '.helmignore', 'README.md', 'LICENSE'}
_VALUES_RE = re.compile(r'values(?:-[a-zA-Z0-9_-]+)?\.ya?ml')
_SENSITIVE_NAMES = {'.git', '__pycache__', '.aws', '.ssh', 'credentials', 'secrets'}
_SENSITIVE_SUFFIXES = ('.key', '.pem', '.p12', '.pyc', '.swp', '.swo', '~')
# PHASE 2 decision (see module docstring "PACKAGED DEPENDENCY ARCHIVES"):
# Option B -- explicit rejection, not Option A (bounded archive inspection).
# Safely extracting an arbitrary .tgz/.tar/.zip requires its own dedicated
# hardening (decompression-bomb bounds, in-archive path-traversal and
# symlink rejection, per-entry size/count budgets) that has not been built
# or validated here; building it half-way would be worse than not
# supporting it. A chart that vendors a dependency as a packaged archive
# (free5GC's MongoDB dependency does exactly this) must be re-vendored as
# an unpacked charts/<name>/ directory to onboard today.
_ARCHIVE_SUFFIXES = ('.tgz', '.tar.gz', '.tar', '.tar.bz2', '.tbz2', '.zip')


def _decompose(name):
    """For a path under `charts/`, return (prefix, depth, relative) --
    `prefix` is the exact enclosing subchart root (e.g. 'charts/mongodb/' or
    'charts/mongodb/charts/redis/'), `depth` how many `charts/<x>/` segments
    deep it is, and `relative` the file's path within that one subchart.
    Returns None for a malformed/boundary path (e.g. 'charts/' alone)."""
    parts = name.split('/')
    if not parts or parts[0] != 'charts':
        return None
    depth = 0
    i = 0
    prefix_parts = []
    while i < len(parts) and parts[i] == 'charts':
        if i + 1 >= len(parts) or not parts[i + 1]:
            return None
        depth += 1
        prefix_parts += [parts[i], parts[i + 1]]
        i += 2
    relative = '/'.join(parts[i:])
    if not relative:
        return None
    return '/'.join(prefix_parts) + '/', depth, relative


def _relative_sensitive_or_unapproved(relative):
    path_parts = [p.lower() for p in relative.split('/')]
    if any((p.startswith('.') and p != '.helmignore') or p.startswith(('secret', '.#')) or p.endswith('#')
           or p in _SENSITIVE_NAMES or p.startswith('.env') or 'credential' in p or p.endswith(_SENSITIVE_SUFFIXES)
           for p in path_parts):
        return True
    first = relative.split('/', 1)[0]
    basename = relative.rsplit('/', 1)[-1]
    approved = (relative in _APPROVED_TOP_NAMES or _VALUES_RE.fullmatch(relative) or
                (first == 'templates' and
                 (basename.rsplit('.', 1)[-1] in ('yaml', 'yml', 'tpl', 'json') or basename == 'NOTES.txt')))
    return not approved


def validate(files, parent_chart_meta, reviewed_dependencies, *, commit=None, source_evidence=None):
    """Validate every `charts/...` entry in the already-captured `files`
    dict (see package.prepare()). Returns `dependency_registry_key` --
    the exact (chart_name, chart_version, commit) key of the
    TRUSTED_DEPENDENCIES entry that was actually used, or `None` if the
    chart had nothing to validate, or if only the lower-trust,
    caller-supplied-consistency-only path was used. `None` here is a
    meaningful, honest fact for provenance purposes (see
    package.prepare()'s `adaptation_provenance`): it means no
    operator-reviewed registry backs this package's dependency content --
    never conflate it with "nothing was approved," since a chart with zero
    `charts/` entries needs no approval at all. Raises ValueError on any
    violation. A chart with no `charts/` entries AND no declared
    `dependencies:` at all is a complete no-op -- existing single-chart
    behavior is unaffected. A declared dependency with nothing vendored
    for it at all (zero charts/ files) is still caught below, not silently
    treated as "nothing to check."

    If this exact (chart name, chart version, `commit`) has an entry in
    TRUSTED_DEPENDENCIES, that in-repo, code-reviewed inventory is
    authoritative -- it requires `source_evidence` to independently prove
    this exact commit was acquired (see chart_adapters.VERIFIED_ACQUISITION_
    AUTHORITIES), and a caller-supplied `reviewed_dependencies` that
    disagrees with it is rejected outright, not silently overridden.
    Otherwise, `reviewed_dependencies` is checked for CONSISTENCY with
    `files` only, not AUTHENTICITY -- see the module docstring's "TRUST
    MODEL" section before calling this from any new, less-trusted caller
    than today's direct, offline test/package.prepare() usage.
    """
    vendored = {name: data for name, data in files.items() if name.startswith('charts/')}
    declared_top = _dependencies(parent_chart_meta)
    if not vendored and not declared_top:
        return None
    for name in vendored:
        if any(name.lower().endswith(suffix) for suffix in _ARCHIVE_SUFFIXES):
            raise ValueError('Packaged Helm dependency archives (.tgz/.tar/.zip) are not supported; '
                              'vendor this dependency as an unpacked charts/<name>/ directory instead: ' + name)
    chart_name = parent_chart_meta.get('name') if isinstance(parent_chart_meta, dict) else None
    chart_version = parent_chart_meta.get('version') if isinstance(parent_chart_meta, dict) else None
    trusted = TRUSTED_DEPENDENCIES.get((chart_name, chart_version, commit))
    if trusted is not None:
        if (not commit or not isinstance(source_evidence, dict) or
                source_evidence.get('authority') not in chart_adapters.VERIFIED_ACQUISITION_AUTHORITIES or
                source_evidence.get('commit') != commit):
            raise ValueError('Trusted vendored dependency inventory requires independently verified source '
                              'acquisition evidence')
        if reviewed_dependencies is not None and reviewed_dependencies != trusted:
            raise ValueError('Caller-supplied vendored dependency inventory conflicts with the trusted, '
                              'reviewed registry')
        reviewed_dependencies = trusted
    if reviewed_dependencies is None:
        raise ValueError('Vendored Helm dependencies require an explicit reviewed inventory')
    if set(vendored) != set(reviewed_dependencies):
        raise ValueError('Reviewed vendored dependency inventory does not match the chart exactly')
    for name, data in vendored.items():
        if hashlib.sha256(data).hexdigest() != reviewed_dependencies[name]:
            raise ValueError('Unreviewed vendored dependency content change: ' + name)
        if b'PRIVATE KEY-----' in data:
            raise ValueError('Private key material is not a chart input: ' + name)

    decomposed = {}
    for name in vendored:
        parts = _decompose(name)
        if parts is None:
            raise ValueError('Malformed vendored dependency path: ' + name)
        decomposed[name] = parts

    roots = sorted({(prefix, depth) for prefix, depth, _ in decomposed.values()}, key=lambda r: r[1])
    declared_by_prefix = {'': declared_top}
    seen_names_by_parent = {}
    for prefix, depth in roots:
        if depth > MAX_SUBCHART_DEPTH:
            raise ValueError('Vendored subchart nesting exceeds the supported depth: ' + prefix)
        chart_yaml = prefix + 'Chart.yaml'
        if chart_yaml not in vendored:
            raise ValueError('Vendored subchart missing Chart.yaml: ' + prefix)
        try:
            meta = yaml.safe_load(vendored[chart_yaml]) or {}
        except Exception:
            raise ValueError('Invalid vendored subchart metadata: ' + prefix) from None
        if (not isinstance(meta, dict) or not isinstance(meta.get('name'), str) or
                not isinstance(meta.get('version'), str)):
            raise ValueError('Invalid vendored subchart metadata: ' + prefix)
        segments = prefix.rstrip('/').split('/')  # e.g. ['charts','mongodb','charts','redis']
        directory_name = segments[-1]
        if meta['name'] != directory_name:
            raise ValueError('Vendored subchart directory name disagrees with Chart.yaml: ' + prefix)
        parent_segments = segments[:-2]  # drop this root's own trailing 'charts/<name>' pair
        parent_prefix = '/'.join(parent_segments) + '/' if parent_segments else ''
        declared = declared_by_prefix.get(parent_prefix)
        if declared is None or (meta['name'], meta['version']) not in declared:
            raise ValueError('Vendored subchart is not declared as a dependency: ' + meta['name'])
        declared_by_prefix[prefix] = _dependencies(meta)
        seen_names_by_parent.setdefault(parent_prefix, set()).add((meta['name'], meta['version']))

    for owner_prefix, declared in declared_by_prefix.items():
        seen = seen_names_by_parent.get(owner_prefix, set())
        for dep in declared:
            if dep not in seen:
                raise ValueError('Declared dependency is not vendored locally: ' + dep[0])

    for name, (prefix, depth, relative) in decomposed.items():
        if _relative_sensitive_or_unapproved(relative):
            raise ValueError('Unexpected vendored dependency input requires review: ' + name)
        if _VALUES_RE.fullmatch(relative):
            reject_inline_credentials(yaml.safe_load(vendored[name]))

    return (chart_name, chart_version, commit) if trusted is not None else None


def _dependencies(chart_meta):
    declared = (chart_meta or {}).get('dependencies') or []
    if not isinstance(declared, list):
        raise ValueError('Invalid Chart.yaml dependencies declaration')
    result = set()
    for dep in declared:
        if (not isinstance(dep, dict) or not isinstance(dep.get('name'), str) or
                not isinstance(dep.get('version'), str)):
            raise ValueError('Invalid Chart.yaml dependency entry')
        result.add((dep['name'], dep['version']))
    return result
