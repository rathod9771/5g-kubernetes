#!/usr/bin/env python3
"""Stage5ZI static freeze gate. Read-only; no cluster, radio or live probes.

Validates exact SHA and frozen Git blob IDs for the qualified Stage5ZG
simulation, safety invariants of the staged workflow and bounded evidence
metadata. This can qualify *static staging preparation only*.
"""
import argparse
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
MANIFEST = ROOT / "experiments/oai-ngap-2026w13/stage5zi/stage5zg-experimental-freeze.json"
LIVE = ROOT / ".github/workflows/hcran-stage5y-amf-ingress-observer.yml"
HEX40 = re.compile(r"^[0-9a-f]{40}$")
SAFE_PATH = re.compile(r"^(?:scripts|experiments|helm)/[A-Za-z0-9._/-]+$")

class QualificationError(RuntimeError):
    pass

def _git(*args):
    p=subprocess.run(["git","-C",str(ROOT),*args],capture_output=True,text=True,timeout=20)
    if p.returncode:
        raise QualificationError("Git source contract not available for "+args[0])
    return p.stdout.strip()

def verify(manifest):
    freeze=manifest.get("freeze",{})
    sha=freeze.get("source_commit","")
    if not HEX40.fullmatch(sha):
        raise QualificationError("Invalid exact frozen source SHA")
    if manifest.get("schema")!="hcran-stage5zg-experiment-freeze-v1":
        raise QualificationError("Unrecognized freeze manifest schema")
    if manifest.get("qualification")!="EXPERIMENTAL_QUALIFIED":
        raise QualificationError("Frozen qualification status missing")
    if manifest.get("production_authorized") is not False or manifest.get("production_decision")!="NO_GO":
        raise QualificationError("Production NO-GO is mandatory")
    if freeze.get("source_repository")!="rathod9771/5g-kubernetes":
        raise QualificationError("Wrong repository")
    if freeze.get("upstream_oai_w45_ue_commit")!="dfb296ab867050a7bde81b393fa31b757ada2c83":
        raise QualificationError("OAI upstream source pin drift")
    if freeze.get("snapshot_branch")!="experiment/hcran-stage5zg-qualified-freeze-20261119":
        raise QualificationError("Unexpected freeze branch name")
    evidence=manifest.get("passed_live_evidence",{})
    if (evidence.get("workflow_commit")!=sha
        or evidence.get("workflow_run_id")!=44457384676
        or evidence.get("workflow_conclusion")!="success"
        or evidence.get("genuine_cycles_attempted")!=6
        or evidence.get("genuine_cycles_passed")!=6
        or evidence.get("stage5u_negative_controls_rejected_per_trial")!=9
        or evidence.get("stage5y_both_cells_amf_ingress_passed") is not True
        or evidence.get("both_bounded_traces_collection_issues")!=0):
        raise QualificationError("Frozen live evidence metadata mismatch")
    _git("cat-file","-e",sha+"^{commit}")
    p=subprocess.run(["git","-C",str(ROOT),"merge-base","--is-ancestor",sha,"HEAD"],
                     capture_output=True,text=True,timeout=20)
    if p.returncode:
        raise QualificationError("Staging branch is not descended from frozen experiment")
    pinned=manifest.get("pinned_path_blobs",{})
    if not isinstance(pinned,dict) or len(pinned)<10:
        raise QualificationError("Expected source-blob inventory incomplete")
    for path,blob in sorted(pinned.items()):
        if (not SAFE_PATH.fullmatch(path) or ".." in pathlib.PurePosixPath(path).parts
            or not HEX40.fullmatch(blob)):
            raise QualificationError("Invalid frozen path or object fingerprint")
        frozen=_git("rev-parse",sha+":"+path)
        now=_git("rev-parse","HEAD:"+path)
        if frozen!=blob or now!=blob:
            raise QualificationError("Frozen source or staged source changed: "+path)
    if (freeze.get("deployment_status")!="NOT_DEPLOYED_ON_TARGET"
        or freeze.get("image_digest_pin")!="NOT_QUALIFIED"):
        raise QualificationError("Unverified staging deployment claims")
    workflow=LIVE.read_text()
    trigger=workflow.split("\non:\n",1)
    if len(trigger)!=2:
        raise QualificationError("Live workflow event trigger not found")
    config=trigger[1].split("\npermissions:",1)[0]
    if ("workflow_dispatch:" not in config or "push:" in config
        or "    needs: offline-gate" not in workflow
        or "stage5u/audit_repeatability.py" not in workflow
        or "stage5y/verify_live_amf_ingress.py" not in workflow
        or "--cycles 3 --verify-gtpu" not in workflow):
        raise QualificationError("Live gate no longer fail closed")
    return {
      "schema":"hcran-stage5zi-static-readiness-v1",
      "status":"STATIC_FREEZE_VALIDATED",
      "source_sha":sha,
      "frozen_source_blobs_verified":len(pinned),
      "experimental_cycles_verified_by_manifest":6,
      "target_inventory":"NOT_COLLECTED",
      "node_pressure":"NOT_COLLECTED",
      "security_and_image_digest_pinning":"NOT_QUALIFIED",
      "target_staging_authorized":False,
      "production_authorized":False,
      "production_decision":"NO_GO",
    }

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest",type=pathlib.Path,default=MANIFEST)
    ap.add_argument("--report",type=pathlib.Path,required=True)
    args=ap.parse_args()
    try:
        manifest=json.loads(args.manifest.read_text())
        report=verify(manifest)
    except (OSError,ValueError,QualificationError) as exc:
        print("FAIL CLOSED Stage5ZI: "+str(exc),file=sys.stderr)
        return 2
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,sort_keys=True,indent=2)+"\n")
    print(json.dumps(report,sort_keys=True))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
