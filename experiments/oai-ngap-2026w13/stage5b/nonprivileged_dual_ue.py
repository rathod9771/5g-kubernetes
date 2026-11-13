#!/usr/bin/env python3
"""Isolated 3GPP H-CRAN CU/DU least-capability runtime experiment.

Runs the proven Stage4O dual-UE+core+F1/NG/ICMP harness unchanged except
that ONLY the RAN CU/macro DU/small DU Docker runs are injected with
--cap-drop ALL and --security-opt no-new-privileges.
Core and UEs keep necessary NET_ADMIN/TUN permissions. No cluster access.
Passing proves an isolated container compatibility case, NOT Kubernetes
PSA admission/NetworkPolicy enforcement, production conformance, or SLO.
"""
import argparse
import json
import pathlib
import re
import sys

def create_probe(baseline, args):
    root=pathlib.Path(baseline).resolve()
    for section in ("stage4o","stage4m","stage4k","stage4l"):
        if not (root/section).is_dir():
            raise RuntimeError("Frozen Stage4O source missing "+section)
        sys.path.insert(0,str(root/section))
    import run_ue_registration
    import dual_du_macro_ue_traffic
    import run_two_ues
    original=run_ue_registration.sh
    ran_names={run_ue_registration.name(x) for x in ("cu","macro","small")}
    captured=set()

    def guarded_sh(*argv, **kwargs):
        command=tuple(str(x) for x in argv)
        if command[:2]==("docker","run") and "--name" in command:
            i=command.index("--name")
            container=command[i+1] if i+1<len(command) else None
            if container in ran_names:
                if container in captured:
                    raise RuntimeError("RAN role started twice, refuse ambiguous privilege test")
                if "--privileged" in command or "--cap-add" in command:
                    raise RuntimeError("Unexpected RAN privilege override")
                captured.add(container)
                command=command[:2]+(
                    "--cap-drop","ALL",
                    "--security-opt","no-new-privileges=true",
                )+command[2:]
        return original(*command, **kwargs)

    for module in (run_ue_registration,dual_du_macro_ue_traffic,run_two_ues):
        module.sh=guarded_sh

    class NoPrivilegeProbe(run_two_ues.DualUEProbe):
        def ue(self):
            super().ue()  # Stage4O two UE, NAS, PDU, F1U/NGU, ICMP
            if captured!=ran_names:
                raise RuntimeError("Not all three RAN components received security flags: "+str(captured))
            findings={}
            for role in ("cu","macro","small"):
                container=run_ue_registration.name(role)
                info=json.loads(guarded_sh("docker","inspect",container))
                if len(info)!=1:
                    raise RuntimeError("Ambiguous inspect for "+role)
                config=info[0]["HostConfig"]
                if config.get("Privileged") is not False:
                    raise RuntimeError(role+" is privileged")
                if "ALL" not in (config.get("CapDrop") or []):
                    raise RuntimeError(role+" Linux capabilities were not dropped")
                security=config.get("SecurityOpt") or []
                if not any("no-new-privileges" in x for x in security):
                    raise RuntimeError(role+" privilege escalation guard absent")
                status=guarded_sh("docker","exec",container,"cat","/proc/1/status")
                effective=re.search(r"^CapEff:\s*([0-9a-fA-F]+)",status,re.M)
                if not effective or int(effective.group(1),16)!=0:
                    raise RuntimeError(role+" process still holds effective capabilities")
                findings[role]={"privileged":False,"cap_drop":["ALL"],
                                "no_new_privileges":True,"effective_capabilities":"0"}
            (self.out/"stage5b-nonprivileged-evidence.json").write_text(
                json.dumps({"ran":findings,"two_real_synthetic_ues":True,
                    "stage4o_user_plane_passed":True,"ric_required":False,
                    "proves_kubernetes_psa":False,"proves_networkpolicy":False,
                    "production_decision":"NO_GO"},indent=2)+"\n")
            self.record("STAGE5B","PASS",
                scope="CU and 2 DUs with no Linux capabilities, no privilege escalation",
                production_decision="NO_GO")
    return NoPrivilegeProbe(args)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--baseline",required=True)
    for key in ("rendered","output","cu-image","du-image","ue-image"):
        p.add_argument("--"+key,required=True)
    p.add_argument("--verify-gtpu",action="store_true")
    args=p.parse_args()
    if not args.verify_gtpu:
        raise SystemExit("FAIL CLOSED: real GTP-U proof required")
    return create_probe(args.baseline,args).run()

if __name__=="__main__":
    raise SystemExit(main())
