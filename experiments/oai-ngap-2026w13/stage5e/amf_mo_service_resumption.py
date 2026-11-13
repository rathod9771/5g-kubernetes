#!/usr/bin/env python3
"""Stage 5E: real Service Request resumption with OAI UE 2026.w45 after AMF loss.

Disposable Docker only: proven patched W13 CU, two W25 DUs, W45 UE (new
3GPP TS 24.501 MO UL Service Request implementation), synthetic Open5GS.
One UE RFsim server remains same process. After prior 2-way handover, stop
AMF only; wait for patched CU NGAP reconnect. Trigger pending UL packet on
the retained UE PDU TUN (NO UE restart), watch MO-UL Service Request and
Service Accept, then 6/6 ICMP and bidirectional F1-U/NG-U pcap.

Do NOT require new Registration Accept or PDU establishment if service
resumed using a retained PDU context; reject stale pre-fault packets.
Fail closed if full-core failure, missing TUN, absent NAS Service Request,
source-release-only F1 transition, UE recreation or lost user-plane.
Even PASS is on-demand Service Request, NOT spontaneous AMF recovery,
nor proof of seamless user plane continuity or production resilience.
"""
import argparse
import json
import pathlib
import re
import subprocess
import sys
import time

ALIVE=("core","cu","macro","small","ue")
NGAP=re.compile("Received NGSetupResponse from AMF",re.I)
MO_IND=re.compile("NAS_MO_UL_DATA_IND|MO UL data: request Service Request",re.I)
SR_INIT=re.compile("Generate initial NAS message: Service Request|Send NAS_INITIAL_UL_TRANSFER_REQ",re.I)
SR_ACCEPT=re.compile("Received NAS Service Accept|Received Service Accept|FGS_SERVICE_ACCEPT|Service Accept",re.I)
RRC_IDLE=re.compile("RRC moved into IDLE state|Received RRC Release",re.I)


def build(args):
    root=pathlib.Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(root/"stage4z"))
    import f1_handover_runtime as stage4z
    probe=stage4z.build_probe(args)
    verified_baseline=probe.ue
    from run_ue_registration import IPS,name,sh
    import run_ue_registration as core_runner

    # Exactly Stage 4P's AMF-only fault isolation: keep all other NFs
    # running when the AMF process dies. The pinned Stage4O bootstrap
    # uses wait -n, which instead shuts down the entire Docker core.
    original=core_runner.sh
    def core_guard(*argv,**kw):
        cmd=tuple(str(v) for v in argv)
        if cmd[:2]==("docker","run") and "--name" in cmd:
            i=cmd.index("--name")
            if i+1<len(cmd) and cmd[i+1]==name("core"):
                file=probe.priv/"start-core.sh"
                text=file.read_text()
                suffix="done\nwait -n\n"
                if not text.endswith(suffix):
                    raise RuntimeError("Pinned isolated core startup changed; refuse AMF fault")
                file.write_text(text[:-len("wait -n\n")]+"wait\n")
                probe.record("T90-AMF-only-fault-launcher","PASS")
        return original(*cmd,**kw)
    core_runner.sh=core_guard

    def amf_count():
        command='n=0; for f in /proc/[0-9]*/comm; do [ -f "$f" ] || continue; if [ "$(cat "$f")" = "open5gs-amfd" ]; then n=$((n+1)); fi; done; echo "$n"'
        return int(sh("docker","exec",name("core"),"/bin/sh","-ec",command,timeout=20).strip())

    def identity():
        raw=json.loads(sh("docker","inspect",name("ue")))
        if len(raw)!=1 or not raw[0].get("State",{}).get("Running"):
            raise RuntimeError("UE unavailable / restarted")
        state=raw[0]["State"]
        return (raw[0]["Id"],state["Pid"],state["StartedAt"],raw[0]["RestartCount"])

    def new_messages_since(n,m):
        return len(n.findall(probe.logs("ue")))>m

    def test(self):
        verified_baseline()  # full real Stage4Z macro->small->macro evidence
        self.demand(*ALIVE)
        initial=identity()
        pdu_addr=self.ue_ip()
        if amf_count()!=1:
            raise RuntimeError("AMF baseline not exactly one process")
        n0=len(NGAP.findall(self.logs("cu")))
        ue_before=self.logs("ue")
        if n0==0: raise RuntimeError("No initial NGAP setup to compare")
        self.record("T91-pinned-W45-UE-two-way-F1-baseline","PASS",
                    pdu_address_before=pdu_addr)
        # Rename prior pcap and evidence; never count pre-fault traffic.
        for old,new in (("gtpu.pcap","prefault-final-gtpu.pcap"),
                        ("stage4z-f1-handover-evidence.json","prefault-stage4z-evidence.json")):
            file=self.out/old
            if file.exists(): file.rename(self.out/new)
        stop='found=0; for f in /proc/[0-9]*/comm; do [ -f "$f" ] || continue; if [ "$(cat "$f")" = "open5gs-amfd" ]; then pid=$(echo "$f" | cut -d/ -f3); kill -TERM "$pid"; found=1; fi; done; test "$found" = 1'
        sh("docker","exec",name("core"),"/bin/sh","-ec",stop,timeout=25)
        deadline=time.monotonic()+20
        while time.monotonic()<deadline and amf_count()!=0:
            time.sleep(1)
        if amf_count()!=0:
            raise RuntimeError("AMF stop unsuccessful")
        self.demand(*ALIVE)
        self.record("T92-only-AMF-stopped","PASS")
        start='exec open5gs-amfd -c /tmp/stage4k-conf/amf.yaml >> /tmp/stage4k-log/amf-restarted.log 2>&1'
        sh("docker","exec","-d",name("core"),"/bin/sh","-c",start,timeout=25)
        deadline=time.monotonic()+145
        while time.monotonic()<deadline:
            self.demand(*ALIVE)
            if amf_count()==1 and len(NGAP.findall(self.logs("cu")))>n0:
                self.record("T93-CU-new-NGSetupResponse","PASS")
                break
            time.sleep(3)
        else:
            raise RuntimeError("Patched CU did not reconnect to restarted AMF")
        deadline=time.monotonic()+20
        while time.monotonic()<deadline and not RRC_IDLE.search(self.logs("ue")[len(ue_before):]):
            time.sleep(1)
        had_idle=bool(RRC_IDLE.search(self.logs("ue")[len(ue_before):]))
        # This is an on-demand service request, not spontaneous recovery:
        # the newer UE must retain the original TUN in 5GMM-IDLE.
        raw=sh("docker","exec",name("ue"),"ip","-o","-4","addr","show",check=False,timeout=20)
        self.record("T94-UE-RRC-IDLE-after-AMF-fault","OBSERVED",entered_idle=had_idle,
                    retained_tun=("oaitun_ue1" in raw and pdu_addr in raw))
        if "oaitun_ue1" not in raw or pdu_addr not in raw:
            raise RuntimeError("UE PDU TUN not preserved across RRC idle; cannot trigger MO-UL Service Request")
        if identity()!=initial:
            raise RuntimeError("UE process changed before on-demand UL probe")
        # Queue UE-originating user data in TUN to cause 3GPP 24.501
        # Service Request. A few initial packet drops are allowed during
        # establishment; post-recovery ICMP is assessed separately.
        mo_before=len(MO_IND.findall(self.logs("ue")))
        sr_before=len(SR_INIT.findall(self.logs("ue")))
        ac_before=len(SR_ACCEPT.findall(self.logs("ue")))
        sh("docker","exec",name("ue"),"/bin/sh","-c",
           "ping -I oaitun_ue1 -c 2 -W 1 -i .6 10.45.0.1 >/dev/null 2>&1 || true",
           timeout=15)
        deadline=time.monotonic()+95
        observed={"mo_indication":False,"service_request":False,"service_accept":False}
        while time.monotonic()<deadline:
            self.demand(*ALIVE)
            observed={
                "mo_indication":new_messages_since(MO_IND,mo_before),
                "service_request":new_messages_since(SR_INIT,sr_before),
                "service_accept":new_messages_since(SR_ACCEPT,ac_before),
            }
            if all(observed.values()): break
            time.sleep(3)
        (self.out/"stage5e-nas-service-observations.json").write_text(
            json.dumps({"observed":observed,"retained_pdu_ip":pdu_addr,
                        "native_spontaneous_recovery_proven":False,
                        "requested_on_demand":True,
                        "production_decision":"NO_GO"},indent=2)+"\n")
        if not all(observed.values()):
            raise RuntimeError("MO Service Request / Accept not fully observed: "+str(observed))
        if self.ue_ip()!=pdu_addr or identity()!=initial:
            raise RuntimeError("UE restart or PDU address change after resumption")
        packet=self.capture_f1_data("post-AMF-service-request","172.29.76.30")
        self.record("STAGE5E","PASS",
                    **{"mode":"on-demand NAS Service Request after AMF restart",
                       "ue_process_preserved":True,"pdu_ip_preserved":True,
                       "post_amf_gtpu":packet["packet_counts"],
                       "production_decision":"NO_GO",
                       "native_spontaneous_recovery_proven":False})

    import types
    probe.ue=types.MethodType(test,probe)
    return probe


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--baseline",required=True)
    for name in ("rendered","output","cu-image","du-image","ue-image"):
        p.add_argument("--"+name,required=True)
    p.add_argument("--verify-gtpu",action="store_true")
    opts=p.parse_args()
    if not opts.verify_gtpu:
        raise SystemExit("FAIL CLOSED: --verify-gtpu required")
    return build(opts).run()


if __name__=="__main__":
    raise SystemExit(main())
