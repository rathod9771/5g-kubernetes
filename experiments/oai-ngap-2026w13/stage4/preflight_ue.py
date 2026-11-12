#!/usr/bin/env python3
"""H-CRAN OAI isolated UE/PDU eligibility preflight.

Renders synthetic (non-deployable) UE profiles in memory using canonical
prepare_simulated_ue.render. No Kubernetes, OSM, Docker, radio, IMSI/secret
artifact upload, subscriber creation or real UE activity. Production NO-GO.
"""
import datetime
import json
from pathlib import Path
import sys
import traceback

import yaml

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT/"scripts"))
from prepare_simulated_ue import render
from scenario_registry import load_registry, select_scenarios

REPORT=Path(sys.argv[1] if len(sys.argv)>1 else "/tmp/ue-hcran-preflight.json")
checks=[]
def verify(name, test):
    try:
        test()
        checks.append({"name":name,"result":"PASS"})
        print("PASS "+name, flush=True)
    except Exception as e:
        checks.append({"name":name,"result":"FAIL","reason":str(e)})
        print("FAIL "+name+": "+str(e), flush=True)
def must(cond, reason):
    if not cond:
        raise AssertionError(reason)
def one(docs, kind, name):
    x=[d for d in docs if d.get("kind")==kind and d.get("metadata",{}).get("name")==name]
    must(len(x)==1, f"expected {kind}/{name}, got {len(x)}")
    return x[0]
def main():
    cfg={"DEPLOYMENT_PROFILE":"rfsim", "MCC":"208", "MNC":"93",
         "DNN":"internet", "SST":"1", "SD":"010203"}
    try:
        scenario=select_scenarios(load_registry(), ["hcran-oai"])[0]
        must(scenario["key"]=="hcran-oai","wrong scenario")
        manifests={}
        for i,cell in enumerate(("macro","small"),1):
            # Reserved, clearly synthetic identifiers. NEVER write the
            # rendered Secret or the identifier to evidence or CI logs.
            dummy={"imsi":"20893000000000"+str(i),"key":"1"*32,"opc":"2"*32}
            manifests[cell]=render(dummy,scenario,"ngap-ci-synthetic",cfg,root=ROOT,cell=cell)
        def ue_check(cell):
            docs=manifests[cell]
            name="oai-nr-ue-hcran-oai-"+cell
            dep=one(docs,"Deployment",name)
            must(dep["spec"]["replicas"]==0,"UE replicas must remain zero")
            pod=dep["spec"]["template"]["spec"]
            must(len(pod["initContainers"])==1,"discovery sidecar mismatch")
            env=pod["initContainers"][0]["env"]
            selectors=[e["value"] for e in env if e["name"]=="GNB_LABEL_SELECTOR"]
            must(selectors==["app=hcran-oai-"+cell], "incorrect DU discovery selector")
            must(pod["containers"][0]["image"] not in ("", "APPROVED_OAI_UE_IMAGE"),
                 "UE image not resolved by policy")
            must(dep["metadata"]["annotations"]["5g-kubernetes/ue-cell"]==cell,
                 "missing UE cell provenance")
            secrets=[x for x in docs if x.get("kind")=="Secret"]
            must(len(secrets)==1,"expected exactly one synthetic Secret in memory")
            must(all(x.get("metadata",{}).get("namespace")=="ngap-ci-synthetic"
                     for x in docs if x.get("metadata",{}).get("namespace")),
                 "unexpected namespace")
            init=one(docs,"ConfigMap",name+"-init")["data"]["ue-init-cran.sh"]
            for x in ("--rfsim","-C 3450720000","--ssb 516","-r 106","--band 78"):
                must(x in init,f"missing RF simulator UE radio parameter {x}")
            must("-C 3319680000" not in "\n".join(x for x in init.splitlines()
                  if x.lstrip().startswith("exec ")), "incorrect monolithic carrier used")
        verify("H-CRAN macro UE isolated render, labels, RFsim, replicas zero",
               lambda: ue_check("macro"))
        verify("H-CRAN small UE isolated render, labels, RFsim, replicas zero",
               lambda: ue_check("small"))
        def core_check():
            vals=yaml.safe_load((ROOT/"helm/open5gs/values-5g.yaml").read_text())
            for nf in ("mongodb","amf","smf","upf","nrf","ausf","udm","udr"):
                must(vals[nf]["enabled"] is True, f"5GC component disabled: {nf}")
            must(vals["populate"]["enabled"] is True,"subscriber provisioner disabled")
        verify("Open5GS 5GC components and subscriber provisioner configured",
               core_check)
        def no_prod():
            must(not (ROOT/".github/workflows/oai-ngap-2026w13-stage4j.yml").read_text().find(
                 "helm upgrade")>=0,"deployment action unexpected")
        verify("no Helm deployment action in eligibility workflow",no_prod)
    except Exception as e:
        checks.append({"name":"fatal preflight","result":"FAIL","reason":str(e),
                       "traceback":traceback.format_exc()})
    passed=bool(checks) and all(v["result"]=="PASS" for v in checks)
    report={
        "generated_utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "stage":"4J isolated H-CRAN OAI synthetic UE prerequisite audit",
        "static_preflight_passed":passed,
        "execution_type":"no UE instantiated; no RFsim attach; no PDU traffic",
        "production_decision":"NO_GO",
        "missing":["real isolated Open5GS Mongo+UDR+UDM+AUSF+AMF+SMF+UPF",
                   "separate synthetic subscriber provision and key consistency",
                   "RFSIM UE Registration Accept per H-CRAN DU",
                   "PDU Session Establishment Accept and UE IP",
                   "F1-U/NG-U GTP-U counters and data-plane traffic",
                   "dual-DU E2 KPM indications and RC effect",
                   "actual staging OSM rollback with operator approval"],
        "checks":checks
    }
    REPORT.parent.mkdir(parents=True,exist_ok=True)
    REPORT.write_text(json.dumps(report,indent=2)+"\n")
    print(f"Static H-CRAN UE preflight: {'PASS' if passed else 'FAIL'}; "
          "production NO_GO; no subscriber data persisted")
    return 0 if passed else 1
if __name__=="__main__":
    raise SystemExit(main())
