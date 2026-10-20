"""Operator-only, offline validation workflow for the real free5GC Helm
chart (github.com/free5gc/free5gc-helm, commit
d22485f5a95fd6bb97ec1252f0933ef5da17f96d, chart charts/free5gc v1.1.7).

Acquires ONLY charts/free5gc from the verified, pinned upstream commit via
execution.acquire()'s chart-scoped acquisition -- the upstream Git fetch
itself happens inside a fresh, disposable temporary clone that is deleted
when acquire() returns; nothing here ever writes back into any upstream
checkout. The already-reviewed free5GC MongoDB registry entries
(chart_adapters.py: REMOVALS/DEPENDENCY_REMOVALS keyed to this exact
chart identity/commit) are applied via the SAME functions package.prepare()
calls internally -- not a parallel or looser mechanism -- and the large
chart-capture profile is explicitly, deliberately selected (this is a
known, legitimately large real chart: ~300 files across 17+ vendored NF
subcharts, well over the 'default' profile's 128-file bound).

This module is NOT a generic, reusable capability and is intentionally
NOT imported by api.py, pipeline.py, or any other network-reachable
surface -- it is a fixed, operator-invoked CLI/library tool, in the same
spirit as this package's demo_*.py scripts (which also sit outside
pipeline.py's job-queue API). Nothing here ever publishes to OSM, deploys
to Kubernetes, or enables schema v2 publication/initial-deployment/
update/rollback; `deployment_eligible` in every report this module
produces is unconditionally False, regardless of how far validation gets.

package.prepare() is fail-closed AND fail-FAST: it stops at the very
first violation it finds, exactly as it does for every other chart this
pipeline has ever onboarded. This workflow calls it for real, with no
relaxed parameters, and reports exactly where it stops -- never retried,
never routed around, never weakened to "get further." To still produce an
ACTIONABLE report (the whole point of this module) covering blocker
categories prepare() didn't get far enough to reach on its own, this
module SEPARATELY performs a read-only, non-gating static survey: it
reapplies the same already-reviewed chart_adapters functions and runs its
own helm lint/template call (reusing package.read_command(), the same
bounded command runner prepare() itself uses) purely to describe what the
rendered chart actually contains. Every finding in that survey is
informational; none of it is a decision this module is authorized to
make, and none of it can ever turn into a passing validation.
"""
import argparse
import json
import tempfile
from collections import Counter
from pathlib import Path

import yaml

from .execution import acquire, Policy
from . import chart_adapters, package, subcharts

UPSTREAM_URL = 'https://github.com/free5gc/free5gc-helm'
UPSTREAM_COMMIT = 'd22485f5a95fd6bb97ec1252f0933ef5da17f96d'
CHART_SUBTREE = 'charts/free5gc'
CHART_NAME = 'free5gc'
CHART_VERSION = '1.1.7'


def _manifest(source, images):
    # Minimal schema_version=2 manifest: only the fields package.prepare()
    # itself reads (schema_version, source, images). This is deliberately
    # NOT a complete, contract.validate()-passing NF capability descriptor
    # -- plmn/tac/slices/interfaces/roles describe a deployable network
    # function's OWN radio/network configuration, which is irrelevant to
    # whether this Helm chart's content passes security validation. The
    # real free5GC CLI entry point always passes images=None (-> []): no
    # reviewed role/image contract exists for the real upstream chart, and
    # fabricating plausible-looking entries would misrepresent it as
    # having one. `images` is only ever overridden by this module's own
    # offline tests, to drive a genuine, fully-declared positive case.
    return {'schema_version': 2, 'name': CHART_NAME, 'source': source,
            'images': images if images is not None else [], 'roles': []}


def _read_tree(root):
    files = {}
    for path in Path(root).rglob('*'):
        if path.is_file():
            files[str(path.relative_to(root))] = path.read_bytes()
    return files


def _reapply_adaptations(raw_files, manifest, evidence):
    """Reapplies the SAME already-reviewed chart_adapters functions
    package.prepare() calls internally, in the same order. Needed because
    prepare() itself raises (by design, fail-fast) before ever returning a
    provenance record for a chart that doesn't pass validation -- this is
    the only way this module's report can describe what those registry
    entries actually did, for a chart prepare() never finishes preparing."""
    files, modified = chart_adapters.adapt(raw_files, manifest, evidence)
    files, overrides = chart_adapters.apply_values_overrides(files, manifest, evidence)
    files, removals = chart_adapters.apply_dependency_removals(files, manifest, evidence)
    return files, {'template_or_file_adaptations': modified, 'values_overrides': overrides,
                    'dependency_removals': removals}


def _pod_containers(document):
    if document.get('kind') == 'Pod':
        return (document.get('spec') or {}).get('containers', []), (document.get('spec') or {}).get('initContainers', [])
    template_spec = ((document.get('spec') or {}).get('template') or {}).get('spec') or {}
    return template_spec.get('containers', []), template_spec.get('initContainers', [])


def _static_survey(files, helm):
    """Read-only, non-gating: never raises, every field is informational.
    Reflects the SAME categories package.prepare()'s real checks enforce
    (see multi_image.py's FORBIDDEN_KINDS/pod-security bar), gathered
    independently so the report stays actionable even where prepare()
    itself stops short of rendering anything."""
    survey = {
        'embedded_certificate_key_files': sorted(n for n in files if n.startswith('cert/')),
    }
    try:
        parent_meta = yaml.safe_load(files.get('Chart.yaml', b'')) or {}
    except Exception:
        parent_meta = {}
    if not isinstance(parent_meta, dict):
        parent_meta = {}
    survey['top_level_mongodb_dependency'] = [
        d for d in (parent_meta.get('dependencies') or []) if isinstance(d, dict) and d.get('name') == 'mongodb'
    ]
    survey['vendored_subchart_directories'] = sorted({
        n.split('/', 2)[1] for n in files if n.startswith('charts/') and len(n.split('/')) > 2
    })
    with tempfile.TemporaryDirectory(prefix='free5gc-validate-render-') as render_dir:
        for name, data in files.items():
            target = Path(render_dir) / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        try:
            package.read_command([helm, 'lint', render_dir, '--strict'])
            survey['helm_lint'] = 'PASSED'
        except Exception as error:
            survey['helm_lint'] = 'FAILED: ' + str(error)
        try:
            rendered = package.read_command(
                [helm, 'template', 'free5gc-validation', render_dir, '--namespace', 'research-sandbox'])
        except Exception as error:
            survey['helm_template'] = 'FAILED: ' + str(error)
            return survey
        survey['helm_template'] = 'PASSED'
        documents = [d for d in yaml.safe_load_all(rendered) if isinstance(d, dict)]
        survey['resource_kinds'] = dict(Counter(d.get('kind') for d in documents))
        survey['helm_hooks'] = sorted(
            (d.get('kind'), (d.get('metadata') or {}).get('name'),
             (d.get('metadata') or {}).get('annotations', {}).get('helm.sh/hook'))
            for d in documents if (d.get('metadata') or {}).get('annotations', {}).get('helm.sh/hook'))
        survey['secret_resources'] = sorted(
            (d.get('metadata') or {}).get('name') for d in documents if d.get('kind') == 'Secret')
        survey['persistent_volumes'] = sorted(
            (d.get('metadata') or {}).get('name') for d in documents if d.get('kind') == 'PersistentVolume')
        survey['statefulsets_with_volume_claim_templates'] = sorted(
            (d.get('metadata') or {}).get('name') for d in documents
            if d.get('kind') == 'StatefulSet' and 'volumeClaimTemplates' in (d.get('spec') or {}))
        images = set(); mutable_images = set(); pod_security = Counter()
        for document in documents:
            if document.get('kind') not in ('Deployment', 'StatefulSet', 'DaemonSet', 'Job', 'CronJob', 'Pod'):
                continue
            containers, init_containers = _pod_containers(document)
            for container in list(containers) + list(init_containers):
                image = container.get('image')
                if image:
                    images.add(image)
                    if '@sha256:' not in image:
                        mutable_images.add(image)
                security = container.get('securityContext') or {}
                if security.get('runAsNonRoot') is not True:
                    pod_security['missing_runAsNonRoot'] += 1
                if (security.get('capabilities') or {}).get('drop') != ['ALL']:
                    pod_security['missing_capability_drop_all'] += 1
                if security.get('allowPrivilegeEscalation') is not False:
                    pod_security['allow_privilege_escalation_not_pinned_false'] += 1
                if (security.get('capabilities') or {}).get('add'):
                    pod_security['adds_capabilities'] += 1
                if security.get('privileged'):
                    pod_security['privileged'] += 1
        survey['image_inventory'] = {'unique_images': sorted(images), 'mutable_tag_references': sorted(mutable_images)}
        survey['pod_security_findings'] = dict(pod_security)
    return survey


def validate(workspace, helm='helm', *, upstream_url=UPSTREAM_URL, upstream_commit=UPSTREAM_COMMIT,
             chart_subtree=CHART_SUBTREE, policy=None, images=None):
    """The one entry point this module exposes. Returns the structured
    report (source verification, adaptations applied, adaptation
    provenance, dependency validation results, helm lint/template
    results, image inventory findings, pod-security findings, blocking
    errors, deployment eligibility). Never raises for an EXPECTED
    chart-content blocker -- those are captured into the report's own
    'blocking_errors' field -- only for a genuine acquisition/environment
    failure (network unavailable, policy denial, workspace misuse)."""
    policy = policy or Policy({'github_repositories': [upstream_url]})
    source = {'kind': 'github', 'url': upstream_url, 'commit': upstream_commit}
    destination = Path(workspace) / 'chart'
    evidence = acquire(source, workspace, destination, policy, chart_path=chart_subtree)

    report = {
        'source_verification': {
            'repository': upstream_url,
            'verified_commit': evidence['commit'],
            'chart_path': evidence['chart_path'],
            'acquisition_authority': evidence['authority'],
        },
        'adaptations_applied': None,
        'adaptation_provenance': None,
        'dependency_validation_results': None,
        'helm_lint_template_results': None,
        'image_inventory_findings': None,
        'pod_security_findings': None,
        'blocking_errors': [],
        'deployment_eligible': False,
    }

    manifest = _manifest(source, images)
    raw_files = _read_tree(destination)

    # THE real, unweakened, fail-closed gate -- the authoritative result.
    # Never retried with relaxed parameters if it raises.
    try:
        package.prepare(workspace, 'chart', manifest, helm, chart_profile='large', source_evidence=evidence)
        report['blocking_errors'] = []
    except Exception as error:
        report['blocking_errors'].append({'stage': 'package.prepare', 'error_type': type(error).__name__,
                                           'message': str(error)})

    # Independent, read-only re-derivation (SAME reviewed functions) --
    # needed only because prepare() raised before returning anything, and
    # purely so the rest of the report stays populated and actionable.
    adapted, provenance = _reapply_adaptations(raw_files, manifest, evidence)
    report['adaptations_applied'] = {
        'files_modified_or_removed': provenance['template_or_file_adaptations'],
        'values_overrides': provenance['values_overrides'],
        'dependency_removals': provenance['dependency_removals'],
    }
    report['adaptation_provenance'] = provenance

    try:
        parent_meta = yaml.safe_load(adapted.get('Chart.yaml', b'')) or {}
    except Exception:
        parent_meta = {}
    if not isinstance(parent_meta, dict):
        parent_meta = {}
    try:
        key = subcharts.validate(adapted, parent_meta, None, commit=evidence['commit'], source_evidence=evidence)
        report['dependency_validation_results'] = {'result': 'PASSED', 'registry_key': key}
    except Exception as error:
        report['dependency_validation_results'] = {'result': 'FAILED', 'error_type': type(error).__name__,
                                                     'message': str(error)}

    survey = _static_survey(adapted, helm)
    report['helm_lint_template_results'] = {k: survey[k] for k in
                                             ('helm_lint', 'helm_template', 'resource_kinds', 'helm_hooks')
                                             if k in survey}
    report['image_inventory_findings'] = survey.get('image_inventory')
    report['pod_security_findings'] = survey.get('pod_security_findings')
    report['embedded_certificate_key_files'] = survey['embedded_certificate_key_files']
    report['top_level_mongodb_dependency'] = survey['top_level_mongodb_dependency']
    report['vendored_subchart_directories_without_reviewed_inventory'] = survey['vendored_subchart_directories']
    report['unsupported_resource_kinds'] = {
        'secret_resources': survey.get('secret_resources', []),
        'persistent_volumes': survey.get('persistent_volumes', []),
        'statefulsets_with_volume_claim_templates': survey.get('statefulsets_with_volume_claim_templates', []),
    }
    # Never conditional on anything above: this workflow is read-only
    # validation only, never an approval mechanism.
    report['deployment_eligible'] = False
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--helm', default='helm')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='free5gc-validate-') as workspace:
        report = validate(workspace, args.helm)
    Path(args.output).write_text(json.dumps(report, indent=2, default=str) + '\n')
    print(json.dumps(report, indent=2, default=str))


if __name__ == '__main__':
    main()
