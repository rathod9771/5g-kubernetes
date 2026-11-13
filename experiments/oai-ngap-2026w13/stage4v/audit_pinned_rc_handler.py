#!/usr/bin/env python3
"""Read-only SHA-pinned audit of OAI 2026.w13 E2SM-RC agent control handler.

This does not transmit E2 requests or make claims about runtime effects.
A known upstream Git blob is identified and a previously reviewed handler
classification is reported. Any changed source fails closed for re-review.
"""
import argparse
import hashlib
import json
import pathlib
import sys

OAI_TAG = "2026.w13"
OAI_PATH = "openair2/E2AP/RAN_FUNCTION/O-RAN/ran_func_rc.c"
OAI_GIT_BLOB_SHA1 = "14c357496c4d838fe903640536338c2f62639a18"


def git_blob_sha(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\x00" + data).hexdigest()


def audit(data):
    actual = git_blob_sha(data)
    result = {
        "source": "OPENAIRINTERFACE/openairinterface5g",
        "tag": OAI_TAG,
        "path": OAI_PATH,
        "expected_git_blob": OAI_GIT_BLOB_SHA1,
        "actual_git_blob": actual,
        "effective_rc_control": "NOT_VERIFIED",
        "production_decision": "NO_GO",
        "live_hp_cluster_contacted": False,
    }
    if actual != OAI_GIT_BLOB_SHA1:
        result.update(status="SOURCE_DRIFT_REVIEW_REQUIRED",
                      reason="File differs from manually reviewed OAI 2026.w13 blob")
        return result
    source = data.decode("utf-8")
    start = source.find("sm_ag_if_ans_t write_ctrl_rc_sm(void const* data)")
    end = source.find("bool read_rc_sm(void* data)", start)
    if start < 0 or end < start:
        result.update(status="REVIEW_REQUIRED", reason="RC handler boundaries not found")
        return result
    handler = source[start:end]
    indicators = [
        'ctrl->hdr.frmt_1.ctrl_act_id == 2',
        'printf("QoS flow mapping configuration',
        'printf("DRB ID',
        'printf("qfi =',
        "ans.ctrl_out.type = RAN_CTRL_V1_3_AGENT_IF_CTRL_ANS_V0;",
        "return ans;",
    ]
    if not all(x in handler for x in indicators):
        result.update(status="REVIEW_REQUIRED",
                      reason="Expected reviewed RC handler structure missing")
        return result
    result.update(
        status="PINNED_HANDLER_REVIEW_CONFIRMED",
        advertised_control_style=1,
        advertised_control_action=2,
        advertised_control_action_name="QoS flow mapping configuration",
        reviewed_finding=(
            "write_ctrl_rc_sm parses and prints DRB/QFI/mapping direction then "
            "returns a control outcome; the reviewed body does not invoke "
            "a RAN bearer or QoS mapping state-mutation operation."
        ),
        next_action=(
            "Implement bounded RAN state mutation and readback on disposable "
            "CU/UE/DU with an independently observable, reversible effect. "
            "Do not count a RICcontrolAcknowledge as an actual RAN change."
        ),
    )
    return result


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("pinned_source", type=pathlib.Path)
    parser.add_argument("--report", type=pathlib.Path, required=True)
    args = parser.parse_args(argv)
    outcome = audit(args.pinned_source.read_bytes())
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(outcome, indent=2, sort_keys=True) + "\n")
    print(json.dumps(outcome, indent=2))
    return 0 if outcome["status"] == "PINNED_HANDLER_REVIEW_CONFIRMED" else 1


if __name__ == "__main__":
    sys.exit(main())
