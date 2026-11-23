#!/usr/bin/env python3
"""Fail-closed H2 UE compatibility audit. Offline only: never claims NAS/RRC success."""
import argparse
import json
from pathlib import Path
import yaml

CANONICAL = ("helm/cran-srsran/du/values-hcran-macro.yaml",
             "helm/cran-srsran/du/values-hcran-small.yaml")
SRSUE_SA = {"name": "srsUE_5G_SA_prototype", "scs_khz": [15],
            "duplex_modes": ["FDD"], "bandwidth_mhz": [5, 10, 15, 20],
            "handover_supported": False}

def classify(profile):
    radio = profile["radio"]
    bandwidth = radio["channel_bandwidth_MHz"]
    # The H-CRAN canonical DU template explicitly pins n78 and 30 kHz SCS.
    band, scs, duplex = 78, 30, "TDD"
    compatible = scs in SRSUE_SA["scs_khz"] and duplex in SRSUE_SA["duplex_modes"] and bandwidth in SRSUE_SA["bandwidth_mhz"]
    return {"du_id": radio["duId"], "pci": radio["pci"], "band": band,
            "duplex": duplex, "scs_khz": scs, "bandwidth_mhz": bandwidth,
            "srsue_prototype_compatible": compatible,
            "h2_rrc": "NOT_RUN", "h3_nas_pdu": "NOT_RUN",
            "h4_f1u_ngu": "NOT_RUN", "h5_mobility": "BLOCKED_UE_UNSUPPORTED",
            "h6_recovery": "NOT_RUN"}

def audit(root):
    root = Path(root)
    results = [classify(yaml.safe_load((root / path).read_text())) for path in CANONICAL]
    if {(x["du_id"], x["pci"]) for x in results} != {(100,101),(101,102)}:
        raise ValueError("H-CRAN DU identities drifted")
    if any(x["srsue_prototype_compatible"] for x in results):
        raise ValueError("Unexpected canonical UE compatibility; independent review required")
    return {"schema": "hcransrs-h2-feasibility-v1",
            "scope": "canonical_n78_30khz_vs_documented_srsue_prototype",
            "evidence": "STATIC_SOURCE_AND_UPSTREAM_DOCUMENTATION_ONLY",
            "srsue_capabilities": SRSUE_SA, "du_results": results,
            "h1": "PASSED_PREVIOUS_EXACT_COMMIT_4205b874",
            "h2": "BLOCKED_INCOMPATIBLE_UE", "end_to_end": "NOT_VALIDATED",
            "production_decision": "NO_GO", "hp_cluster_touched": False,
            "required_next_action": "Independent pinned TDD 30kHz NR SA UE/bridge feasibility, or explicitly separate FDD 15kHz research overlay with no canonical conformance claim",
            "reference": "https://docs.srsran.com/projects/project/en/latest/tutorials/source/srsUE/source/index.html"}

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--root", default=".")
    p.add_argument("--output")
    args = p.parse_args()
    result = audit(args.root)
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).write_text(payload)
    print(payload, end="")
    return 0  # audit success != runtime success

if __name__ == "__main__":
    raise SystemExit(main())
