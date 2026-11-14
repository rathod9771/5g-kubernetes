#!/usr/bin/env python3
"""Stage5I final evidence gate. Stage5H cause-9 + fresh GTP-U must also pass.

Source/compilation-only success, NG Setup alone, or a synthetic fixture
MUST NOT be reported as a real UE recovery PASS.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"stage5h"))
from evidence_contract import check as check_stage5h

CU_MARK = "Stage5I: InitialUEMessage undelivered; release local RRC UE"
UE_MARK = "Stage5I: RRC release interrupted Service Request; idle TUN listener rearmed"

def check(folder):
    folder=Path(folder)
    ok, issues = check_stage5h(folder)
    issues = list(issues)
    try:
        events=json.loads((folder/"results.json").read_text())
        trace=json.loads((folder/"stage5h-postfault-protocol-trace.json").read_text())
        race=[x for x in events if x.get("stage")=="T102A-pre-NGAP-service-request-race"]
        if len(race)!=1 or race[0].get("premature_service_requests",0)<1:
            issues.append("No real pre-NGAP Service Request race exercised")
        release=[x for x in events if x.get("stage")=="T102B-local-CU-release-and-UE-idle-rearm"]
        if len(release)!=1 or release[0].get("status")!="PASS" or not all(
            release[0].get(k) is True for k in ("cu_released","ue_rearmed")
        ):
            issues.append("Missing live CU release + UE listener rearm PASS")
        if not any(x.get("stage")=="STAGE5I" and x.get("status")=="PASS" for x in events):
            issues.append("Stage5I runtime did not pass")
        for role, marker in (("cu",CU_MARK),("ue",UE_MARK)):
            lines=trace.get(role,{}).get("lines",[])
            if not isinstance(lines,list) or not any(marker in line for line in lines):
                issues.append("Missing real post-fault "+role+" marker")
    except (OSError,ValueError,TypeError,KeyError,IndexError) as exc:
        issues.append("Incomplete Stage5I evidence: "+str(exc))
    return ok and not issues,issues

def main():
    p=argparse.ArgumentParser()
    p.add_argument("evidence",type=Path)
    args=p.parse_args()
    ok,issues=check(args.evidence)
    print(json.dumps({"stage":"STAGE5I","status":"PASS" if ok else "FAIL",
                      "reasons":issues,"production_decision":"NO_GO"},indent=2))
    return 0 if ok else 1

if __name__=="__main__":
    raise SystemExit(main())
