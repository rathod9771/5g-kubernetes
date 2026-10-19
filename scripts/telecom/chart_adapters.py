"""Reviewed, offline adaptation of specific upstream Helm template files.

External RAN/Core charts (e.g. free5GC) sometimes hardcode a container image
or omit required pod-security settings in a Helm test/lifecycle hook
template, bypassing the chart's own `values.yaml`-driven image contract
entirely (a literal `image: busybox` line cannot be fixed by `--set` or a
values overlay, since the template never references `.Values` for it at
all). This module is NOT a general template engine and does not parse or
understand Helm templating -- it performs a small number of byte-exact,
individually reviewed whole-file substitutions, each one scoped to an EXACT
(chart name, chart version, source commit) and an EXACT expected upstream
file digest.

Fail-closed properties:
  * Adaptation is opt-in per chart identity. A chart whose (name, version,
    commit) has no registered entry is returned byte-for-byte unchanged --
    this pipeline never assumes an arbitrary third-party chart can be
    adapted automatically (see pipeline REPORT "free5GC-specific
    limitations").
  * A registered patch only ever replaces a file whose CURRENT content
    matches the EXACT digest it was reviewed against. If the upstream file
    is missing, moved, or has changed at all, adaptation raises rather than
    silently applying a stale review to different content.
  * A patch may only target a path under `templates/`; it can never touch
    Chart.yaml, values.yaml, or add/remove files.
  * The replacement content is literal, final, reviewed bytes checked into
    this module -- never fetched, generated, or templated -- and it is NOT
    exempt from anything downstream: the adapted file set still passes
    through the exact same, unweakened `safe_chart_inputs()` extension/path
    allowlist and `multi_image.verify()` security checks as any organically
    authored template (see package.prepare()). Adaptation never bypasses
    safe_chart_inputs(); it only changes what the SAME checks see.
  * Because a replacement is a literal edit (not routed through
    `.Values`), a patched hook's image reference can legitimately be an
    immutable `repository@sha256` digest -- unlike this chart family's
    `.Values`-templated NF containers, which remain tag-based (see
    multi_image.py docstring) -- so an adapted hook can genuinely achieve
    digest-pinned execution for that one identity.
  * A chart/version/commit match alone is NOT sufficient: adaptation also
    requires `source_evidence` (threaded from pipeline.run(), which got it
    from execution.acquire()/helm_source() for THIS job) to show one of the
    VERIFIED_ACQUISITION_AUTHORITIES and the exact matching commit --
    declaration-only evidence, or none at all, is rejected (see adapt()'s
    docstring for the one honestly-disclosed residual limitation this does
    NOT close).

NOTE: no real free5GC adapter entry ships in this change. The actual
upstream free5GC checkout exists only on the Dell workstation and was never
fetched, cloned, or inspected from this (HP) environment. The ADAPTERS
registry below contains exactly one SYNTHETIC demonstration entry, matching
this repository's own offline test fixture (see
examples/telecom/multi-nf-chart/templates/unsafe.yaml and
tests/test_telecom_multi_image.py) -- it is not a validated patch for any
real upstream chart and must not be read as one.
"""

import hashlib

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


# Acquisition authorities that mean "this exact commit's tree was
# independently extracted and verified via git's own content-addressing"
# (see execution.acquire()). 'PROVIDER_DECLARED' (the fallback evidence for
# oci/helm sources, or any source never actually acquired) is deliberately
# excluded: it is a self-reported claim, not a verified one.
VERIFIED_ACQUISITION_AUTHORITIES = {'LOCAL_OBJECTS_VERIFIED', 'GITHUB_FETCH_VERIFIED'}


def adapt(files, manifest, source_evidence=None):
    """Return (adapted_files, provenance); `files` itself is never mutated.

    `adapted_files` is a NEW dict (a shallow copy of `files` with any
    matched patch targets replaced). `provenance` is a list of
    {'path', 'original_sha256', 'replacement_sha256'} records, one per file
    actually patched -- empty when no adapter matched this chart identity.

    `source_evidence` must be the evidence value `execution.acquire()` (or
    `execution.helm_source()`) itself produced for THIS job -- the caller
    (package.prepare(), threaded from pipeline.run()) is expected to pass
    through the same object it already computed from a real acquisition,
    never to construct one ad hoc. Adaptation only proceeds when its
    'authority' is one of VERIFIED_ACQUISITION_AUTHORITIES and its 'commit'
    equals the manifest's declared source commit -- a declaration-only
    authority, or a missing/mismatched one, is rejected outright, even if a
    registered adapter's (name, version, commit) key otherwise matches.

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
    enforced regardless of how trustworthy `source_evidence` is: adaptation
    can only ever substitute the fixed, literal, pre-reviewed `replacement`
    bytes checked into this module for a file whose CURRENT content matches
    `original_sha256` exactly -- fabricated evidence alone, without that
    exact byte match, achieves nothing.
    """
    if manifest.get('schema_version') != 2:
        return files, []
    try:
        chart_meta = yaml.safe_load(files.get('Chart.yaml', b'')) or {}
    except Exception:
        return files, []
    if not isinstance(chart_meta, dict):
        return files, []
    commit = (manifest.get('source') or {}).get('commit')
    key = (chart_meta.get('name'), chart_meta.get('version'), commit)
    entry = ADAPTERS.get(key)
    if entry is None:
        return files, []
    if (not commit or not isinstance(source_evidence, dict) or
            source_evidence.get('authority') not in VERIFIED_ACQUISITION_AUTHORITIES or
            source_evidence.get('commit') != commit):
        raise ValueError('Adaptation requires independently verified source acquisition evidence')
    adapted = dict(files)
    provenance = []
    for target, patch in entry.items():
        if not target.startswith('templates/'):
            raise ValueError('Adapter may only patch templates/: ' + target)
        if target not in files:
            raise ValueError('Adapter target file missing from chart: ' + target)
        if hashlib.sha256(files[target]).hexdigest() != patch['original_sha256']:
            raise ValueError('Adapter review is stale: upstream content changed for ' + target)
        adapted[target] = patch['replacement']
        provenance.append({'path': target, 'original_sha256': patch['original_sha256'],
                            'replacement_sha256': hashlib.sha256(patch['replacement']).hexdigest()})
    return adapted, provenance
