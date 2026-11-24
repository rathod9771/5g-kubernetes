#!/usr/bin/env python3
"""Exact pinned-OAI PDSCH capability initialization repair for H-CRAN research.

No broad search/replace, no disabled assertions, no silent version upgrades.
Replicates the one-layer-until-capabilities-known default from upstream OAI
develop, which fixes the pinned build's zero-layer fallback on DCI 1_1.
Run only against a clean checkout at the exact source pin.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

PIN = "dfb296ab867050a7bde81b393fa31b757ada2c83"
MAIN_PATH = "openair2/LAYER2/NR_MAC_UE/main_ue_nr.c"
CONFIG_PATH = "openair2/LAYER2/NR_MAC_UE/config_ue.c"
DCI_PATH = "openair2/LAYER2/NR_MAC_UE/nr_ue_procedures.c"
PINNED_MAIN_BLOB = "59eadd731fcd2f10c198349f1b19a8651e255595"
OLD = "  mac->uecap_maxMIMO_PDSCH_layers = 0;"
NEW = "  mac->uecap_maxMIMO_PDSCH_layers = 1;"
CAPABILITY_ASSIGNMENT = (
    "mac->uecap_maxMIMO_PDSCH_layers = "
    "fs_dl_cc->maxNumberMIMO_LayersPDSCH ? "
    "2 << *fs_dl_cc->maxNumberMIMO_LayersPDSCH : 1;"
)


class SourceMismatch(RuntimeError):
    pass


def git_blob_sha(raw):
    header = ("blob " + str(len(raw))).encode() + b"\0"
    return hashlib.sha1(header + raw).hexdigest()


def checked_text(main, config, dci, mode):
    """Fail closed if the pinned branch no longer has the diagnosed code path."""
    if "void nr_ue_init_mac(" not in main or "  mac->p_Max = INT_MIN;" not in main:
        raise SourceMismatch("MAC initializer structure changed")
    init = main[main.index("void nr_ue_init_mac("):main.index("  mac->p_Max = INT_MIN;",main.index("void nr_ue_init_mac("))]
    source_line = OLD if mode in ("check", "apply") else NEW
    if init.count(source_line) != 1 or main.count(source_line) != 1:
        raise SourceMismatch("Expected PDSCH initial assignment absent or ambiguous")
    other = NEW if source_line == OLD else OLD
    if other in init:
        raise SourceMismatch("Mixed old and new PDSCH assignments")
    if CAPABILITY_ASSIGNMENT not in config:
        raise SourceMismatch("Baseline one-layer capability handling changed")
    for required in (
        "if (sc_info->maxMIMO_Layers_PDSCH)",
        "max_mimo_layers = *sc_info->maxMIMO_Layers_PDSCH;",
        "max_mimo_layers = mac->uecap_maxMIMO_PDSCH_layers;",
        "AssertFatal(max_mimo_layers > 0,",
    ):
        if required not in dci:
            raise SourceMismatch("DCI 1_1 fallback/assertion contract changed")
    if mode == "verify":
        return main
    return main.replace(OLD, NEW, 1)


def git(repo, *args):
    cp = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                        text=True, timeout=15, check=True)
    return cp.stdout.strip()


def run(repo, mode):
    repo = Path(repo).resolve()
    if not repo.is_dir() or repo.is_symlink():
        raise SourceMismatch("Missing regular pinned OAI checkout")
    if git(repo, "rev-parse", "HEAD") != PIN:
        raise SourceMismatch("Unapproved OAI source commit")
    paths = (MAIN_PATH, CONFIG_PATH, DCI_PATH)
    for name in paths:
        f = repo / name
        if not f.is_file() or f.is_symlink():
            raise SourceMismatch("Pinned OAI source missing or symlinked")
    main_raw = (repo / MAIN_PATH).read_bytes()
    main = main_raw.decode("utf-8")
    config = (repo / CONFIG_PATH).read_text()
    dci = (repo / DCI_PATH).read_text()
    wanted = checked_text(main, config, dci, mode)
    changed = git(repo, "diff", "--name-only", "--")
    if mode in ("check", "apply"):
        if changed:
            raise SourceMismatch("Source checkout must start clean")
        if git_blob_sha(main_raw) != PINNED_MAIN_BLOB:
            raise SourceMismatch("OAI C source blob differs from audited pinned revision")
        if mode == "apply":
            (repo / MAIN_PATH).write_text(wanted)
    else:
        if changed != MAIN_PATH:
            raise SourceMismatch("Unexpected files modified by research patch")
        expected = main.replace(NEW, OLD, 1).encode()
        if git_blob_sha(expected) != PINNED_MAIN_BLOB:
            raise SourceMismatch("OAI patch is not exact one-line pinned-source change")
    if mode != "check":
        stats = git(repo, "diff", "--numstat", "--")
        if stats != "1\t1\t" + MAIN_PATH:
            raise SourceMismatch("Patch touched more than one initializer line")
        git(repo, "diff", "--check")
    return {"result": "PINNED_SINGLE_LAYER_CAPABILITY_DEFAULT_" + mode.upper() + "_PASS",
            "oai_source_commit": PIN, "pinned_blob": PINNED_MAIN_BLOB,
            "changed_file": MAIN_PATH if mode != "check" else None,
            "expected_dci_assertion_preserved": True,
            "ue_protocol_runtime_proven": False, "production_decision": "NO_GO"}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--checkout", required=True)
    p.add_argument("--mode", choices=("check", "apply", "verify"), required=True)
    a = p.parse_args()
    print(json.dumps(run(a.checkout, a.mode), sort_keys=True))
