#!/usr/bin/env python3
"""Stage 5C: 24-round NEA2/NIA2 two-UE end-to-end RFsim endurance trial.

Proven Stage4T repeated 6 rounds / ~97 s. This extension requires 24 rounds,
10 ICMP per UE/round, 15s between rounds, live 3-SCTP association checks,
zero container restarts/OOM, per-round Docker resource stats, F1-U/NG-U
PCAP, >=360s observed. Still only a short isolated RFsim endurance
trial, not production load testing, radio compliance or availability SLO.
No HP cluster, no OSM, no E2/RIC, synthetic subscribers only.
"""
import argparse
import datetime
import json
import pathlib
import sys

ROUNDS=24
SAMPLE_GAP_SEC=15
MIN_OBSERVED_SECONDS=360


def make_probe(baseline,args):
    root=pathlib.Path(baseline).resolve()
    for name in ("stage4t","stage4o","stage4m","stage4k","stage4l"):
        if not (root/name).is_dir():
            raise RuntimeError("Frozen Stage4T source missing "+name)
        sys.path.insert(0,str(root/name))
    import run_dual_ue_soak
    run_dual_ue_soak.ROUNDS=ROUNDS
    run_dual_ue_soak.SAMPLE_GAP_SEC=SAMPLE_GAP_SEC

    class Endurance(run_dual_ue_soak.Soak):
        def soak(self):
            super().soak()
            report=json.loads((self.out/"dual-UE-soak-evidence.json").read_text())
            if report["round_count"]!=ROUNDS or len(report["samples"])!=ROUNDS:
                raise RuntimeError("Incomplete 24-round sampling")
            if report["observed_duration_sec"]<MIN_OBSERVED_SECONDS:
                raise RuntimeError("Minimum bounded endurance observation time not reached")
            if report["per_ue_pings_sent"]!=ROUNDS*10 or report["per_ue_pings_received"]!=ROUNDS*10:
                raise RuntimeError("Not all 240 ICMP replies per UE received")
            if any(not item["all_restarts_zero"] or item["docker_stat_rows"]<6
                   or any(p["sent"]!=10 or p["received"]!=10
                          for p in item["pings"].values()) for item in report["samples"]):
                raise RuntimeError("Lost UE service, CPU/memory sampling or restart in extended run")
            if len({record["at_utc"] for record in report["samples"]})!=ROUNDS:
                raise RuntimeError("Duplicate or fabricated observation sample timestamps")
            evidence={
                "schema":"hcran-5c-extended-rfsim-v1","mode":"isolated_real_dual_ue",
                "duration_seconds":report["observed_duration_sec"],
                "rounds":ROUNDS,
                "per_ue_icmp":ROUNDS*10,
                "actual_ran_f1u_gtpu":True,"actual_ngu_gtpu":True,
                "all_samples_current_and_zero_restarts":True,
                "captured_pcap_sha256":report["pcap_sha256"],
                "prometheus_staging_scrapes_proven":False,
                "production_resource_slo_proven":False,
                "radio_rf_conformance_proven":False,
                "production_authorized":False,"production_decision":"NO_GO",
            }
            (self.out/"stage5c-extended-soak-evidence.json").write_text(
                json.dumps(evidence,indent=2)+"\n")
            self.record("STAGE5C","PASS",rounds=ROUNDS,
                        duration_sec=evidence["duration_seconds"],
                        each_ue_icmp=evidence["per_ue_icmp"],
                        production_decision="NO_GO")
    return Endurance(args)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--baseline",required=True)
    for key in ("rendered","output","cu-image","du-image","ue-image"):
        p.add_argument("--"+key,required=True)
    p.add_argument("--verify-gtpu",action="store_true")
    args=p.parse_args()
    if not args.verify_gtpu:
        raise SystemExit("FAIL CLOSED: GTP-U capture and both UE PDU service required")
    return make_probe(args.baseline,args).run()


if __name__=="__main__":
    raise SystemExit(main())
