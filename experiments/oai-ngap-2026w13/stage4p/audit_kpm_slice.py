#!/usr/bin/env python3
"""Audit and narrowly adapt the disposable KPM xApp's hardcoded S-NSSAI filter.

H-CRAN uses SST=1, SD=0x010203. OAI's reference xApp documents 1/0xffffff;
this is a build-time monitor filter, *not* a change to the canonical H-CRAN
CU/DU/core/UE configuration. Only the pinned source of xapp_kpm_moni.c may
be modified, and all changes are recorded for independent review.
"""
import json
import pathlib
import re
import sys

def main():
    if len(sys.argv)!=3:
        raise SystemExit("usage: audit_kpm_slice.py XAPP_C_PATH AUDIT_JSON_PATH")
    source=pathlib.Path(sys.argv[1]); report=pathlib.Path(sys.argv[2])
    raw=source.read_text(encoding="utf-8")
    if "S_NSSAI" not in raw or "filter_predicate" not in raw:
        raise RuntimeError("Pinned KPM slice-filter source no longer recognized")
    matches=[{"line":n+1,"text":line.strip()[:250]} for n,line in
             enumerate(raw.splitlines()) if any(term in line for term in
                 ("S_NSSAI","0xffffff","0xFFFFFF","filter_predicate","test_cond_value"))]
    if len(matches)>50:
        raise RuntimeError("Unexpected breadth of KPM source diagnostics")
    expr=re.compile(r"\b0[xX][fF]{6}\b")
    real_lines=[line for line in raw.splitlines() if expr.search(line) and not
                line.lstrip().startswith(("//","/*","*"))]
    result={"hcran_requested_slice":{"sst":1,"sd":"010203"},
            "pinned_kpm_filter_lines":matches,
            "non_comment_ffffff_lines":len(real_lines),
            "patched_only_xapp":False}
    if real_lines:
        # Strict: do not replace references throughout FlexRIC or RAN.
        # Adapt only a literal within the pinned standalone KPM monitor.
        modified=expr.sub("0x010203",raw)
        if modified==raw:raise RuntimeError("Internal KPM patch mismatch")
        source.write_text(modified,encoding="utf-8")
        result["patched_only_xapp"]=True
        result["replace_count"]=len(expr.findall(raw))
        print("KPM xApp filter adapted from SD ffffff to 010203 in disposable source")
    else:
        print("KPM xApp uses no explicit ffffff SD literal; monitoring filter kept unchanged")
    report.parent.mkdir(parents=True,exist_ok=True)
    report.write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")

if __name__=="__main__":
    main()
