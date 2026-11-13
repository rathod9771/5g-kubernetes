#!/usr/bin/env python3
"""3GPP Rel-15/16 H-CRAN-only evidence inventory and fail-closed release gate.

The script NEVER deploys, connects to Kubernetes, accesses subscribers, or
authorizes promotion. A green *inventory* CI job is not a green production gate.
"""
import datetime
import json
import os
import pathlib
import urllib.request

API = "https://api.github.com/repos/rathod9771/5g-kubernetes"
KNOWN = [
    ("NGAP_V2_RECOVERY", 43157479388, "oai-ngap-stage3-real-open5gs-"),
    ("HCRAN_STATIC", 43160947977, "oai-ngap-stage4a-hcran-no-go-"),
    ("DUAL_DU_F1C", 43162695243, "oai-ngap-stage4d"),
    ("THREE_AMF_FAULT_CYCLES", 43163998295, "oai-ngap-stage4e-dual-du-f1-"),
    ("HCRAN_DUAL_UE_STATIC_PREREQUISITES", 43186660857, "oai-hcran-ue-prerequisites-"),
    ("HCRAN_REAL_UE_NAS_PDU_ONE_DU", 43190810958, "oai-hcran-real-ue-pdu-"),
    ("HCRAN_REAL_F1U_NGU_GTPU_ONE_DU", 43192172149, "oai-hcran-real-gtpu-"),
    ("HCRAN_DUAL_F1_MACRO_UE_USER_PLANE", 43193089291, "oai-hcran-dual-f1-live-traffic-"),
    ("HCRAN_DUAL_F1_SMALL_UE_USER_PLANE", 43193891521, "oai-hcran-small-ue-userplane-"),
    ("HCRAN_TWO_UE_CONCURRENT_USER_PLANE", 43194751671, "oai-hcran-dual-ue-traffic-"),
    ("HCRAN_BOUNDED_TWO_UE_SOAK", 43224919656, "oai-hcran-dual-ue-soak-"),
    ("HCRAN_NEA2_NIA2_CONFIGURED_TWO_UE_TRAFFIC", 43378042762, "hcran-stage4x-NEA2-NIA2-dual-UE-attempt-"),
    ("HCRAN_SUPERVISED_SYNTHETIC_UE_RECOVERY_AFTER_AMF", 43367213888, "oai-hcran-supervised-ue-recovery-"),
    ("HCRAN_ASSISTED_TWO_UE_RECOVERY_AFTER_FULL_5GC_RESTART", 43209876014, "oai-hcran-assisted-dual-ue-recovery-"),
    ("HCRAN_CONTROLLED_TWO_UE_RECOVERY_AFTER_AMF_ONLY_FAULT", 43220775040, "oai-hcran-controlled-ue-reattach-"),
    ("OSM_OFFLINE_SIMULATION", 43165822749, "oai-ngap-stage4h-offline-osm-"),
]
# Verification of these gates requires real positive evidence, not inferred
# success from compile, static renders, F1-C setup, or a synthetic test.
NOT_VERIFIED = {
    "INTRA_GNB_INTER_DU_MOBILITY": "No macro-to-small or small-to-macro inter-DU mobility / UE context transfer proven with active PDU service; TS 38.401/38.473/38.331",
    "F1AP_UE_CONTEXT_MODIFICATION_RELEASE": "Selected F1 Setup and user-plane operation passed, but complete UE Context Modification/Release procedures are not qualified",
    "RRC_REESTABLISHMENT_AFTER_RADIO_FAILURE": "No verified RRC re-establishment or compliant session continuity following simulated radio-link failure",
    "NAS_SECURITY_AND_REGISTRATION_EDGE_CASES": "Basic NAS Registration Accept passed; registration reject, authentication/retry, security and deregistration matrix not executed",
    "PDU_SESSION_MODIFICATION_RELEASE_AND_QOS": "PDU setup passed; PDU Session Modification, Release, QFI/5QI treatment and negative cases not qualified",
    "NR_RF_PHY_MAC_AND_MOBILITY_PERFORMANCE": "RFSIM connectivity is not an RF/PHY/MAC conformance or dynamic radio mobility test",
    "MULTI_CYCLE_NAS_PDU_SERVICE_SLO": "Only single bounded isolated UE attach and PDU proved; no repeated registrations or sustained service SLO",
    "DATA_PLANE_THROUGHPUT_AND_LOSS": "Stage 4T passed 6 rounds with 60/60 ICMP replies per UE over ~97 s; not a sustained throughput or load-under-pressure benchmark",
    "FULL_5GC_RESTART_AUTO_UE_DATA_RECOVERY": "Stage 4P run 43208820608 FAILED: NGAP reconnected in 36.19 s, F1-C remained connected, but both UE TUNs/DRBs were released and data did not recover automatically",
    "AMF_ONLY_FAULT_UNASSISTED_UE_RECOVERY": "AMF-only failure tests 43218236191 and 43219517357: patched NGAP reconnected but both UEs failed to restore NAS/PDU/TUN unassisted within 150 s. Stage 4U (43367213888) achieved supervised recovery by explicitly restarting each synthetic UE once; native UE self-recovery is still NO-GO",
    "INDEPENDENT_STAGING_OBSERVER_CLOCK": "No independent timestamped observer and clock-offset acceptance in staging",
    "STAGING_3_CYCLE_DUAL_DU_RECOVERY": "Offline AMF fault cycle not reproduced on qualified staging topology with UE",
    "STAGING_OSM_ROLLBACK": "No actual OSM-controlled staging rollback and restored image digest",
    "STAGING_HEALTH_PERFORMANCE": "No sustained resource/PSI/restarts/registration SLO window",
    "SECURITY_ALGORITHM_NEGOTIATION_AND_COMPATIBILITY": "Stage 4X (43378042762) passed two synthetic UEs, PDU sessions and bidirectional traffic under a NEA2/NIA2-only CU configuration. Independent AS/NAS security-mode decoding, adverse security-procedure tests and staging interoperability remain unverified",
    "HCRAN_POD_PRIVILEGE_AND_NETWORK_ISOLATION_REVIEW": "Current gNB CU and DU containers run privileged; no reviewed least-privilege, Pod Security, network isolation, secret delivery or production exception decision",
    "F1_HANDOVER_UE_RFSIM_AND_CU_TELNET_BUILD": "Upstream 2026.w13 contains inter-DU F1 handover code; patched CU build lacks explicit telnetsrv module and no UE-server two-DU mobility runtime evidence",
    "PROVENANCE_ROLLBACK_APPROVAL": "Experimental artifact is expiring, no approved immutable release and operator signoff",
}


def fetch(path):
    token = os.getenv("GITHUB_TOKEN", "").strip()
    headers = {"Accept": "application/vnd.github+json",
               "User-Agent": "ngap-readiness-inventory",
               "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(API + path, headers=headers)
    with urllib.request.urlopen(req, timeout=25) as response:
        return json.load(response)


def verify_run(name, run_id, prefix):
    entry = {"gate": name, "run_id": run_id,
             "url": f"https://github.com/rathod9771/5g-kubernetes/actions/runs/{run_id}"}
    try:
        run = fetch(f"/actions/runs/{run_id}")
        artifacts = fetch(f"/actions/runs/{run_id}/artifacts?per_page=100")
        matches = [a for a in artifacts.get("artifacts", [])
                   if a["name"].startswith(prefix) and not a.get("expired", False)]
        entry.update({"status": run.get("status"), "conclusion": run.get("conclusion"),
                      "source_commit": run.get("head_sha"),
                      "artifact_ids": [x["id"] for x in matches],
                      "result": "PASS" if (run.get("status") == "completed"
                                        and run.get("conclusion") == "success"
                                        and len(matches) > 0) else "NOT_VERIFIED"})
    except Exception as error:
        entry.update({"result": "NOT_VERIFIED", "reason": type(error).__name__ + ": " + str(error)})
    return entry


def main():
    dst = pathlib.Path(os.getenv("REPORT", "/tmp/ngap-readiness.json"))
    dst.parent.mkdir(parents=True, exist_ok=True)
    observed = [verify_run(*args) for args in KNOWN]
    missing = [{"gate": key, "result": "NOT_VERIFIED", "reason": reason}
               for key, reason in NOT_VERIFIED.items()]
    # Manual staging signoff MUST NOT be extrapolated from GitHub Actions.
    report = {
        "schema_version": 2,
        "hcran_architecture": "3GPP NR gNB-CU + macro DU + small DU over F1; NG to 5GC",
        "hcran_ric_required": False,
        "e2sm_kpm_and_rc_release_gates": False,
        "monitoring": "Prometheus/Grafana; verify live scrape reachability separately",
        "cluster_ric_removal_confirmed": False,
        "generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "scope": "CI evidence inventory; no access to real HP cluster",
        "source_repository": "rathod9771/5g-kubernetes",
        "production_decision": "NO_GO",
        "production_deployment_authorized": False,
        "verified_ci_and_offline_evidence": observed,
        "outstanding_runtime_and_release_gates": missing,
        "rule": "Only 3GPP-oriented F1/NG/NAS/PDU and GTP-U historical CI evidence, including Stage 4X security-profile functional traffic, is counted. Near-RT RIC/E2SM-KPM/RC are explicitly out of scope. Basic dual-UE traffic, bounded soak and supervised synthetic UE restart passed in isolated CI; 3GPP UE mobility, protocol negative cases, native recovery, RF test, sustained performance, staging rollback and operator authorization remain unverified.",
    }
    dst.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Production decision: NO_GO. CI historical evidence: "
          f"{sum(x['result'] == 'PASS' for x in observed)}/{len(observed)}; "
          f"missing live gates: {len(missing)}.")
    print(f"Report: {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
