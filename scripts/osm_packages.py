#!/usr/bin/env python3
"""Local-only deterministic RAN packaging. Never contacts Kubernetes or OSM."""

import argparse
import gzip
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import platform
import zlib
import re
from local_safety import exclusive_lock, atomic_write
from pathlib import Path

import yaml

from scenario_registry import (
    REPO_ROOT, RegistryError, load_registry, repository_path, select_scenarios,
)

FORMAT_VERSION = 2
KUBE_VERSION = "1.29.15"
DEFAULT_OUTPUT = REPO_ROOT / "build/osm-packages"


class PackageError(ValueError):
    pass


def json_bytes(value):
    # JSON is valid YAML, and avoids serializer-version-dependent archive bytes.
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode()


def sha256(value):
    return hashlib.sha256(value).hexdigest()


def read_tree(directory):
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise PackageError(f"Missing directory or symlink: {directory}")
    result = {}
    for file in sorted(directory.rglob("*")):
        if file.is_symlink():
            raise PackageError(f"Symlinks are not package inputs: {file}")
        if file.is_file():
            result[file.relative_to(directory).as_posix()] = file.read_bytes()
        elif not file.is_dir():
            raise PackageError(f"Non-regular package input: {file}")
    return result


def tree_digest(files):
    hashes = {name: sha256(content) for name, content in files.items()}
    return sha256(json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode())


def verify_legacy_review(root, scenario):
    """Unknown package-only edits must be reconciled, never silently discarded."""
    review = scenario["reviewed_legacy"]
    directory = repository_path(root, review["path"])
    archive = Path(str(directory) + ".tar.gz")
    if tree_digest(read_tree(directory)) != review["tree_sha256"]:
        raise PackageError(f"{scenario['key']}: legacy staging changed since fix review; reconcile package-only changes first")
    if not archive.is_file() or archive.is_symlink() or sha256(archive.read_bytes()) != review["archive_sha256"]:
        raise PackageError(f"{scenario['key']}: legacy archive changed since fix review; reconcile first")


def helm_effective_values(files, profiles=()):
    """Ask Helm to parse and coalesce values; never approximate Helm in Python."""
    with tempfile.TemporaryDirectory(prefix="helm-values-") as directory:
        root = Path(directory)
        probe = root / "probe"
        # Dependency values need explicit handling before admitting such charts.
        metadata = mapping(files["Chart.yaml"], "Chart.yaml")
        if metadata.get("dependencies") or any(n.startswith("charts/") for n in files):
            raise PackageError("Dependency charts require a reviewed baking implementation")
        for name in ("Chart.yaml", "values.yaml"):
            if name in files:
                (probe / name).parent.mkdir(parents=True, exist_ok=True)
                (probe / name).write_bytes(files[name])
        (probe / "templates").mkdir()
        (probe / "templates/probe.yaml").write_text(
            'apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: values-probe\n'
            'data:\n  resolved: {{ .Values | toJson | quote }}\n')
        args = ["template", "values-probe", str(probe), "--kube-version", KUBE_VERSION]
        for index, content in enumerate(profiles):
            path = root / (str(index) + ".yaml")
            path.write_bytes(content)
            args += ["-f", str(path)]
        doc = yaml.safe_load(helm_run(args))
        value = json.loads(doc["data"]["resolved"])
        if not isinstance(value, dict):
            raise PackageError("Helm values must resolve to a mapping")
        return value


def reject_inline_credentials(value):
    if isinstance(value, dict):
        for key, content in value.items():
            normalized = str(key).lower().replace("_", "").replace("-", "")
            if normalized in {"password", "passwd", "token", "apikey", "privatekey", "clientsecret", "credentials", "secret"} and content:
                raise PackageError("Inline credentials require external secret configuration")
            reject_inline_credentials(content)
    elif isinstance(value, list):
        for content in value:
            reject_inline_credentials(content)


def safe_chart_inputs(files, reviewed=None):
    """Fail closed on local debris even when .helmignore happens to hide it."""
    for name in files:
        if reviewed is not None:
            # A vendored multi-chart tree is accepted only as an exact, reviewed
            # byte inventory. Generic RAN input rules remain fail-closed.
            if set(files) != set(reviewed) or sha256(files[name]) != reviewed.get(name):
                raise PackageError("Reviewed vendored chart changed: " + name)
            if Path(name).is_absolute() or ".." in Path(name).parts or "\\" in name:
                raise PackageError("Unsafe reviewed chart path")
            if any(b"PRIVATE KEY-----" in line and line.strip() != b"## -----BEGIN RSA PRIVATE KEY-----" for line in files[name].splitlines()):
                raise PackageError("Private key material is not a chart input")
            continue
        path = Path(name)
        parts = [part.lower() for part in path.parts]
        sensitive = any((part.startswith(".") and part != ".helmignore") or part.startswith(("secret", ".#")) or part.endswith("#")
                        or part in {".git", "__pycache__", ".aws", ".ssh", "credentials", "secrets"}
                        or part.startswith(".env") or "credential" in part
                        or part.endswith((".key", ".pem", ".p12", ".pyc", ".swp", ".swo", "~"))
                        for part in parts)
        if path.is_absolute() or ".." in path.parts or "\\" in name or any(ord(c) < 32 for c in name) or sensitive:
            raise PackageError("Sensitive or unsafe chart input: " + name)
        approved = (name in {"Chart.yaml", "Chart.lock", ".helmignore", "README.md", "LICENSE"}
                    or re.fullmatch(r"values(?:-[a-zA-Z0-9_-]+)?\.ya?ml", name)
                    or (path.parts[0] == "templates" and
                        (path.suffix in {".yaml", ".yml", ".tpl", ".json"} or path.name == "NOTES.txt")))
        if not approved:
            raise PackageError("Unexpected chart input requires review: " + name)
        if b"PRIVATE KEY-----" in files[name]:
            raise PackageError("Private key material is not a chart input")
        if path.name.startswith("values") and path.suffix in {".yaml", ".yml"}:
            reject_inline_credentials(yaml.safe_load(files[name]))


def helm_packaged_files(files, reviewed=None):
    """Helm itself applies .helmignore and its standard packaging exclusions."""
    safe_chart_inputs(files, reviewed)
    metadata = mapping(files.get("Chart.yaml", b"{}"), "Chart.yaml")
    for field in ("name", "version"):
        value = metadata.get(field)
        if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.+\-]*", value):
            raise PackageError("Unsafe chart metadata: " + field)
    with tempfile.TemporaryDirectory(prefix="helm-package-") as directory:
        source = Path(directory) / "chart"
        for name, content in files.items():
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        helm_run(["package", str(source), "--destination", directory])
        archives = list(Path(directory).glob("*.tgz"))
        if len(archives) != 1:
            raise PackageError("Helm did not produce exactly one chart")
        result = {}
        with tarfile.open(archives[0], "r:gz") as archive:
            for member in archive:
                path = Path(member.name)
                if path.is_absolute() or ".." in path.parts or not (member.isfile() or member.isdir()):
                    raise PackageError("Unsafe packaged chart member")
                if member.isfile():
                    name = Path(*path.parts[1:]).as_posix()
                    result[name] = archive.extractfile(member).read()
        return result


def mapping(content, label):
    value = yaml.safe_load(content)
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise PackageError(f"Expected a YAML mapping: {label}")
    return value


def chart_files(root, chart):
    source = repository_path(root, chart["source"])
    files = read_tree(source)
    if "Chart.yaml" not in files or not any(f.startswith("templates/") for f in files):
        raise PackageError(f"Missing Chart.yaml/templates: {source}")
    safe_chart_inputs(files, chart.get("reviewed_inputs"))
    inputs = {f"{chart['source']}/{name}": sha256(data) for name, data in files.items()}
    profiles = []
    for relative in chart["values_files"]:
        content = repository_path(root, relative).read_bytes()
        inputs[relative] = sha256(content)
        profiles.append(content)
    packaged = helm_packaged_files(files, chart.get("reviewed_inputs"))
    if "Chart.yaml" not in packaged or not any(n.startswith("templates/") for n in packaged):
        raise PackageError("Helm exclusions removed required chart files")
    if chart.get("reviewed_inputs") is not None:
        # Reference reproduction preserves vendored defaults verbatim. Do not
        # attempt dependency/profile baking or normalize the working chart.
        if profiles:
            raise PackageError("Reviewed reference chart does not accept profile baking")
        return packaged, inputs
    # Resolve against packaged defaults, as a real Helm installation does.
    values = helm_effective_values(packaged, profiles)
    reject_inline_credentials(values)
    files = packaged
    files["values.yaml"] = json_bytes(values)
    return files, inputs


def descriptors(scenario):
    knf, nsd, member = scenario["knf_package"], scenario["nsd_package"], scenario["member_id"]
    vnfd = {"vnfd": {
        "id": knf, "product-name": knf, "provider": "Amrita5G",
        "version": scenario["descriptor_version"],
        "description": f"Generated from canonical Helm sources: {scenario['display_name']}",
        "df": [{"id": "default-df"}],
        "ext-cpd": [{"id": "mgmt-ext", "k8s-cluster-net": "net1"}],
        "k8s-cluster": {"nets": [{"id": "net1"}]},
        "kdu": [{"name": c["kdu"], "helm-chart": c["kdu"]} for c in scenario["charts"]],
        "mgmt-cp": "mgmt-ext",
    }}
    ns = {"nsd": {"nsd": [{
        "id": nsd, "name": nsd, "version": scenario["descriptor_version"],
        "description": f"Generated NS for {scenario['display_name']}", "designer": "Amrita5G",
        "df": [{"id": "default-df", "vnf-profile": [{
            "id": member, "vnfd-id": knf,
            "virtual-link-connectivity": [{
                "constituent-cpd-id": [{"constituent-base-element-id": member, "constituent-cpd-id": "mgmt-ext"}],
                "virtual-link-profile-id": "net1",
            }],
        }]}],
        "virtual-link-desc": [{"id": "net1", "mgmt-network": True}], "vnfd-id": [knf],
    }]}}
    return vnfd, ns


def validate_references(scenario, files):
    knf, nsd = scenario["knf_package"], scenario["nsd_package"]
    try:
        v = mapping(files[f"{knf}/{knf}_vnfd.yaml"], knf)["vnfd"]
        n = mapping(files[f"{nsd}/{nsd}_nsd.yaml"], nsd)["nsd"]["nsd"][0]
        expected_kdus = [{"name": c["kdu"], "helm-chart": c["kdu"]} for c in scenario["charts"]]
        member = n["df"][0]["vnf-profile"][0]
        cp = member["virtual-link-connectivity"][0]["constituent-cpd-id"][0]
        valid = (
            v["id"] == knf and v["product-name"] == knf and v["kdu"] == expected_kdus
            and v["version"] == scenario["descriptor_version"]
            and n["id"] == nsd and n["name"] == nsd and n["vnfd-id"] == [knf]
            and n["version"] == scenario["descriptor_version"]
            and member["vnfd-id"] == knf and member["id"] == scenario["member_id"]
            and cp["constituent-base-element-id"] == member["id"] and cp["constituent-cpd-id"] == v["mgmt-cp"]
        )
        for kdu in v["kdu"]:
            valid = valid and f"{knf}/helm-chart-v3s/{kdu['helm-chart']}/Chart.yaml" in files
        if not valid:
            raise PackageError(f"{scenario['key']}: NSD/KNF/KDU reference mismatch")
    except (KeyError, IndexError, TypeError) as error:
        raise PackageError(f"{scenario['key']}: missing/invalid NSD/KNF reference: {error}") from error


def archive_bytes(package, files):
    """Sorted USTAR + fixed gzip header, ownership, times and permission modes."""
    tar_buffer = io.BytesIO()
    if not re.fullmatch(r"[a-z][a-z0-9_-]*", package):
        raise PackageError("Unsafe package name")
    for name in files:
        if Path(name).is_absolute() or ".." in Path(name).parts or not name.startswith(package + "/"):
            raise PackageError("Unsafe archive path")
    directories = {package}
    for name in files:
        directories.update(p.as_posix() for p in Path(name).parents if p.as_posix() != ".")
    with tarfile.open(fileobj=tar_buffer, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name in sorted(directories | set(files)):
            info = tarfile.TarInfo(name)
            info.uid = info.gid = info.mtime = 0
            info.uname = info.gname = ""
            if name in directories:
                info.type, info.mode = tarfile.DIRTYPE, 0o755
                archive.addfile(info)
            else:
                info.mode = 0o755 if name.endswith(".sh") else 0o644
                info.size = len(files[name])
                archive.addfile(info, io.BytesIO(files[name]))
    output = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=output, compresslevel=9, mtime=0) as compressed:
        compressed.write(tar_buffer.getvalue())
    return output.getvalue()


def expected_artifacts(root, scenario):
    verify_legacy_review(root, scenario)
    knf, nsd = scenario["knf_package"], scenario["nsd_package"]
    files, inputs = {}, {}
    for chart in scenario["charts"]:
        tree, hashes = chart_files(root, chart)
        inputs.update(hashes)
        files.update({f"{knf}/helm-chart-v3s/{chart['kdu']}/{name}": data for name, data in tree.items()})
    v, n = descriptors(scenario)
    files[f"{knf}/{knf}_vnfd.yaml"] = json_bytes(v)
    files[f"{nsd}/{nsd}_nsd.yaml"] = json_bytes(n)
    validate_references(scenario, files)
    archives = {
        package + ".tar.gz": archive_bytes(package, {name: data for name, data in files.items() if name.startswith(package + "/")})
        for package in (knf, nsd)
    }
    provenance = {
        "format_version": FORMAT_VERSION,
        "registry_sha256": sha256((Path(root) / "config/scenarios.json").read_bytes()),
        "generator": {"version": "2", "implementation_sha256": {
            name: sha256((REPO_ROOT / "scripts" / name).read_bytes())
            for name in ("osm_packages.py", "scenario_registry.py", "local_safety.py")}},
        "toolchain": {"helm": helm_run(["version", "--short"]).strip(),
                      "python": platform.python_version(), "zlib": zlib.ZLIB_RUNTIME_VERSION, "pyyaml": yaml.__version__,
                      "compression": "gzip-deflate-9", "archive": "USTAR"},
        "profile_sha256": {name: inputs[name] for c in scenario["charts"] for name in c["values_files"]},
        "descriptor_sha256": {name: sha256(data) for name, data in files.items()
                              if name.endswith(("_vnfd.yaml", "_nsd.yaml"))},
        "scenario": scenario["key"], "scenario_sha256": sha256(json_bytes(scenario)),
        "kubernetes_render_version": KUBE_VERSION,
        "source_inputs": inputs,
        "staging_sha256": {name: sha256(data) for name, data in files.items()},
        "archives_sha256": {name: sha256(data) for name, data in archives.items()},
    }
    return files | archives | {scenario["key"] + ".provenance.json": json_bytes(provenance)}


def safe_output(root, output):
    root, output = Path(root).resolve(), Path(output).absolute()
    for component in (output, *output.parents):
        if component.is_symlink():
            raise PackageError("Symlinks are not output paths")
    output = output.resolve()
    allowed = root / "build/osm-packages"
    if root.is_relative_to(output) or (output.is_relative_to(root) and not output.is_relative_to(allowed)):
        raise PackageError("Output must be build/osm-packages (or a separate directory outside the repository); legacy inputs are read-only")
    # Do not let a symlink in an output subtree redirect writes to another tree.
    if output.exists():
        for file in output.rglob("*"):
            if file.is_symlink():
                raise PackageError(f"Symlinks are not output paths: {file}")
    return output


def published_output(output):
    output = Path(output)
    pointer = output / "CURRENT"
    if not pointer.exists():
        return output
    if pointer.is_symlink():
        raise PackageError("Unsafe publication pointer")
    name = pointer.read_text().strip()
    if not re.fullmatch(r"[a-f0-9]{64}", name):
        raise PackageError("Invalid publication pointer")
    return output / "releases" / name


def compare_provenance(stored_bytes, expected_bytes):
    """Strict format-2 content identity; return audit-only JSON pointer changes."""
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise PackageError('Duplicate provenance field')
            result[key] = value
        return result
    try:
        stored = json.loads(stored_bytes, object_pairs_hook=unique_object)
        expected = json.loads(expected_bytes, object_pairs_hook=unique_object)
    except (ValueError, UnicodeError) as error:
        raise PackageError('Invalid provenance JSON') from None
    fields = {'format_version', 'registry_sha256', 'generator', 'toolchain', 'profile_sha256',
              'descriptor_sha256', 'scenario', 'scenario_sha256', 'kubernetes_render_version',
              'source_inputs', 'staging_sha256', 'archives_sha256'}
    hash_maps = ('profile_sha256', 'descriptor_sha256', 'source_inputs', 'staging_sha256', 'archives_sha256')
    def digest(value):
        return isinstance(value, str) and re.fullmatch(r'[a-f0-9]{64}', value) is not None
    for provenance in (stored, expected):
        if not isinstance(provenance, dict) or set(provenance) != fields:
            raise PackageError('Missing or unknown provenance schema fields')
        if type(provenance['format_version']) is not int or provenance['format_version'] != FORMAT_VERSION:
            raise PackageError('Unknown provenance format/version')
        if not all(digest(provenance[k]) for k in ('registry_sha256', 'scenario_sha256')):
            raise PackageError('Invalid provenance identity hash')
        for key in hash_maps:
            value = provenance[key]
            if not isinstance(value, dict) or not all(isinstance(k, str) and digest(v) for k, v in value.items()):
                raise PackageError('Invalid provenance hash map: ' + key)
        if not all(isinstance(provenance[k], str) and provenance[k] for k in ('scenario', 'kubernetes_render_version')):
            raise PackageError('Invalid provenance scenario/render identity')
        generator = provenance['generator']
        if not isinstance(generator, dict) or set(generator) != {'version', 'implementation_sha256'} or generator['version'] != '2':
            raise PackageError('Unknown provenance generator schema/version')
        implementation = generator['implementation_sha256']
        if not isinstance(implementation, dict) or set(implementation) != {'local_safety.py', 'osm_packages.py', 'scenario_registry.py'} or not all(digest(v) for v in implementation.values()):
            raise PackageError('Missing or unknown generator implementation schema')
        toolchain = provenance['toolchain']
        if not isinstance(toolchain, dict) or set(toolchain) != {'helm', 'python', 'zlib', 'pyyaml', 'compression', 'archive'} or not all(isinstance(v, str) and v for v in toolchain.values()):
            raise PackageError('Missing or unknown provenance toolchain schema')
    # Whole-registry edits are audit-only only AFTER the resolved scenario and
    # every source/profile/descriptor/staging/archive identity prove unchanged.
    for key in sorted(fields - {'generator', 'toolchain', 'registry_sha256'}):
        if stored[key] != expected[key]:
            raise PackageError('Payload provenance drift: ' + key)
    changes = []
    if stored['registry_sha256'] != expected['registry_sha256']:
        changes.append('/registry_sha256')
    for key in sorted(stored['toolchain']):
        if stored['toolchain'][key] != expected['toolchain'][key]:
            changes.append('/toolchain/' + key)
    for key in sorted(stored['generator']['implementation_sha256']):
        if stored['generator']['implementation_sha256'][key] != expected['generator']['implementation_sha256'][key]:
            changes.append('/generator/implementation_sha256/' + key)
    return changes


def validate_artifacts(root, output, scenario):
    expected = expected_artifacts(root, scenario)
    output_root = safe_output(root, output)
    output = published_output(output_root)
    if (output_root / "CURRENT").exists() and tree_digest(read_tree(output)) != (output_root / "CURRENT").read_text().strip():
        raise PackageError("Immutable publication was modified")
    actual = {}
    for package in (scenario["knf_package"], scenario["nsd_package"]):
        actual.update({f"{package}/{name}": data for name, data in read_tree(output / package).items()})
    validate_references(scenario, actual)
    for name in expected:
        if "/" not in name:
            file = output / name
            if not file.is_file() or file.is_symlink():
                raise PackageError(f"{scenario['key']}: missing generated file: {name}")
            actual[name] = file.read_bytes()
    missing, extra = expected.keys() - actual.keys(), actual.keys() - expected.keys()
    provenance_name = scenario['key'] + '.provenance.json'
    changed = {name for name in expected.keys() & actual.keys() if name != provenance_name and expected[name] != actual[name]}
    if missing or extra or changed:
        raise PackageError(f"{scenario['key']}: source/staging/archive drift; missing={sorted(missing)}, extra={sorted(extra)}, changed={sorted(changed)}")
    audit_changes = compare_provenance(actual[provenance_name], expected[provenance_name])
    if audit_changes:
        print(scenario['key'] + ': payload integrity verified; audit metadata differs: ' + ', '.join(audit_changes) + '; stored provenance preserved', file=sys.stderr)
    return actual


def helm_command():
    # HELM_BIN can bypass a host-specific wrapper; no Helm repositories are used.
    command = os.environ.get("HELM_BIN", "helm")
    if not shutil.which(command):
        raise PackageError(f"Helm binary not found: {command}")
    return command


def helm_run(arguments):
    environment = dict(os.environ, KUBECONFIG=os.devnull)
    process = subprocess.run([helm_command(), *arguments], capture_output=True, text=True, env=environment, timeout=60)
    if process.returncode:
        raise PackageError(f"Helm {' '.join(arguments)} failed:\n{process.stdout}{process.stderr}")
    return process.stdout


def render(chart_path, release, values=()):
    options = [option for value in values for option in ("-f", str(value))]
    helm_run(["lint", str(chart_path), "--strict", *options])
    result = helm_run(["template", release, str(chart_path), "--namespace", "reproducibility-check", "--kube-version", KUBE_VERSION, *options])
    documents = [doc for doc in yaml.safe_load_all(result) if doc]
    if not documents or any(not isinstance(d, dict) or "kind" not in d or "metadata" not in d for d in documents):
        raise PackageError(f"Invalid/empty Helm rendering: {chart_path}")
    identities = [(d["kind"], d["metadata"]["name"]) for d in documents]
    if len(identities) != len(set(identities)):
        raise PackageError(f"Duplicate rendered resource names: {chart_path}")
    return documents


def check_helm(root, scenario, expected):
    """Prove that baking profiles preserves canonical rendered objects and fixes."""
    knf = scenario["knf_package"]
    all_resources = set()
    with tempfile.TemporaryDirectory(prefix="osm-package-check-") as temporary:
        temporary = Path(temporary)
        for chart in scenario["charts"]:
            source = repository_path(root, chart["source"])
            profiles = [repository_path(root, p) for p in chart["values_files"]]
            canonical = render(source, chart["release_name"], profiles)
            prefix = f"{knf}/helm-chart-v3s/{chart['kdu']}/"
            destination = temporary / chart["kdu"]
            for name, data in expected.items():
                if name.startswith(prefix):
                    file = destination / name[len(prefix):]
                    file.parent.mkdir(parents=True, exist_ok=True)
                    file.write_bytes(data)
            generated = render(destination, chart["release_name"])
            if canonical != generated:
                raise PackageError(f"{scenario['key']}/{chart['kdu']}: baked profile changes Helm rendering")
            deployments = [d for d in generated if d["kind"] == "Deployment"]
            if not deployments:
                raise PackageError(f"{scenario['key']}: no Deployment in {chart['kdu']}")
            if scenario["validation_policy"]["split_ran"]:
                expected_name = chart["release_name"]
                if [d["metadata"]["name"] for d in deployments] != [expected_name]:
                    raise PackageError(f"{scenario['key']}: deployment/profile identity mismatch")
                effective_values = mapping(expected[prefix + "values.yaml"], prefix + "values.yaml")
                for deployment in deployments:
                    template = deployment["spec"]["template"]
                    annotation = template["metadata"].get("annotations", {})
                    if annotation.get("sidecar.istio.io/inject") != "false":
                        raise PackageError(f"{scenario['key']}: lost RAN Istio opt-out fix")
                    if template["metadata"]["labels"].get("ran-type") != scenario["ran_type"]:
                        raise PackageError(f"{scenario['key']}: RAN type/profile mismatch")
                    if "resources" in effective_values and template["spec"]["containers"][0].get("resources") != effective_values["resources"]:
                        raise PackageError(f"{scenario['key']}: resource profile was ignored")
                config = "\n".join(str(value) for d in generated if d["kind"] == "ConfigMap" for value in d.get("data", {}).values())
                if "POD_IP" not in config:
                    raise PackageError(f"{scenario['key']}: lost Pod-IP substitution fix")
                if chart["kdu"] == "cu":
                    slice_marker = "sd: 66051" if scenario["implementation"] == "srsRAN" else "sd = 0x010203"
                    if "getent hosts amf-ngap-stable" not in config or slice_marker not in config:
                        raise PackageError(f"{scenario['key']}: lost stable-AMF or slice fix")
                if chart["kdu"] == "du" and f"getent hosts {effective_values['cuFullname']}" not in config:
                    raise PackageError(f"{scenario['key']}: DU discovery does not follow CU profile")
            for document in generated:
                if document["kind"] == "Secret":
                    raise PackageError("Embedded Secret resources require external secret configuration")
                identity = (document["kind"], document["metadata"]["name"])
                if identity in all_resources:
                    raise PackageError(f"{scenario['key']}: cross-KDU resource collision: {identity}")
                all_resources.add(identity)
                if scenario["validation_policy"]["forbid_hpa"] and document["kind"] == "HorizontalPodAutoscaler":
                    raise PackageError(f"{scenario['key']}: latest vCRAN profiles must not contain HPA")


def source_snapshot(root, scenarios, destination):
    """Capture bytes once; reject changes during capture, then use only the copy."""
    root, destination = Path(root), Path(destination)
    inputs = {"config/scenarios.json": (root / "config/scenarios.json").read_bytes()}
    for scenario in scenarios:
        for chart in scenario["charts"]:
            tree = read_tree(repository_path(root, chart["source"]))
            safe_chart_inputs(tree, chart.get("reviewed_inputs"))
            inputs.update({chart["source"] + "/" + n: b for n, b in tree.items()})
        review = scenario["reviewed_legacy"]
        inputs.update({review["path"] + "/" + n: b for n, b in read_tree(repository_path(root, review["path"])).items()})
        inputs[review["path"] + ".tar.gz"] = repository_path(root, review["path"] + ".tar.gz").read_bytes()
    parsed = json.loads(inputs["config/scenarios.json"])
    entries = {s["key"]: s for s in parsed["scenarios"] + parsed.get("installer_components", [])}
    if any(entries.get(s["key"]) != s for s in scenarios):
        raise PackageError("Registry changed before snapshot")
    for scenario in scenarios:
        for chart in scenario["charts"]:
            prefix = chart["source"] + "/"
            original = {n[len(prefix):]: b for n, b in inputs.items() if n.startswith(prefix)}
            if read_tree(repository_path(root, chart["source"])) != original:
                raise PackageError("Source tree changed during snapshot; retry")
    for name, data in inputs.items():
        if repository_path(root, name).read_bytes() != data:
            raise PackageError("Source changed during snapshot; retry")
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return inputs


def build(root, output, scenarios):
    output = safe_output(root, output)
    with exclusive_lock(output / ".publication.lock"):
        return _build_locked(root, output, scenarios)


def _build_locked(root, output, scenarios):
    # Source capture/checks leave the published release untouched on failure.
    with tempfile.TemporaryDirectory(prefix="osm-source-") as temporary:
        snapshot = Path(temporary)
        source_snapshot(root, scenarios, snapshot)
        plans = [(s, expected_artifacts(snapshot, s)) for s in scenarios]
        for scenario, expected in plans:
            check_helm(snapshot, scenario, expected)
        combined = {}
        for _, expected in plans:
            combined.update(expected)
        output = safe_output(root, output)
        if (output / "CURRENT").exists():
            previous = read_tree(published_output(output))
            if tree_digest(previous) != (output / "CURRENT").read_text().strip():
                raise PackageError("Immutable publication was modified")
            for scenario, expected in plans:
                provenance_name = scenario['key'] + '.provenance.json'
                prefixes = (scenario['knf_package'] + '/', scenario['nsd_package'] + '/')
                payload = {n: b for n, b in expected.items() if n != provenance_name}
                prior_payload = {n: b for n, b in previous.items() if n.startswith(prefixes) or n in payload}
                if prior_payload == payload:
                    if provenance_name not in previous:
                        raise PackageError('Missing required stored provenance')
                    compare_provenance(previous[provenance_name], expected[provenance_name])
                    # Keep the original audit record and publication identity.
                    combined[provenance_name] = previous[provenance_name]
            combined = previous | combined
        digest = tree_digest(combined)
        releases = output / "releases"
        releases.mkdir(exist_ok=True)
        target = releases / digest
        if target.exists():
            if read_tree(target) != combined:
                raise PackageError("Immutable publication was modified")
        else:
            # Orphaned .pending-* dirs are never readers' inputs; a retry is safe.
            with tempfile.TemporaryDirectory(prefix=".pending-", dir=releases) as staging:
                staging = Path(staging)
                for name, content in combined.items():
                    file = staging / name
                    file.parent.mkdir(parents=True, exist_ok=True)
                    with file.open("wb") as stream:
                        stream.write(content)
                        stream.flush()
                        os.fsync(stream.fileno())
                    file.chmod(0o444)
                if read_tree(staging) != combined:
                    raise PackageError("Publication staging validation failed")
                for directory in [p for p in staging.rglob("*") if p.is_dir()] + [staging]:
                    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(fd)
                    finally:
                        os.close(fd)
                os.rename(staging, target)
                fd = os.open(releases, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        if not (output / "CURRENT").exists() or (output / "CURRENT").read_text().strip() != digest:
            atomic_write(output / "CURRENT", (digest + "\n").encode())
        for scenario, _ in plans:
            validate_artifacts(snapshot, output, scenario)
        return target


def validated_snapshot(root, output, scenario):
    """Return validated owned bytes; subsequent path replacement cannot change them."""
    output = safe_output(root, output)
    if not output.is_dir() or not (output / "CURRENT").is_file():
        raise PackageError("Missing atomically prepared artifacts; run prepare")
    with exclusive_lock(output / ".publication.lock"):
        with tempfile.TemporaryDirectory(prefix="osm-validation-") as temporary:
            snapshot = Path(temporary)
            source_snapshot(root, [scenario], snapshot)
            expected = validate_artifacts(snapshot, output, scenario)
            check_helm(snapshot, scenario, expected)
            return expected


def prepare(root, output, scenarios):
    return build(root, output, scenarios)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("list", "build", "prepare", "validate", "paths"))
    parser.add_argument("scenarios", nargs="*")
    parser.add_argument("--ready", action="store_true", help="Explicitly select only reconciled scenarios")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    try:
        registry = load_registry()
        if args.command == "list":
            if args.scenarios or args.ready:
                selected = select_scenarios(registry, args.scenarios, args.ready)
            else:
                selected = registry["scenarios"]
            for scenario in selected:
                print(f"{scenario['key']}: {scenario['generation_status']}" + (f" — {scenario['blocked_reason']}" if scenario['blocked_reason'] else ""))
            if not args.scenarios and not args.ready:
                for component in registry["blocked_components"]:
                    print(f"{component['key']}: blocked — {component['reason']}")
            return 0
        scenarios = select_scenarios(registry, args.scenarios, args.ready)
        if args.command in ("build", "prepare"):
            build(REPO_ROOT, args.output, scenarios)
        else:
            for scenario in scenarios:
                expected = validated_snapshot(REPO_ROOT, args.output, scenario)
            for scenario in scenarios:
                if args.command == "paths":
                    for field in ("knf_package", "nsd_package"):
                        print((published_output(args.output) / (scenario[field] + ".tar.gz")).resolve())
                else:
                    print(f"VALID {scenario['key']}: sources, profiles, descriptors, staging, archives, Helm")
        return 0
    except (RegistryError, PackageError, OSError, ValueError, yaml.YAMLError, subprocess.SubprocessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
