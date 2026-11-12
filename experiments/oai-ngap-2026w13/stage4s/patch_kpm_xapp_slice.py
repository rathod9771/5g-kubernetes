#!/usr/bin/env python3
"""Fail-closed pinned FlexRIC KPM xApp NSSAI mismatch correction.

The built-in KPM monitor requests S-NSSAI SST=1, SD=0xffffff, per OAI
2026.w13 E2AP README. H-CRAN OAI synthetic UEs actually use SST=1,
SD=0x010203. An accepted subscription can therefore produce ZERO
indications. This script updates ONLY the disposable pinned xApp example
source on a GitHub Actions runner, never the agent/SM or production charts.
"""
from pathlib import Path
import argparse
import re
import sys

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--source",required=True)
    p.add_argument("--out",required=True)
    a=p.parse_args()
    source=Path(a.source).resolve()
    expected="examples/xApp/c/monitor/xapp_kpm_moni.c"
    if not str(source).endswith(expected):
        raise ValueError("Unexpected target file; refuse broad source modification")
    text=source.read_text(encoding="utf-8")
    old=re.compile(r"0[xX][fF]{6}\b")
    hits=list(old.finditer(text))
    if len(hits)!=1:
        raise RuntimeError("Expected exactly one KPM xApp 0xffffff slice SD, "
                           f"found {len(hits)}; stop rather than altering arbitrary data")
    # Keep the exact SST and all KPM reporting/subscription behavior.
    updated=old.sub("0x010203",text,count=1)
    if updated==text:
        raise RuntimeError("No slice SD modification applied")
    source.write_text(updated,encoding="utf-8")
    log=(f"PINNED KPM xApp only: {expected}\n"
         f"OLD requested slice: SST=1 SD=0xffffff\n"
         f"NEW requested slice: SST=1 SD=0x010203\n"
         f"exact 0xffffff occurrences before=1 after={len(old.findall(updated))}\n"
         "This is not evidence of KPM indications; runtime callbacks must prove them.\n")
    Path(a.out).write_text(log,encoding="utf-8")
    print(log)
    return 0

if __name__=="__main__":
    sys.exit(main())
