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

TRUST MODEL -- read before calling validate() from anywhere new (see the
pipeline REPORT's "architectural requirement" discussion): `validate()`
takes `reviewed_dependencies` as a bare parameter and only checks INTERNAL
CONSISTENCY -- that the supplied digests match the chart content that is
ALSO supplied by the same caller. Nothing here independently establishes
WHO computed those digests or verifies they came from an actual human
review process. A caller who controls both the chart content and the
`reviewed_dependencies` value it passes in (e.g. by hashing its own files
and calling that "reviewed") gains nothing extra from this module -- the
content/structural checks below still apply in full -- but the words
"operator-reviewed" only become true once something OUTSIDE this module's
control supplies that inventory. This is why `vendored_dependencies` is
NOT wired into pipeline.run()/queue() (see package.prepare()): there is no
untrusted caller that can reach this parameter today. A future wiring must
NOT simply thread a caller-supplied dict through -- it should follow
chart_adapters.py's precedent instead: an in-repo, code-reviewed registry
(like ADAPTERS) keyed by (chart name, chart version, source commit), with
the commit cross-checked against execution.acquire()'s independently
verified evidence (source_evidence, the same binding chart_adapters.adapt()
already requires) -- so the inventory is authorized by what's checked into
this repository and reviewed through the normal code-review process, not
by whatever a packaging-time caller happens to assert.

Bounded recursion: a vendored subchart may itself vendor ONE further level
of subcharts (`charts/<a>/charts/<b>/...`); deeper nesting is rejected
outright (MAX_SUBCHART_DEPTH) rather than silently traversed. This is a
validation-shape bound, separate from (and additional to) the pre-existing
filesystem-level depth/entry/size budgets in package.prepare()'s capture()
walker, which already apply uniformly to every file including these.

SCALE NOTE (see pipeline REPORT "whether current chart traversal limits
support a real free5GC-sized repository"): the REAL free5GC chart, with its
vendored subcharts, is independently known (from this session's earlier
investigation) to run to roughly 900 files -- far beyond
package.prepare()'s existing capture() budgets (128 files, 4MiB total chart
size, 256 directory-walk entries). This module does NOT raise those
budgets. A chart at real free5GC scale will still hit "Chart input budget
exceeded" today, failing loudly rather than being silently truncated or
accepted. Raising those specific numbers is an explicit, separate decision
left to the operator -- see the REPORT for the concrete memory/CPU/file-
count tradeoffs that decision carries.
"""

import hashlib
import re

import yaml

from osm_packages import reject_inline_credentials

MAX_SUBCHART_DEPTH = 2  # charts/<a>/ and charts/<a>/charts/<b>/ -- not deeper.

_APPROVED_TOP_NAMES = {'Chart.yaml', 'Chart.lock', 'values.schema.json', '.helmignore', 'README.md', 'LICENSE'}
_VALUES_RE = re.compile(r'values(?:-[a-zA-Z0-9_-]+)?\.ya?ml')
_SENSITIVE_NAMES = {'.git', '__pycache__', '.aws', '.ssh', 'credentials', 'secrets'}
_SENSITIVE_SUFFIXES = ('.key', '.pem', '.p12', '.pyc', '.swp', '.swo', '~')


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


def validate(files, parent_chart_meta, reviewed_dependencies):
    """Validate every `charts/...` entry in the already-captured `files`
    dict (see package.prepare()). Returns nothing on success; raises
    ValueError on any violation. A chart with no `charts/` entries AND no
    declared `dependencies:` at all is a complete no-op -- existing
    single-chart behavior is unaffected. A declared dependency with
    nothing vendored for it at all (zero charts/ files) is still caught
    below, not silently treated as "nothing to check."

    `reviewed_dependencies` is checked for CONSISTENCY with `files`, not
    AUTHENTICITY -- see the module docstring's "TRUST MODEL" section before
    calling this from any new, less-trusted caller than today's direct,
    offline test/package.prepare() usage.
    """
    vendored = {name: data for name, data in files.items() if name.startswith('charts/')}
    declared_top = _dependencies(parent_chart_meta)
    if not vendored and not declared_top:
        return
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
