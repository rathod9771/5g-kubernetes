"""Stage 5ZJ adversarial offline tests. No target-cluster interaction."""
import datetime as dt
import importlib.util
import json
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/hcran_stage5zj_merge_pressure.py"
SPEC = importlib.util.spec_from_file_location("stage5zj_pressure", SCRIPT)
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)
NOW = dt.datetime(2026, 11, 19, 6, 0, tzinfo=dt.timezone.utc)


def specimen(name="staging-node", **changes):
    node = dict(node=name, collected_utc="2026-11-19T05:59:45+00:00",
                cpu_some_avg10=2.5, io_some_avg10=0.5, load_per_cpu=0.2)
    node.update(changes)
    return dict(nodes=[node])


class Stage5ZJPressure(unittest.TestCase):
    def write(self, directory, name, data):
        p = pathlib.Path(directory) / name
        p.write_text(json.dumps(data))
        return p

    def test_combines_two_distinct_fresh_nodes(self):
        with tempfile.TemporaryDirectory() as td:
            a = self.write(td, "a.json", specimen("staging-node-a"))
            b = self.write(td, "b.json", specimen("staging-node-b"))
            result = mod.combine([b, a], ["staging-node-b", "staging-node-a"], NOW)
            self.assertEqual([n["node"] for n in result["nodes"]], ["staging-node-a", "staging-node-b"])
            self.assertEqual(len(result), 1)
            self.assertEqual(set(result["nodes"][0]), mod.REQUIRED)

    def test_fails_closed_on_missing_or_extra_node(self):
        with tempfile.TemporaryDirectory() as td:
            a = self.write(td, "a.json", specimen())
            for expected in (["other-node"], ["staging-node", "other-node"], []):
                with self.subTest(expected=expected):
                    with self.assertRaises(mod.PressureError):
                        mod.combine([a], expected, NOW)
            with self.assertRaises(mod.PressureError):
                mod.combine([a, a], ["staging-node"], NOW)
            with self.assertRaises(mod.PressureError):
                mod.combine([a], ["staging-node", "staging-node"], NOW)

    def test_fails_closed_on_old_future_naive_timestamps(self):
        for ts in ("2026-11-19T05:54:00Z", "2026-11-19T06:00:31Z",
                   "2026-11-19T05:59:45", "garbled"):
            with self.subTest(ts=ts), tempfile.TemporaryDirectory() as td:
                a = self.write(td, "a.json", specimen(collected_utc=ts))
                with self.assertRaises(mod.PressureError):
                    mod.combine([a], ["staging-node"], NOW)

    def test_fails_closed_on_bogus_metrics(self):
        for field, val in [("cpu_some_avg10", True), ("io_some_avg10", float("nan")),
                           ("load_per_cpu", -1), ("cpu_some_avg10", 101),
                           ("load_per_cpu", "25"), ("io_some_avg10", float("inf"))]:
            with self.subTest(field=field, val=val), tempfile.TemporaryDirectory() as td:
                a = self.write(td, "a.json", specimen(**{field: val}))
                with self.assertRaises(mod.PressureError):
                    mod.combine([a], ["staging-node"], NOW)

    def test_rejects_unapproved_extra_keys_and_multi_node_input(self):
        with tempfile.TemporaryDirectory() as td:
            a = self.write(td, "a.json", specimen(secret="not-allowed"))
            with self.assertRaises(mod.PressureError):
                mod.combine([a], ["staging-node"], NOW)
            b = self.write(td, "b.json", dict(nodes=[specimen()["nodes"][0], specimen("other-node")["nodes"][0]]))
            with self.assertRaises(mod.PressureError):
                mod.combine([b], ["staging-node"], NOW)
            c = self.write(td, "c.json", dict(nodes=[specimen()["nodes"][0]], secret="do-not-copy"))
            with self.assertRaises(mod.PressureError):
                mod.combine([c], ["staging-node"], NOW)

    def test_rejects_invalid_names_and_large_file(self):
        for name in ("../bad", "BadName", "node_1", "", "x" * 254):
            with self.subTest(name=name):
                with self.assertRaises(mod.PressureError):
                    mod.validate_node(name)
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "huge.json"
            p.write_text(" " * (mod.MAX_INPUT_BYTES + 1))
            with self.assertRaises(mod.PressureError):
                mod.combine([p], ["staging-node"], NOW)

    def test_script_rejects_missing_node_and_never_writes_output(self):
        with tempfile.TemporaryDirectory() as td:
            a = self.write(td, "a.json", specimen(collected_utc=dt.datetime.now(dt.timezone.utc).isoformat()))
            out = pathlib.Path(td) / "merged.json"
            result = mod.main(["--input", str(a), "--expect-node", "not-the-node", "--output", str(out)])
            self.assertEqual(result, 2)
            self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
