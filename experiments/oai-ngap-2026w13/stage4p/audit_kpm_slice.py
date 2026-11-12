#!/usr/bin/env python3
"""Audit and narrowly adapt the pinned KPM xApp to H-CRAN's SST/SD.

OAI 2026.w13 CU's nssai_matches() requires SD=0xffffff when the KPM xApp
supplies only one octet (SST). The canonical H-CRAN test PDU uses
SST=1, SD=0x010203, so a one-byte filter selects zero UEs and generates
no KPM indications. Encode complete four-byte S-NSSAI (01 01 02 03) in
the *disposable xApp source only*, keeping the RAN/5GC untouched.
Fail closed on source drift; preserve exact audit and source diff.
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
    required=("S_NSSAI_TEST_COND_TYPE","OCTET_STRING_TEST_COND_VALUE",
              "filter_predicate","len_nssai")
    if not all(t in raw for t in required):
        raise RuntimeError("Pinned KPM S-NSSAI filter structure has changed")

    before_len=re.compile(r"(\blen_nssai\s*=\s*)1(\s*;)")
    len_hits=len(before_len.findall(raw))
    assign="dst.test_cond_value->octet_string_value->buf[0] = value;"
    if len_hits!=1 or raw.count(assign)!=1:
        raise RuntimeError(
            f"Unsafe pinned xApp source drift: len_nssai=1 hits {len_hits}, "
            f"SST assignment hits {raw.count(assign)}")
    modified=before_len.sub(r"\g<1>4\2",raw,count=1)
    modified=modified.replace(assign,
        assign+"\n"
        "  dst.test_cond_value->octet_string_value->buf[1] = 0x01;\n"
        "  dst.test_cond_value->octet_string_value->buf[2] = 0x02;\n"
        "  dst.test_cond_value->octet_string_value->buf[3] = 0x03;",1)
    if modified==raw or "len_nssai = 4;" not in modified:
        raise RuntimeError("Failed to encode four-octet H-CRAN S-NSSAI")
    # Require existing allocation and size to inherit the updated variable,
    # so no one-byte allocation is left under a four-byte write.
    if modified.count("octet_string_value->len = len_nssai;")!=1:
        raise RuntimeError("Expected OCTET_STRING length source changed")
    if modified.count("calloc(len_nssai, sizeof(uint8_t))")!=1:
        raise RuntimeError("Expected OCTET_STRING allocation source changed")
    source.write_text(modified,encoding="utf-8")
    report.parent.mkdir(parents=True,exist_ok=True)
    report.write_text(json.dumps({
        "source":"Pinned FlexRIC xapp_kpm_moni.c, experiment only",
        "canonical_hcran_s_nssai":{"sst":1,"sd":"010203"},
        "original_filter":{"bytes":"01","length":1,
                           "oai_cu_interpreted_sd":"ffffff"},
        "patched_filter":{"bytes":"01010203","length":4,
                          "oai_cu_interpreted_sd":"010203"},
        "changed_expression_count":2,
        "ran_or_subscriber_config_changed":False,
        "production_decision":"NO_GO",
    },indent=2)+"\n",encoding="utf-8")
    print("PASS: disposable KPM xApp selects canonical H-CRAN NSSAI 01:010203; no RAN changes")

if __name__=="__main__":
    main()
