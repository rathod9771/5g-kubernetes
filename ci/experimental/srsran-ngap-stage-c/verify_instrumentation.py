"""Review/CI checks; flag checks are NOT evidence of execution without the later native logs."""
import json
import shlex
import sys
from pathlib import Path

MODES = {"address", "undefined", "thread"}
REQUIRED = (
    "lib/cu_cp/cu_cp_controller/amf_connection_manager.cpp",
    "lib/support/executors/task_worker.cpp",
    "lib/cu_cp/routines/amf_connection_setup_routine.cpp",
    "lib/cu_cp/routines/amf_connection_removal_routine.cpp",
    "ci/experimental/srsran-ngap/snapshot_component.cpp",
    "ci/experimental/srsran-ngap-stage-c/concurrency_component.cpp",
)
BINARIES = (
    "tests/unittests/cu_cp/cu_cp_test",
    "l3-stage-b-tests/l3_ngap_snapshot_component",
    "l3-stage-c-tests/l3_ngap_snapshot_concurrency",
)


def check_flags(tokens, mode):
    if mode not in MODES:
        raise ValueError("Unknown sanitizer mode")
    modes = set()
    for token in tokens:
        if token.startswith("-fsanitize="):
            modes.update(token.split("=", 1)[1].split(","))
        if token.startswith("-fno-sanitize=") and token != "-fno-sanitize=vptr":
            raise ValueError("Unexpected sanitizer disable flag")
    if modes != {mode}:
        raise ValueError("Missing or incompatible sanitizer flags")


def verify_database(entries, commands, mode):
    found = set()
    # Check every CU-CP and NGAP entry, plus both snapshot component translation units.
    for entry in entries:
        name = entry["file"].replace("\\", "/")
        relevant = "/lib/cu_cp/" in name or "/lib/ngap/" in name or "/lib/support/" in name or "/lib/srslog/" in name or any(name.endswith(p) for p in REQUIRED)
        if relevant:
            check_flags(entry.get("arguments") or shlex.split(entry["command"]), mode)
            for required in REQUIRED:
                if name.endswith(required): found.add(required)
    if found != set(REQUIRED):
        raise ValueError("Missing required instrumented source entry")
    for binary in BINARIES:
        matching = []
        for line in commands.splitlines():
            tokens = shlex.split(line)
            if "-o" in tokens and tokens[tokens.index("-o") + 1] == binary and "-c" not in tokens:
                matching.append(tokens)
        if len(matching) != 1:
            raise ValueError("Missing or ambiguous executable link command: " + binary)
        check_flags(matching[0], mode)
    return {"mode": mode, "required_sources": sorted(found), "link_targets": list(BINARIES)}


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit("usage: verify_instrumentation.py MODE compile_commands.json build-commands.txt")
    print(json.dumps(verify_database(json.loads(Path(sys.argv[2]).read_text()), Path(sys.argv[3]).read_text(), sys.argv[1]), indent=2))
