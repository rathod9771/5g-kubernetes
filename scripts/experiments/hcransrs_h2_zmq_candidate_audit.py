#!/usr/bin/env python3
"""Read-only H2 candidate documentation audit; no UE or network trial.

The third-party OCUDU guide is NOT evidence of srsRAN Project ZMQ
interoperability. Never download/execute an unreviewed binary or store IMSI/Ki.
"""
import argparse
import json
from pathlib import Path
import yaml

DOC_REPO = "SystronLab/OCUDU-ZMQ"
DOC_COMMIT = "c5f4115f36203d48b8f56ff03d87448649036f61"
DOC_BLOB = "b3e6c61e0be821681063d2f706d9023426958217"
DOC_PATH = "oai_ue.md"
TEMPLATE = "helm/cran-srsran/du/templates/du.yaml"
PROFILES = ("helm/cran-srsran/du/values-hcran-macro.yaml",
            "helm/cran-srsran/du/values-hcran-small.yaml")

def audit(root, candidate_metadata=None):
    root = Path(root)
    template = (root/TEMPLATE).read_text()
    if "band: 78" not in template or "common_scs: 30" not in template:
        raise ValueError("Canonical srsRAN n78/30-kHz profile changed")
    cells = [yaml.safe_load((root/path).read_text())["radio"] for path in PROFILES]
    if [(c["duId"], c["pci"], c["channel_bandwidth_MHz"]) for c in cells] != [(100, 101, 20), (101, 102, 10)]:
        raise ValueError("Canonical H-CRAN DU identities or bandwidth drifted")
    if candidate_metadata is not None:
        if (candidate_metadata.get("repository") != DOC_REPO or
            candidate_metadata.get("commit") != DOC_COMMIT or
            candidate_metadata.get("blob") != DOC_BLOB or
            candidate_metadata.get("path") != DOC_PATH):
            raise ValueError("Research candidate source provenance mismatch")
    return {
        "schema":"hcran-srsran-h2-interoperability-source-audit-v1",
        "scenario":"H-CRAN-srsRAN", "canonical":{"band":"n78", "scs_khz":30,
        "du_ids":[100,101], "channel_bandwidth_MHz":[20,10]},
        "candidate":{"repository":DOC_REPO,"commit":DOC_COMMIT,
                     "path":DOC_PATH, "blob":DOC_BLOB,
                     "documents":"OAI NR UE ZMQ with OCUDU gNB",
                     "does_not_prove":"OAI NR UE compatibility with pinned srsRAN Project RF-ZMQ driver"},
        "critical_gaps":["IQ framing and sample format", "clocking and sample-rate agreement",
                         "ZMQ endpoint direction and connection establishment",
                         "separate macro/small RF paths", "independent NR UE build compatibility",
                         "UE RRC/NAS and genuine bidirectional GTP-U",
                         "UE/handover support for inter-DU mobility"],
        "decision":"CANDIDATE_ONLY_INTEROPERABILITY_NOT_VALIDATED",
        "h2_rrc":"BLOCKED", "h3_nas":"NOT_RUN", "h4_gtpu":"NOT_RUN",
        "h5_mobility":"NOT_RUN", "production_decision":"NO_GO",
        "hp_cluster_touched":False}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",default=".")
    ap.add_argument("--output",required=True)
    args=ap.parse_args()
    result=audit(args.root)
    out=Path(args.output)
    out.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    print(json.dumps({"decision":result["decision"],"production_decision":"NO_GO"}))
if __name__=="__main__":
    main()
