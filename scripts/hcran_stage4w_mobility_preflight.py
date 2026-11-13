#!/usr/bin/env python3
"""SHA-pinned, read-only OAI 2026.w13 F1 mobility source feasibility audit.

Important: proof of source support is NOT an actual F1 handover, serving-cell
change, UE continuity, or production qualification. No network access, no
Kubernetes connection, no docker, and no control action is performed here.
"""
import argparse
import hashlib
import json
import pathlib
import sys

PINNED = {
    "rrc_gNB_mobility.c": "3c59cdfc1660d3381506768e728897b35bfabe1d",
    "handover-tutorial.md": "b7ccbc318ce9273e14f4e6643f1bedd4e878f300",
}

SOURCE_MARKERS = (
    "void nr_rrc_trigger_f1_ho(",
    "void nr_HO_F1_trigger_telnet(",
    "rrc_f1_ue_context_setup_for_target_du(",
    "nr_rrc_f1_ho_complete",
    "find_target_du(",
)
TUTORIAL_MARKERS = (
    "ci trigger_f1_ho",
    "--telnetsrv",
    "both DUs",
    "ssb_PositionsInBurst_Bitmap",
)


def git_blob_sha(data: bytes):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def review(source: bytes, tutorial: bytes, build_workflow: str):
    result = {
        "scope": "pinned source feasibility, not 3GPP conformance",
        "oai_tag": "2026.w13",
        "source_git_blob_expected": PINNED["rrc_gNB_mobility.c"],
        "tutorial_git_blob_expected": PINNED["handover-tutorial.md"],
        "source_git_blob_actual": git_blob_sha(source),
        "tutorial_git_blob_actual": git_blob_sha(tutorial),
        "source_support": "NOT_VERIFIED",
        "real_f1_handover_observed": False,
        "ue_pdu_session_continuity_observed": False,
        "runtime_status": "BLOCKED",
        "release_decision": "NO_GO",
        "deploy_authorized": False,
        "reasons": [],
    }
    if result["source_git_blob_actual"] != PINNED["rrc_gNB_mobility.c"]:
        result["reasons"].append("OAI mobility source differs from pinned reviewed Git blob")
    if result["tutorial_git_blob_actual"] != PINNED["handover-tutorial.md"]:
        result["reasons"].append("OAI mobility tutorial differs from pinned reviewed Git blob")
    if result["reasons"]:
        return result
    src = source.decode("utf-8")
    doc = tutorial.decode("utf-8")
    if missing := [m for m in SOURCE_MARKERS if m not in src]:
        result["reasons"].append("Source does not contain required F1 handover functions: " + ", ".join(missing))
        return result
    if missing := [m for m in TUTORIAL_MARKERS if m not in doc]:
        result["reasons"].append("Pinned handover tutorial lacks required setup instructions: " + ", ".join(missing))
        return result
    result["source_support"] = "PINNED_SOURCE_HAS_F1_HANDOVER_PATH"
    # Stage 3 portable runtime image was compiled with --gNB and without
    # --build-lib telnetsrv. Runtime must not silently assume it has the
    # manual-trigger shared module described in the OAI tutorial.
    if "--build-lib telnetsrv" not in build_workflow:
        result["reasons"].append(
            "The Stage 3 patched CU build does not explicitly include --build-lib telnetsrv; "
            "manual F1 handover trigger availability is unverified."
        )
    result["reasons"].extend([
        "No UE-side RFsim server and two-DU mobility trial has been executed against this patched image.",
        "No correlated F1AP/RRC handover and preserved PDU-session data-plane evidence exists.",
        "No sustained monitoring, security compatibility or live OSM staging rollback evidence exists.",
    ])
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mobility-source", type=pathlib.Path, required=True)
    ap.add_argument("--handover-tutorial", type=pathlib.Path, required=True)
    ap.add_argument("--stage3-workflow", type=pathlib.Path, required=True)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    opts = ap.parse_args(argv)
    result = review(opts.mobility_source.read_bytes(),
                    opts.handover_tutorial.read_bytes(),
                    opts.stage3_workflow.read_text(encoding="utf-8"))
    opts.out.parent.mkdir(parents=True, exist_ok=True)
    opts.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["source_support"] == "PINNED_SOURCE_HAS_F1_HANDOVER_PATH" else 1


if __name__ == "__main__":
    sys.exit(main())
