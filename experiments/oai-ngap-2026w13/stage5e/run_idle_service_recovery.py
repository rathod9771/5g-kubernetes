#!/usr/bin/env python3
"""Stage 5E: AMF-only fault and uplink-triggered native Service Request with 2026.w45 UE.

Disposable two-OAI-UE/two-DU/NGAP-patched CU/Open5GS test based on the
historically failing Stage4P harness. CU/DU remain frozen; only the synthetic
UE image changes to an upstream revision which implements an RRC_IDLE TUN
listener, MO-UL-triggered NAS Service Request, and Service Accept handling.

No UE process replacement/restart, no DU process restart, no manual F1
handover, no telnet, no E2/RIC, no HP cluster/OSM credentials.
No false PASS from NG Setup alone or pre-fault traffic. If there is no
post-fault bidirectional GTP-U, the experiment FAILS.
"""
import argparse
import datetime
import json
import pathlib
import re
import subprocess
import sys
import time

def make_probe(baseline, opts):
    root = pathlib.Path(baseline).resolve()
    for name in ("stage4p", "stage4o", "stage4m", "stage4k", "stage4l"):
        if not (root / name).is_dir():
            raise RuntimeError("Frozen Stage4P source checkout missing "+name)
        sys.path.insert(0,str(root/name))
    from run_dual_ue_amf_restart import Recovery, ALIVE, NGAP, REDIAL
    from run_ue_registration import REG, PDU, name, sh

    class IdleServiceProbe(Recovery):
        def ue_identity(self):
            result={}
            for role in ("ue","ue-small"):
                out=json.loads(sh("docker","inspect",name(role),timeout=25))
                if len(out)!=1 or not out[0].get("State",{}).get("Running"):
                    raise RuntimeError("Synthetic UE container not running: "+role)
                item=out[0]
                result[role]={
                    "id":item["Id"],
                    "started_at":item["State"]["StartedAt"],
                    "pid":item["State"]["Pid"],
                    "restart_count":item.get("RestartCount",0),
                }
            return result

        def pdu_addresses(self):
            result={}
            for role in ("ue","ue-small"):
                ip=sh("docker","exec",name(role),"ip","-o","-4","addr","show",
                      check=False,timeout=20)
                addresses=re.findall(r"\binet\s+(10\.45\.\d+\.\d+)/\d+",ip)
                result[role]=addresses[0] if len(addresses)==1 and "oaitun_ue1" in ip else None
            return result

        def initial_baseline(self):
            self.demand(*ALIVE)
            self.sctp_check("T90-all-three-SCTP-peers-before-AMF-fault")
            if self.amf_count()!=1:
                raise RuntimeError("AMF count must be exactly one before fault")
            result=self.pdu_addresses()
            if not all(result.values()) or len(set(result.values()))!=2:
                raise RuntimeError("Expected two distinct synthetic PDU IP addresses")
            return result

        def probe_mo_uplink(self, attempt):
            """Stimulate *real* UE TUN while the original UE processes run."""
            procs={}
            for role in ("ue","ue-small"):
                procs[role]=subprocess.Popen(
                    ["docker","exec",name(role),"ping","-I","oaitun_ue1",
                     "-c","6","-i","0.4","-W","2","10.45.0.1"],
                    stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
            results={}
            for role,proc in procs.items():
                try:
                    raw,_=proc.communicate(timeout=28)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    raw,_=proc.communicate(timeout=5)
                (self.out/f"after-amf-mo-ul-{attempt:02d}-{role}.txt").write_text(raw[-3000:])
                match=re.search(r"(\d+)\s+packets transmitted,\s+(\d+)\s+(?:packets )?received",raw)
                sent=int(match.group(1)) if match else 0
                received=int(match.group(2)) if match else 0
                results[role]={"sent":sent,"received":received,
                               "success":proc.returncode==0 and sent==6 and received==6}
            return results

        def fault_and_recover(self):
            before_ips=self.initial_baseline()
            original_identity=self.ue_identity()
            if any(s["restart_count"]!=0 for s in original_identity.values()):
                raise RuntimeError("Pre-fault test UE has restart history")
            before_ng=len(NGAP.findall(self.logs("cu")))
            before_redial=len(REDIAL.findall(self.logs("cu")))
            before_log={x:len(self.logs(x)) for x in ("ue","ue-small")}
            for old,new in (("gtpu.pcap","before-fault-dual-ue.pcap"),
                            ("dual-ue-traffic-evidence.json","before-fault-data.json")):
                file=self.out/old
                if file.exists():
                    file.rename(self.out/new)
            self.stop_amf()
            self.await_redial(before_redial)
            self.start_amf()
            self.await_ngap(before_ng)
            self.sctp_check("T91-reconnected-NGAP-with-both-F1-DUs")
            # Never count stale pre-fault traffic in user-plane proof.
            self.record("T92-after-AMF-new-UE-state","OBSERVED",
                        pdu_interfaces=self.pdu_addresses(),
                        ue_processes_unchanged=self.ue_identity()==original_identity)
            # Running UE 2026.w45 receives MO UL pending indication from
            # idle TUN listener -> NAS Service Request; do not fake a
            # successful registration or PDU with pre-fault accept messages.
            deadline=time.monotonic()+180
            rounds=[]
            self.start_capture()
            try:
                i=0
                while time.monotonic()<deadline:
                    i+=1
                    self.demand(*ALIVE)
                    if self.ue_identity()!=original_identity:
                        raise RuntimeError("UE container or process restarted, invalid native recovery proof")
                    state=self.pdu_addresses()
                    if any(not state[x] for x in ("ue","ue-small")):
                        self.record("T93-waiting-for-idle-TUN", "OBSERVED",
                                    attempt=i,active=state)
                        time.sleep(5)
                        continue
                    probes=self.probe_mo_uplink(i)
                    rounds.append({"attempt":i,"probes":probes,
                                   "at_utc":datetime.datetime.now(datetime.timezone.utc).isoformat()})
                    self.record("T94-MO-UL-ping-probe","OBSERVED",
                                attempt=i,pings=probes)
                    if all(x["success"] for x in probes.values()):
                        break
                    time.sleep(5)
            finally:
                self.stop_capture()
            recovered=bool(rounds and all(x["success"] for x in rounds[-1]["probes"].values()))
            after=self.ue_identity()
            if after!=original_identity:
                raise RuntimeError("UE process identity changed during fault test")
            # Preserve diagnostic proof whether the test failed or passed.
            state={
                "schema":"hcran-stage5e-native-mo-ul-service-v1",
                "source":"two-real-synthetic-OAI-UEs-on-isolated-GitHub-runner",
                "amf_only_fault":True,
                "new_ngap_connection_observed":True,
                "ues_were_restarted":False,
                "f1_du_was_restarted":False,
                "externally_triggered_f1_handover":False,
                "rc_ric_required":False,
                "ue_image":self.opts.ue_image,
                "before_pdu_ips":before_ips,
                "after_pdu_ips":self.pdu_addresses(),
                "user_plane_probes":rounds,
                "both_ue_icmp_recovered":recovered,
                "production_decision":"NO_GO",
                "production_authorized":False,
            }
            (self.out/"stage5e-recovery-status.json").write_text(json.dumps(state,indent=2)+"\n")
            if not recovered:
                raise RuntimeError("Both UEs FAILED to recover service through idle TUN MO UL without process restart")
            # Real per-DU and N3 GTP-U + independent TEIDs required.
            self.check_post_gtpu()
            self.sctp_check("T95-NGAP-and-F1C-after-UE-service")
            for role in ("ue","ue-small"):
                new_log=self.logs(role)[before_log[role]:]
                found=bool(re.search(r"Service Request|NAS_MO_UL_DATA_IND|Registration Accept|5GMM-REGISTERED",new_log,re.I))
                self.record("T96-"+role+"-NAS-resumption-observed",
                            "PASS" if found else "NOT_VERIFIED",
                            post_fault_nas_event=found)
                if not found:
                    raise RuntimeError(role+" recovered ping without a documented NAS/resumption event")
            self.record("STAGE5E","PASS",scope=(
                "two UE processes unchanged; AMF-only restart followed by "
                "MO-uplink-triggered NAS service and real F1-U/NG-U"),
                production_decision="NO_GO",
                still_required="multi-cycle staging, negative cases, formal 3GPP tests and real OSM rollback")

    return IdleServiceProbe(opts)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--baseline",required=True)
    for key in ("rendered","output","cu-image","du-image","ue-image"):
        p.add_argument("--"+key,required=True)
    p.add_argument("--verify-gtpu",action="store_true")
    args=p.parse_args()
    if not args.verify_gtpu:
        raise SystemExit("FAIL CLOSED: actual F1-U/NG-U wire proof is mandatory")
    return make_probe(args.baseline,args).run()

if __name__=="__main__":
    raise SystemExit(main())
