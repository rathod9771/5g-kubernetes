"""Local/static regressions; all mutations occur in disposable test directories."""

import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import osm_packages as packages
from scenario_registry import RegistryError, dashboard_scenarios, load_registry, select_scenarios


class PackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_registry(ROOT)
        cls.scenario = select_scenarios(cls.registry, ["cran-srsran"])[0]

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="p0a-package-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "repo"
        self.output = Path(self.temporary.name) / "output"
        # Copy only tracked input families, never diagnostics/backups or live state.
        shutil.copytree(ROOT / "helm", self.root / "helm")
        (self.root / "config").mkdir()
        shutil.copy2(ROOT / "config/scenarios.json", self.root / "config/scenarios.json")
        shutil.copy2(ROOT / 'config/reference-versions.json', self.root / 'config/reference-versions.json')
        legacy = self.scenario["reviewed_legacy"]["path"]
        shutil.copytree(ROOT / legacy, self.root / legacy)
        shutil.copy2(ROOT / (legacy + ".tar.gz"), self.root / (legacy + ".tar.gz"))

    def materialize(self):
        artifacts = packages.expected_artifacts(self.root, self.scenario)
        for name, data in artifacts.items():
            target = self.output / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        return artifacts

    def test_registry_keys_aliases_and_unresolved_oai_authority(self):
        entries = dashboard_scenarios(self.registry)
        self.assertEqual(len(entries), 12)
        self.assertEqual(select_scenarios(self.registry, ["srsran"])[0]["key"], "cran-srsran")
        oran = next(s for s in self.registry["scenarios"] if s["key"] == "oran-oai")
        self.assertEqual(oran["charts"], [])
        self.assertIn("helm/oran-oai/cu", oran["candidate_sources"])
        self.assertEqual(entries["cran-srsran"]["nsd_package"], "cran_srsran_ns")

    def test_every_blocked_selection_fails_before_output(self):
        keys = [s["key"] for s in self.registry["scenarios"] if s["generation_status"] == "blocked"]
        keys += [s["key"] for s in self.registry["blocked_components"]]
        for key in keys:
            with self.subTest(key=key), self.assertRaisesRegex(RegistryError, "BLOCKED"):
                select_scenarios(self.registry, ["cran-srsran", key])
        self.assertFalse((self.output / "CURRENT").exists())

    def test_missing_chart_or_profile_is_rejected(self):
        chart = self.root / "helm/cran-srsran/cu/Chart.yaml"
        chart.unlink()
        with self.assertRaisesRegex(RegistryError, "Missing chart"):
            load_registry(self.root)
        shutil.copy2(ROOT / "helm/cran-srsran/cu/Chart.yaml", chart)
        (self.root / "helm/cran-srsran/cu/values-cloudran.yaml").unlink()
        with self.assertRaisesRegex(RegistryError, "Missing or external profile"):
            load_registry(self.root)

    def test_duplicate_package_id_is_rejected(self):
        registry = copy.deepcopy(self.registry)
        registry["scenarios"][1]["knf_package"] = registry["scenarios"][0]["knf_package"]
        (self.root / "config/scenarios.json").write_text(json.dumps(registry))
        with self.assertRaisesRegex(RegistryError, "duplicate knf_package"):
            load_registry(self.root)

    def test_source_drift_is_detected(self):
        self.materialize()
        source = self.root / "helm/cran-srsran/cu/templates/cu.yaml"
        source.write_text(source.read_text() + "\n# new canonical revision\n")
        with self.assertRaisesRegex(packages.PackageError, "drift"):
            packages.validate_artifacts(self.root, self.output, self.scenario)

    def test_image_lock_drift_remains_fatal_even_when_archive_bytes_match(self):
        self.materialize()
        path=self.root/'config/reference-versions.json'
        data=json.loads(path.read_text())
        data['ran_images']['components']['srsran']['source']['version']='unreviewed'
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(packages.PackageError,'Payload provenance drift: source_inputs'):
            packages.validate_artifacts(self.root,self.output,self.scenario)

    def test_explicit_prepare_can_publish_new_source_receipt_for_unchanged_payload(self):
        target=packages.prepare(self.root,self.output,[self.scenario])
        original=(target/'cran_srsran_knf.tar.gz').read_bytes()
        path=self.root/'config/reference-versions.json';data=json.loads(path.read_text())
        data['ran_images']['components']['srsran']['source']['strategy']='reviewed receipt change'
        path.write_text(json.dumps(data))
        with self.assertRaises(packages.PackageError):packages.validated_snapshot(self.root,self.output,self.scenario)
        replacement=packages.prepare(self.root,self.output,[self.scenario])
        self.assertNotEqual(target,replacement)
        self.assertEqual(original,(replacement/'cran_srsran_knf.tar.gz').read_bytes())
        packages.validated_snapshot(self.root,self.output,self.scenario)

    def test_prepare_cannot_repair_falsified_stored_archive_hashes(self):
        ProvenanceIdentityTests.published_fixture(self, lambda p:p['archives_sha256'].update({'cran_srsran_knf.tar.gz':'0'*64}))
        pointer=(self.output/'CURRENT').read_bytes()
        with self.assertRaisesRegex(packages.PackageError,'archives_sha256'):
            packages.prepare(self.root,self.output,[self.scenario])
        self.assertEqual((self.output/'CURRENT').read_bytes(),pointer)

    def test_stale_extra_embedded_chart_is_detected(self):
        self.materialize()
        stale = self.output / "cran_srsran_knf/helm-chart-v3s/obsolete/templates/gnb.yaml"
        stale.parent.mkdir(parents=True)
        stale.write_text("obsolete")
        with self.assertRaisesRegex(packages.PackageError, "extra=.*obsolete"):
            packages.validate_artifacts(self.root, self.output, self.scenario)

    def test_missing_generated_chart_is_detected(self):
        self.materialize()
        (self.output / "cran_srsran_knf/helm-chart-v3s/cu/Chart.yaml").unlink()
        with self.assertRaisesRegex(packages.PackageError, "reference mismatch"):
            packages.validate_artifacts(self.root, self.output, self.scenario)

    def test_archive_and_provenance_tampering_are_detected(self):
        for filename in ["cran_srsran_knf.tar.gz", "cran-srsran.provenance.json"]:
            with self.subTest(filename=filename):
                self.materialize()
                target = self.output / filename
                target.write_bytes(target.read_bytes() + b"tampered")
                with self.assertRaisesRegex(packages.PackageError, "drift|Invalid provenance JSON"):
                    packages.validate_artifacts(self.root, self.output, self.scenario)

    def test_nsd_knf_and_kdu_mismatches_are_detected(self):
        files = packages.expected_artifacts(self.root, self.scenario)
        nsd_name = "cran_srsran_ns/cran_srsran_ns_nsd.yaml"
        nsd = json.loads(files[nsd_name])
        nsd["nsd"]["nsd"][0]["vnfd-id"] = ["wrong_knf"]
        files[nsd_name] = packages.json_bytes(nsd)
        with self.assertRaisesRegex(packages.PackageError, "reference mismatch"):
            packages.validate_references(self.scenario, files)
        files = packages.expected_artifacts(self.root, self.scenario)
        vnfd_name = "cran_srsran_knf/cran_srsran_knf_vnfd.yaml"
        vnfd = json.loads(files[vnfd_name])
        vnfd["vnfd"]["kdu"][0]["helm-chart"] = "missing"
        files[vnfd_name] = packages.json_bytes(vnfd)
        with self.assertRaisesRegex(packages.PackageError, "reference mismatch"):
            packages.validate_references(self.scenario, files)

    def test_unknown_package_only_fix_blocks_generation(self):
        legacy = self.root / self.scenario["reviewed_legacy"]["path"] / "helm-chart-v3s/cu/templates/cu.yaml"
        legacy.write_text(legacy.read_text() + "\n# additional package-only fix\n")
        with self.assertRaisesRegex(packages.PackageError, "reconcile package-only"):
            packages.expected_artifacts(self.root, self.scenario)
        self.assertFalse((self.output / "CURRENT").exists())

    def test_reproducibility_ignores_order_permissions_and_timestamps(self):
        first = packages.expected_artifacts(self.root, self.scenario)
        for source in (self.root / "helm/cran-srsran").rglob("*"):
            if source.is_file():
                os.utime(source, (123456789, 123456789))
                source.chmod(0o600)
        second = packages.expected_artifacts(self.root, self.scenario)
        self.assertEqual(first, second)
        package = "cran_srsran_knf.tar.gz"
        with tarfile.open(fileobj=io.BytesIO(first[package]), mode="r:gz") as archive:
            members = archive.getmembers()
            self.assertEqual([m.name for m in members], sorted(m.name for m in members))
            self.assertTrue(all(m.mtime == m.uid == m.gid == 0 for m in members))
        content = {"a/a.yaml": b"a", "a/b.yaml": b"b"}
        self.assertEqual(packages.archive_bytes("a", content), packages.archive_bytes("a", dict(reversed(list(content.items())))))
        self.assertEqual(first[package][4:8], b"\0\0\0\0")

    def test_legacy_output_and_symlink_are_rejected(self):
        with self.assertRaisesRegex(packages.PackageError, "legacy inputs are read-only"):
            packages.safe_output(self.root, self.root / "osm-packages")
        self.output.mkdir()
        (self.output / "escape").symlink_to(self.root / "config")
        with self.assertRaisesRegex(packages.PackageError, "Symlinks"):
            packages.safe_output(self.root, self.output)

    def test_invalid_helm_rendering_blocks_before_writes(self):
        source = self.root / "helm/cran-srsran/cu/templates/cu.yaml"
        source.write_text("{{ invalid_function }}")
        with self.assertRaisesRegex(packages.PackageError, "Helm"):
            packages.build(self.root, self.output, [self.scenario])
        self.assertFalse((self.output / "CURRENT").exists())

    def test_regression_of_preserved_amf_fix_blocks_before_writes(self):
        values = self.root / "helm/cran-srsran/cu/values.yaml"
        values.write_text(values.read_text().replace("amf-ngap-stable", "open5gs-amf-ngap"))
        with self.assertRaisesRegex(packages.PackageError, "lost stable-AMF"):
            packages.build(self.root, self.output, [self.scenario])
        self.assertFalse((self.output / "CURRENT").exists())

    def test_entire_batch_is_checked_before_first_output_write(self):
        fran = select_scenarios(self.registry, ["fran"])[0]
        legacy = fran["reviewed_legacy"]["path"]
        shutil.copytree(ROOT / legacy, self.root / legacy)
        shutil.copy2(ROOT / (legacy + ".tar.gz"), self.root / (legacy + ".tar.gz"))
        (self.root / "helm/fran-edge/templates/edge-app.yaml").write_text("{{ invalid_function }}")
        with self.assertRaisesRegex(packages.PackageError, "Helm"):
            packages.build(self.root, self.output, [self.scenario, fran])
        self.assertFalse((self.output / "CURRENT").exists())

    def test_unowned_existing_output_is_not_overwritten(self):
        file = self.output / "cran_srsran_knf/unowned.txt"
        file.parent.mkdir(parents=True)
        file.write_text("preserve")
        packages.build(self.root, self.output, [self.scenario])
        self.assertEqual(file.read_text(), "preserve")
        packages.validate_artifacts(self.root, self.output, self.scenario)

    def test_build_is_idempotent_and_refuses_to_overwrite_drift(self):
        packages.build(self.root, self.output, [self.scenario])
        hashes = packages.read_tree(self.output)
        before = {str(p): p.stat().st_mtime_ns for p in self.output.rglob("*") if p.is_file()}
        packages.build(self.root, self.output, [self.scenario])
        self.assertEqual(hashes, packages.read_tree(self.output))
        self.assertEqual(before, {str(p): p.stat().st_mtime_ns for p in self.output.rglob("*") if p.is_file()})
        target = packages.published_output(self.output) / "cran_srsran_knf.tar.gz"
        target.chmod(0o600)
        target.write_bytes(b"changed")
        with self.assertRaisesRegex(packages.PackageError, "modified"):
            packages.build(self.root, self.output, [self.scenario])
        self.assertEqual(target.read_bytes(), b"changed")


class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT / "ran-selector"))
        spec = importlib.util.spec_from_file_location("p0a_backend_test", ROOT / "ran-selector/backend.py")
        cls.backend = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.backend)

    def test_registry_api_preserves_keys_and_exposes_blocks(self):
        client = self.backend.app.test_client()
        result = client.get("/api/scenarios").get_json()["scenarios"]
        self.assertEqual(len(result), 11)
        by_key = {entry["key"]: entry for entry in result}
        self.assertEqual(by_key["oran-oai"]["generation_status"], "blocked")
        self.assertEqual(by_key["cran-oai"]["releases"], ["cran-oai-cu", "cran-oai-du"])

    def test_blocked_or_missing_packages_never_reach_lifecycle(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        patcher = mock.patch.object(self.backend, "CONFIG_FILE", str(Path(temporary.name) / "active.yaml"))
        patcher.start()
        self.addCleanup(patcher.stop)
        client = self.backend.app.test_client()
        with mock.patch.object(self.backend.osm_client, "terminate_ns", side_effect=AssertionError("lifecycle invoked")), mock.patch.object(self.backend.osm_client, "instantiate_ns", side_effect=AssertionError("lifecycle invoked")), mock.patch.object(self.backend, "run", side_effect=AssertionError("command invoked")):
            response = client.post("/api/deploy", json={"ran": "oran-oai"})
            self.assertEqual(response.status_code, 409)
            with mock.patch.object(self.backend, "DEFAULT_OUTPUT", ROOT / "build/osm-packages/missing-packages"):
                response = client.post("/api/deploy", json={"ran": "srsran"})
                self.assertEqual(response.status_code, 409)


if __name__ == "__main__":
    unittest.main()


class ProvenanceIdentityTests(unittest.TestCase):
    setUpClass = classmethod(PackageTests.setUpClass.__func__)
    setUp = PackageTests.setUp
    materialize = PackageTests.materialize

    def published_fixture(self, mutate=None):
        with mock.patch.object(packages.yaml, '__version__', '6.0.1'):
            self.materialize()
        name=self.scenario['key']+'.provenance.json'
        if mutate:
            provenance=json.loads((self.output/name).read_bytes())
            mutate(provenance)
            (self.output/name).write_bytes(packages.json_bytes(provenance))
        tree=packages.read_tree(self.output)
        digest=packages.tree_digest(tree)
        target=self.output/'releases'/digest
        for path,data in tree.items():
            file=target/path;file.parent.mkdir(parents=True,exist_ok=True);file.write_bytes(data)
        (self.output/'CURRENT').write_text(digest+'\n')
        return target,tree

    def validate_unchanged(self,target,tree):
        pointer=(self.output/'CURRENT').read_bytes()
        pointer_mtime=(self.output/'CURRENT').stat().st_mtime_ns
        before={name:(target/name).stat().st_mtime_ns for name in tree}
        with mock.patch.object(packages.yaml,'__version__','6.0.3'):
            result=packages.validated_snapshot(self.root,self.output,self.scenario)
        self.assertEqual(result,tree)
        self.assertEqual((self.output/'CURRENT').read_bytes(),pointer)
        self.assertEqual((self.output/'CURRENT').stat().st_mtime_ns,pointer_mtime)
        for name,data in tree.items():
            self.assertEqual((target/name).read_bytes(),data)
            self.assertEqual((target/name).stat().st_mtime_ns,before[name])
        return result

    def test_precision_pyyaml_only_drift_returns_stored_bytes_unchanged(self):
        target,tree=self.published_fixture()
        with mock.patch('sys.stderr',new_callable=io.StringIO) as warnings:
            self.validate_unchanged(target,tree)
        self.assertIn('/toolchain/pyyaml',warnings.getvalue())
        self.assertIn('payload integrity verified',warnings.getvalue())

    def test_generator_implementation_only_drift_is_audit_metadata(self):
        target,tree=self.published_fixture(lambda p:p['generator']['implementation_sha256'].update({'osm_packages.py':'0'*64}))
        self.validate_unchanged(target,tree)

    def test_each_content_identity_field_remains_fatal(self):
        original=json.loads(packages.expected_artifacts(self.root,self.scenario)['cran-srsran.provenance.json'])
        for field in ['scenario','scenario_sha256','profile_sha256','descriptor_sha256','source_inputs','staging_sha256','archives_sha256','kubernetes_render_version']:
            with self.subTest(field=field):
                changed=copy.deepcopy(original)
                if isinstance(changed[field],dict):
                    key=next(iter(changed[field]), 'unexpected-profile');changed[field][key]='0'*64
                elif field.endswith('sha256'):changed[field]='0'*64
                else:changed[field]='different'
                with self.assertRaisesRegex(packages.PackageError,'Payload provenance drift'):
                    packages.compare_provenance(packages.json_bytes(changed),packages.json_bytes(original))

    def test_missing_and_unknown_schema_fields_rejected(self):
        original=json.loads(packages.expected_artifacts(self.root,self.scenario)['cran-srsran.provenance.json'])
        for mutate in [lambda p:p.pop('source_inputs'),lambda p:p['toolchain'].pop('pyyaml'),lambda p:p.update({'unknown_field':True}),lambda p:p.update({'format_version':999}),lambda p:p['generator'].update({'version':'999'})]:
            changed=copy.deepcopy(original);mutate(changed)
            with self.assertRaises(packages.PackageError):packages.compare_provenance(packages.json_bytes(changed),packages.json_bytes(original))

    def test_registry_difference_requires_identical_resolved_scenario(self):
        original=json.loads(packages.expected_artifacts(self.root,self.scenario)['cran-srsran.provenance.json'])
        changed=copy.deepcopy(original);changed['registry_sha256']='0'*64
        self.assertEqual(packages.compare_provenance(packages.json_bytes(changed),packages.json_bytes(original)),['/registry_sha256'])
        changed['scenario_sha256']='1'*64
        with self.assertRaisesRegex(packages.PackageError,'scenario_sha256'):
            packages.compare_provenance(packages.json_bytes(changed),packages.json_bytes(original))

    def test_publication_digest_tampering_fails_before_audit_comparison(self):
        target,tree=self.published_fixture()
        name='cran-srsran.provenance.json'
        (target/name).write_bytes((target/name).read_bytes()+b' ')
        with self.assertRaisesRegex(packages.PackageError,'Immutable publication was modified'):
            packages.validated_snapshot(self.root,self.output,self.scenario)

    def test_missing_required_field_in_publication_fails(self):
        self.published_fixture(lambda p:p.pop('archives_sha256'))
        with self.assertRaisesRegex(packages.PackageError,'schema fields'):
            packages.validated_snapshot(self.root,self.output,self.scenario)

    def test_unknown_format_in_publication_fails(self):
        self.published_fixture(lambda p:p.update({'format_version':999}))
        with self.assertRaisesRegex(packages.PackageError,'format/version'):
            packages.validated_snapshot(self.root,self.output,self.scenario)

    def test_payload_tampering_with_updated_publication_digest_still_fails(self):
        target,tree=self.published_fixture()
        archive=self.scenario['knf_package']+'.tar.gz'
        tree[archive]=tree[archive]+b'tampered'
        digest=packages.tree_digest(tree)
        altered=self.output/'releases'/digest
        for name,data in tree.items():
            file=altered/name;file.parent.mkdir(parents=True,exist_ok=True);file.write_bytes(data)
        (self.output/'CURRENT').write_text(digest+'\n')
        with self.assertRaisesRegex(packages.PackageError,'source/staging/archive drift'):
            packages.validated_snapshot(self.root,self.output,self.scenario)

    def test_prepare_metadata_only_change_reuses_publication(self):
        target,tree=self.published_fixture()
        pointer=(self.output/'CURRENT').read_bytes();mtime=(self.output/'CURRENT').stat().st_mtime_ns
        with mock.patch.object(packages.yaml,'__version__','6.0.3'):
            self.assertEqual(packages.prepare(self.root,self.output,[self.scenario]),target)
        self.assertEqual((self.output/'CURRENT').read_bytes(),pointer)
        self.assertEqual((self.output/'CURRENT').stat().st_mtime_ns,mtime)
        self.assertEqual(packages.read_tree(target),tree)
