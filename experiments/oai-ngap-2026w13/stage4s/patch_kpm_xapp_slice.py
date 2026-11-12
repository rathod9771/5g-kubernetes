#!/usr/bin/env python3
"""Encode H-CRAN test slice 1/010203 in the pinned FlexRIC KPM monitor xApp.

The stock pinned xApp creates a one-byte S-NSSAI test value containing SST
only. The OAI 2026.w13 KPM agent interprets this as (SST, SD=0xffffff).
H-CRAN UEs use SD=0x010203, so no UE passes the stock KPM filter.

OAI ran_func_kpm.c:174-207 supports FOUR octets [SST, SD1, SD2, SD3].
This patch updates ONLY the disposable runner's xapp_kpm_moni.c action
predicate to encode 0x01 0x01 0x02 0x03. Never alters the CU agent source,
production charts, Open5GS subscriber or deployed H-CRAN settings.
"""
import argparse
from pathlib import Path
import sys

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--source",required=True)
    p.add_argument("--out",required=True)
    a=p.parse_args()
    file=Path(a.source).resolve()
    if not str(file).endswith("examples/xApp/c/monitor/xapp_kpm_moni.c"):
        raise ValueError("Unexpected source; refusing broad replacement")
    original=file.read_text(encoding="utf-8")
    one="const size_t len_nssai = 1;"
    setting="dst.test_cond_value->octet_string_value->buf[0] = value;"
    if original.count(one)!=1 or original.count(setting)!=1:
        raise RuntimeError(
            "Pinned FlexRIC KPM xApp source changed: expected one SST-only "
            f"length assignment and one buf[0] write; saw {original.count(one)}, "
            f"{original.count(setting)}")
    updated=original.replace(one,"const size_t len_nssai = 4;",1)
    updated=updated.replace(setting,setting+"""
   // 3GPP S-NSSAI: SST=1 followed by 24-bit SD 0x010203.
   dst.test_cond_value->octet_string_value->buf[1] = 0x01;
   dst.test_cond_value->octet_string_value->buf[2] = 0x02;
   dst.test_cond_value->octet_string_value->buf[3] = 0x03;""",1)
    if updated==original:
        raise RuntimeError("KPM xApp condition not modified")
    file.write_text(updated,encoding="utf-8")
    msg=("Changed ONLY disposable pinned FlexRIC monitor xApp predicate:\n"
         "S-NSSAI OCTET_STRING len 1 -> 4; payload [01 01 02 03]\n"
         "Expected OAI condition: SST=1, SD=010203\n"
         "No UE or agent code changed. Actual KPM indications still mandatory.\n")
    Path(a.out).write_text(msg,encoding="utf-8")
    print(msg)
    return 0

if __name__=="__main__":
    sys.exit(main())
