#!/usr/bin/env python3
"""Stage 5D: test AMF recovery followed by controlled F1 handover, same UE process.

Leverages the PASS Stage 4Z RFsim setup: one synthetic UE is the RF server;
macro and small DUs are clients; an OAI NGAP-patched CU with CI telnetsrv
triggers actual F1 handovers. Stage 5D tests whether additional F1 mobility
can restore NAS/PDU+GTP-U after a simulated AMF-only restart.

Manual post-fault F1 handover is an *assisted* radio control, NEVER
spontaneous AMF recovery. A FAIL is evidence of non-recovery, no restarts.
Unauthenticated telnet is confined to disposable Docker bridge.
"""
import argparse
import json
import pathlib
import re
import sys
import time

NGAP=re.compile(r"Received NGSetupResponse from AMF",re.I)
REG=re.compile(r"Registration Accept|5GMM-REGISTERED|registration procedure completed",re.I)
PDU=re.compile(r"PDU Session Establishment Accept|PDU session established",re.I)
ALIVE=("core","cu","macro","small","ue")


def create_probe(opts):
    here=pathlib.Path(__file__).resolve()
    sys.path.insert(0,str(here.parents[1]/"stage4z"))
    import f1_handover_runtime as stage4z
    base=stage4z.build_probe(opts)
    previous_ue=base.ue
    import run_ue_registration as core_runner
    from run_ue_registration import IPS, name, sh

    # Stage4O is pinned to the old bootstrap where the core launcher uses
    # wait -n. That exits PID 1 as soon as AMF stops, turning an AMF-only
    # fault into a FULL CORE outage. Reproduce Stage4P's validated
    # 'wait' behavior only inside this disposable runner. No host/OSM writes.
    bootstrap_sh=core_runner.sh
    def preserve_non_amf_core(*argv,**kwargs):
        cmd=tuple(str(x) for x in argv)
        if cmd[:2]==("docker","run") and "--name" in cmd:
            idx=cmd.index("--name")
            if idx+1<len(cmd) and cmd[idx+1]==name("core"):
                launcher=base.priv/"start-core.sh"
                contents=launcher.read_text()
                if not contents.endswith("done\\nwait -n\\n"):
                    raise RuntimeError("Core bootstrap format changed: cannot safely isolate AMF fault")
                launcher.write_text(contents[:-len("wait -n\\n")]+"wait\\n")
                base.record("T79-AMF-only-core-launcher-keeps-other-NFs-alive","PASS")
        return bootstrap_sh(*cmd,**kwargs)
    core_runner.sh=preserve_non_amf_core

    def count_amf():
        script='n=0; for f in /proc/[0-9]*/comm; do [ -f "$f" ] || continue; if [ "$(cat "$f")" = "open5gs-amfd" ]; then n=$((n+1)); fi; done; echo "$n"'
        return int(sh("docker","exec",name("core"),"/bin/sh","-ec",script,timeout=20).strip())

    def inspect_ue():
        items=json.loads(sh("docker","inspect",name("ue")))
        if len(items)!=1:
            raise RuntimeError("Ambiguous synthetic UE container")
        state=items[0].get("State",{})
        if not state.get("Running"):
            raise RuntimeError("Synthetic UE process has stopped")
        return {"id":items[0]["Id"],"pid":state["Pid"],
                "started":state["StartedAt"],"restarts":items[0].get("RestartCount",0)}

    def controlled_test(self):
        previous_ue() # real macro->small->macro F1 handovers already required
        self.demand(*ALIVE)
        prev_ue=inspect_ue()
        current_ip=self.ue_ip()
        if count_amf()!=1:
            raise RuntimeError("Baseline AMF process not exactly one")
        old_ng=len(NGAP.findall(self.logs("cu")))
        regs=len(REG.findall(self.logs("ue")))
        pdus=len(PDU.findall(self.logs("ue")))
        self.record("T80-baseline-F1-and-NAS-ready","PASS",amf=1)
        stop='found=0; for f in /proc/[0-9]*/comm; do [ -f "$f" ] || continue; if [ "$(cat "$f")" = "open5gs-amfd" ]; then pid=$(echo "$f" | cut -d/ -f3); kill -TERM "$pid"; found=1; fi; done; test "$found" = 1'
        sh("docker","exec",name("core"),"/bin/sh","-ec",stop,timeout=30)
        deadline=time.monotonic()+22
        while count_amf()!=0 and time.monotonic()<deadline:
            time.sleep(1)
        if count_amf()!=0:
            raise RuntimeError("AMF fault was not established")
        self.record("T81-AMF-stopped","PASS")
        # No UE or DU process recreation permitted.
        restart='exec open5gs-amfd -c /tmp/stage4k-conf/amf.yaml >> /tmp/stage4k-log/amf-restarted.log 2>&1'
        sh("docker","exec","-d",name("core"),"/bin/sh","-c",restart,timeout=25)
        deadline=time.monotonic()+140
        while time.monotonic()<deadline:
            self.demand(*ALIVE)
            if count_amf()==1 and len(NGAP.findall(self.logs("cu")))>old_ng:
                self.record("T82-new-NGSetupResponse","PASS")
                break
            time.sleep(3)
        else:
            raise RuntimeError("No new CU NG Setup Response after AMF restart")
        self.record("T83-AMF-only-recovery-state","OBSERVED",
                    fresh_reg=len(REG.findall(self.logs("ue")))>regs,
                    fresh_pdu=len(PDU.findall(self.logs("ue")))>pdus)
        old_cu=len(self.logs("cu"))
        old_ue=len(self.logs("ue"))
        self.telnet_command("ci trigger_f1_ho")
        self.wait_completed_ho(101,old_cu,"small",timeout=100)
        self.record("T84-controlled-F1-radio-handover","OBSERVED",
                    mode="operator-triggered not native")
        deadline=time.monotonic()+85
        while time.monotonic()<deadline:
            self.demand(*ALIVE)
            uelog=self.logs("ue")[old_ue:]
            if re.search("RRCReconfigurationComplete",uelog,re.I):
                self.record("T85-UE-RRC-completed-after-AMF-restart","PASS")
                break
            time.sleep(2)
        else:
            raise RuntimeError("No RRC ReconfigurationComplete after recovery F1 mobility")

        state={}
        deadline=time.monotonic()+100
        while time.monotonic()<deadline:
            self.demand(*ALIVE)
            output=self.logs("ue")
            state={
                "fresh_registration":len(REG.findall(output))>regs,
                "fresh_pdu_session":len(PDU.findall(output))>pdus,
                "new_or_preserved_tun":False,
            }
            try:
                state["new_or_preserved_tun"]=self.ue_ip()==current_ip
            except RuntimeError:
                pass
            if all(state.values()):
                break
            time.sleep(5)
        (self.out/"stage5d-nas-pdu-state.json").write_text(
            json.dumps({"state":state,"assisted_f1_handover":True,
                        "native_amf_recovery_proven":False,
                        "production_decision":"NO_GO"},indent=2)+"\n")
        if not all(state.values()):
            raise RuntimeError("After extra F1 handover, UE did not re-register and establish PDU service: "+str(state))
        gtpu=self.capture_f1_data("post-amf-F1-assisted", "172.29.76.31")
        if inspect_ue()!=prev_ue:
            raise RuntimeError("UE process was restarted: invalid continuity evidence")
        self.record("STAGE5D","PASS",
                    mode="manual F1 handover AFTER AMF reconnect, no UE process restart",
                    f1u_ngu_packet_counts=gtpu["packet_counts"],
                    native_amf_recovery_proven=False,
                    production_decision="NO_GO")

    import types
    base.ue=types.MethodType(controlled_test,base)
    return base


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--baseline",required=True)
    for name in ("rendered","output","cu-image","du-image","ue-image"):
        p.add_argument("--"+name,required=True)
    p.add_argument("--verify-gtpu",action="store_true")
    args=p.parse_args()
    if not args.verify_gtpu:
        raise SystemExit("FAIL CLOSED: actual GTP-U evidence is required")
    return create_probe(args).run()


if __name__=="__main__":
    raise SystemExit(main())
