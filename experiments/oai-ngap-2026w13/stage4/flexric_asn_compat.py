#!/usr/bin/env python3
"""Pinned FlexRIC NR-RRC generated-pointer compatibility adjustment.

Only touches the disposable cloned FlexRIC file. The pinned mouse07410
asn1c compiler represents certain NR-RRC optional members as pointers;
FlexRIC's legacy SQLite RRC recording code uses member access by value.
Requires exact source matches, records each modification, and fails closed
on upstream drift. No RAN, NGAP, E2 agent, UE, or production source edited.
"""
import argparse
from pathlib import Path
import sys

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("source", help="pinned FlexRIC sqlite3_wrapper.c")
    arg=ap.parse_args()
    p=Path(arg.source)
    src=p.read_text(encoding="utf-8")
    replacements=[
        ("ul_msg->message.present", "ul_msg->message->present"),
        ("ul_msg->message.choice.c1", "ul_msg->message->choice.c1"),
        ("results->measResultServingMOList.list", "results->measResultServingMOList->list"),
        ("NR_MeasResultNR_t *meas_nr = &serv_mo->measResultServingCell;",
         "NR_MeasResultNR_t *meas_nr = serv_mo->measResultServingCell;"),
    ]
    updated=src
    for before,after in replacements:
        n=updated.count(before)
        if not n:
            raise RuntimeError(f"Expected pinned FlexRIC expression absent: {before!r}")
        updated=updated.replace(before,after)
        print(f"corrected {n} occurrence(s): {before} -> {after}")
    null_old="if (!ul_msg || ul_msg->message->present"
    if null_old in updated:
        updated=updated.replace(null_old,
            "if (!ul_msg || !ul_msg->message || ul_msg->message->present")
        print("added null check for optional ul_msg->message")
    if updated==src:
        raise RuntimeError("No changes applied")
    p.write_text(updated,encoding="utf-8")

    # mouse07410 ASN.1 compiler produces extra CBOR symbols and support
    # files not modeled in this pinned FlexRIC RRC source list.
    # Prevent the generated code from referencing CBOR, and compile the
    # two additional runtime support files that are actually used.
    root = p.parents[4]
    cmake = root / "examples/xApp/c/monitor/RRC_MESSAGES/CMakeLists.txt"
    cmake_source = cmake.read_text(encoding="utf-8")
    if "-no-gen-OER" not in cmake_source or "-no-gen-CBOR" in cmake_source:
        raise RuntimeError("Pinned RRC ASN.1 compiler flags changed")
    cmake.write_text(cmake_source.replace("-no-gen-OER", "-no-gen-OER -no-gen-CBOR", 1),
                     encoding="utf-8")
    print("disabled CBOR code generation in pinned RRC grammar")

    runtime = root / "examples/xApp/c/monitor/RRC_MESSAGES/nr-rrc-17.3.0.cmake"
    runtime_source = runtime.read_text(encoding="utf-8")
    if "set(nr_rrc_source" not in runtime_source or "OPEN_TYPE_print.c" in runtime_source:
        raise RuntimeError("Pinned RRC source list changed")
    runtime.write_text(runtime_source.replace(
        "set(nr_rrc_source",
        "set(nr_rrc_source\\n    OPEN_TYPE_print.c\\n    asn_constraint_value.c", 1
    ).replace("\\n", "\n"), encoding="utf-8")
    print("included required ASN.1 OPEN_TYPE_print.c and asn_constraint_value.c")
    return 0

if __name__ == "__main__":
    sys.exit(main())
