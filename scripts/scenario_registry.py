"""Shared, read-only scenario specification. Importing this module has no side effects."""

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = Path("config/scenarios.json")
NAME = re.compile(r"^[a-z][a-z0-9_-]*$")


class RegistryError(ValueError):
    pass


def repository_path(root, relative):
    """Reject absolute paths, traversal and symlinks outside the repository."""
    path = Path(relative)
    root = Path(root).resolve()
    if path.is_absolute() or ".." in path.parts:
        raise RegistryError(f"Not a repository-relative path: {relative}")
    candidate = root / path
    if any(component.is_symlink() for component in (candidate, *candidate.parents) if component != root.parent):
        raise RegistryError(f"Symlink input path: {relative}")
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        raise RegistryError(f"Path escapes repository: {relative}")
    return resolved


def load_registry(root=REPO_ROOT):
    data = json.loads((Path(root) / REGISTRY_PATH).read_text())
    if data.get("schema_version") != 1:
        raise RegistryError("Unsupported scenario registry schema")
    keys, packages, releases = set(), set(), set()
    for scenario in data["scenarios"] + data.get("installer_components", []):
        key = scenario["key"]
        if not NAME.fullmatch(key) or key in keys:
            raise RegistryError(f"Invalid or duplicate scenario: {key}")
        keys.add(key)
        if not isinstance(scenario.get("pods"), list) or any(not isinstance(p, str) or not NAME.fullmatch(p) for p in scenario["pods"]):
            raise RegistryError(f"Invalid pod identifiers: {key}")
        selector = scenario.get("selector")
        if selector is not None and (not isinstance(selector, str) or not re.fullmatch(r"[a-zA-Z0-9_.\-/]+=[a-zA-Z0-9_.\-]+", selector)):
            raise RegistryError(f"Invalid selector: {key}")
        status = scenario["generation_status"]
        if status not in ("ready", "blocked"):
            raise RegistryError(f"Invalid generation status: {key}")
        if status == "blocked" and not scenario.get("blocked_reason"):
            raise RegistryError(f"Blocked scenario needs a reason: {key}")
        if status == "ready" and scenario.get("blocked_reason"):
            raise RegistryError(f"Ready scenario still has a blocking reason: {key}")
        if scenario["deployment_mechanism"] not in ("osm", "control-only"):
            raise RegistryError(f"Unknown deployment mechanism: {key}")
        for field in ("knf_package", "nsd_package"):
            package = scenario[field]
            if package is None and scenario["deployment_mechanism"] == "control-only":
                continue
            if not isinstance(package, str) or not NAME.fullmatch(package) or package in packages:
                raise RegistryError(f"Invalid or duplicate {field}: {package}")
            packages.add(package)
        if status == "ready":
            policy = scenario.get("validation_policy", {})
            if any(type(policy.get(field)) is not bool for field in ("split_ran", "forbid_hpa")):
                raise RegistryError(f"Missing validation policy: {key}")
        if status == "ready" and not scenario["charts"]:
            raise RegistryError(f"Ready scenario has no canonical charts: {key}")
        kdus = set()
        for chart in scenario["charts"] + scenario.get("observed_releases", []):
            if status == 'ready' and scenario in data['scenarios']:
                bindings = chart.get('image_bindings')
                if not isinstance(bindings, dict) or not bindings or any(
                        k not in ('image', 'edgeApp.image') or not isinstance(v, str) for k, v in bindings.items()):
                    raise RegistryError('Ready chart needs approved image bindings: ' + key)
            kdu = chart["kdu"]
            if not NAME.fullmatch(kdu) or kdu in kdus:
                raise RegistryError(f"Duplicate/invalid KDU in {key}: {kdu}")
            kdus.add(kdu)
            release = chart["release_name"]
            if not NAME.fullmatch(release) or release in releases:
                raise RegistryError(f"Duplicate/invalid release: {release}")
            releases.add(release)
            source = repository_path(root, chart["source"])
            # Installer-only input existence is checked on package selection/build;
            # dashboard/runtime configuration does not require those source trees.
            if scenario in data["scenarios"] and not (source / "Chart.yaml").is_file():
                raise RegistryError(f"Missing chart: {chart['source']}")
            for values in chart["values_files"]:
                profile = repository_path(root, values)
                if not profile.is_file() or not profile.is_relative_to(source):
                    raise RegistryError(f"Missing or external profile: {values}")
        for candidate in scenario.get("candidate_sources", []):
            if not (repository_path(root, candidate) / "Chart.yaml").is_file():
                raise RegistryError(f"Missing candidate chart: {candidate}")
        if status == "ready":
            review = scenario.get("reviewed_legacy", {})
            if not review.get("tree_sha256") or not review.get("archive_sha256"):
                raise RegistryError(f"Missing legacy-fix review: {key}")
            repository_path(root, review["path"])
    for alias, key in data["aliases"].items():
        if alias in keys or key not in keys:
            raise RegistryError(f"Invalid scenario alias: {alias} -> {key}")
    blocked_keys = set()
    for component in data["blocked_components"]:
        key = component["key"]
        if key in keys or key in blocked_keys or not component.get("reason"):
            raise RegistryError(f"Invalid blocked component: {key}")
        blocked_keys.add(key)
    return data


def select_scenarios(registry, keys=(), ready=False):
    """An explicit blocked request fails before any output is created."""
    if ready and keys:
        raise RegistryError("Choose explicit scenario keys or --ready, not both")
    if ready:
        return [s for s in registry["scenarios"] if s["generation_status"] == "ready"]
    if not keys:
        raise RegistryError("Specify scenario keys or --ready")
    entries = {s["key"]: s for s in registry["scenarios"] + registry.get("installer_components", [])}
    blocked = {s["key"]: s["reason"] for s in registry["blocked_components"]}
    selected = []
    for key in keys:
        key = registry["aliases"].get(key, key)
        if key in blocked:
            raise RegistryError(f"BLOCKED {key}: {blocked[key]}")
        if key not in entries:
            raise RegistryError(f"Unknown scenario: {key}")
        scenario = entries[key]
        if scenario["generation_status"] != "ready":
            raise RegistryError(f"BLOCKED {key}: {scenario['blocked_reason']}")
        if scenario not in selected:
            selected.append(scenario)
    return selected


def dashboard_scenarios(registry):
    """Keep existing keys and read-only inventory metadata, including blocked scenarios."""
    result = {}
    for scenario in registry["scenarios"]:
        charts = scenario["charts"] or scenario.get("observed_releases", [])
        result[scenario["key"]] = {
            "name": scenario["display_name"],
            "releases": [(c["release_name"], c["source"], c["values_files"]) for c in charts],
            "pods": scenario["pods"],
            "selector": scenario["selector"],
            "additive": scenario["additive"],
            "generation_status": scenario["generation_status"],
            "blocked_reason": scenario["blocked_reason"],
            "nsd_package": scenario["nsd_package"],
        }
    return result
