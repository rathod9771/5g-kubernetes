"""Fail-closed H2 candidate: OCUDU docs are not srsRAN 25.04 interoperability."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
import yaml

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/"scripts/experiments/hcransrs_h2_zmq_candidate_audit.py"
spec=importlib.util.spec_from_file_location("hcransrs_h2_zmq_candidate_audit",P)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class CandidateAudit(unittest.TestCase):
    def test_candidate_only_never_protocol_pass(self):
        r=m.audit(ROOT)
        self.assertEqual(r["decision"],"CANDIDATE_ONLY_INTEROPERABILITY_NOT_VALIDATED")
        self.assertEqual(r["h2_rrc"],"BLOCKED")
        self.assertEqual(r["h3_nas"],"NOT_RUN")
        self.assertEqual(r["production_decision"],"NO_GO")
        self.assertEqual(r["canonical"]["du_ids"],[100,101])
    def test_bad_upstream_provenance_rejected(self):
        valid={"repository":m.DOC_REPO,"commit":m.DOC_COMMIT,
               "blob":m.DOC_BLOB,"path":m.DOC_PATH}
        self.assertEqual(m.audit(ROOT,valid)["h2_rrc"],"BLOCKED")
        for key in valid:
            with self.subTest(key=key),self.assertRaises(ValueError):
                m.audit(ROOT,{**valid,key:"invalid"})
    def test_drifted_radio_profile_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d)
            for file in (m.TEMPLATE,*m.PROFILES):
                p=base/file;p.parent.mkdir(parents=True,exist_ok=True)
                p.write_text((ROOT/file).read_text())
            p=base/m.TEMPLATE;p.write_text(p.read_text().replace("band: 78","band: 3"))
            with self.assertRaises(ValueError):m.audit(base)
            p.write_text((ROOT/m.TEMPLATE).read_text())
            q=base/m.PROFILES[1]
            data=yaml.safe_load(q.read_text());data["radio"]["duId"]=100
            q.write_text(yaml.safe_dump(data))
            with self.assertRaises(ValueError):m.audit(base)
if __name__=="__main__":unittest.main()
