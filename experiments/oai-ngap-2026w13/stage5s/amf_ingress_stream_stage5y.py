#!/usr/bin/env python3
"""Stage5Y: bounded, redacted per-cell AMF InitialUEMessage observation.

Open5GS prints "InitialUEMessage" and the corresponding
"RAN_UE_NGAP_ID[...] CellID[0xe010X]" on DIFFERENT lines. A
single-line pattern cannot establish this association. This module
matches only one unambiguous cell in the next five log lines of a
bounded AMF snapshot and publishes *only* synthetic cell identifiers.

The AMF runs inside a disposable Docker core container. Polling a
bounded tail of its test-only /tmp/stage4k-log/*amf*.log files handles
the restart-created new files. Raw lines are kept in memory for
parsing only, not persisted or printed; subscriber identifiers and
NAS payload are never emitted. Any truncated, ambiguous or missing
collection fails closed rather than proving ingress.
"""
from collections import Counter
import re
import subprocess
import time

TOKEN = "__STAGE5Y_AMF_FILE__"
MAX_SNAPSHOT_CHARS = 2 * 1024 * 1024
MAX_LINES_PER_FILE = 600
MAX_FILE_COUNT = 12
POLL_SECONDS = 1.5
INITIAL = re.compile(r"\bInitialUEMessage\b")
CELL = re.compile(r"CellID\[(0x[0-9a-f]+)\]", re.IGNORECASE)

def find_initial_ue_cells(lines):
    """Return only (zero-based line index, synthetic integer cell_id)."""
    found = []
    for i, line in enumerate(lines):
        if not INITIAL.search(line):
            continue
        ids = set()
        for following in lines[i + 1:i + 6]:
            if INITIAL.search(following):
                break  # never attach a later message's cell metadata
            if "RAN_UE_NGAP_ID" in following:
                ids.update(int(raw, 16) for raw in CELL.findall(following))
        if len(ids) == 1:
            found.append((i, next(iter(ids))))
    return found

def decode_bounded_snapshot(raw):
    """Return list of (file_key, total_lines, last_lines); no raw logging."""
    if not isinstance(raw, str) or len(raw) > MAX_SNAPSHOT_CHARS:
        raise ValueError("Oversized/malformed core AMF snapshot")
    files = []
    current = None
    for line in raw.splitlines():
        if line.startswith(TOKEN + "\t"):
            if current is not None:
                files.append(current)
            parts = line[len(TOKEN) + 1:].split("\t")
            if len(parts) != 2 or not parts[0].startswith("/tmp/stage4k-log/"):
                raise ValueError("Invalid AMF source header")
            count = int(parts[1])
            if count < 0:
                raise ValueError("Invalid AMF source size")
            current = [parts[0], count, []]
        elif current is not None:
            current[2].append(line)
    if current is not None:
        files.append(current)
    if len(files) > MAX_FILE_COUNT:
        raise ValueError("Unexpected number of AMF log files")
    for name, count, lines in files:
        if len(lines) > MAX_LINES_PER_FILE + 2:
            raise ValueError("AMF log snapshot exceeded line envelope")
        if count + 1 < len(lines):
            raise ValueError("AMF log lines exceed declared count")
    return files

def observe_amf_files(collector):
    """Call on a daemon thread, after generic UE/CU live observers start."""
    from run_ue_registration import name
    shell = (
        'for f in /tmp/stage4k-log/*amf*.log; do '
        'test -f "$f" || continue; '
        'printf "__STAGE5Y_AMF_FILE__\\t%s\\t%s\\n" "$f" "$(wc -l < "$f")"; '
        'tail -n 600 "$f"; '
        'done'
    )
    last_total = {}
    last_emitted = {}
    failures = 0
    missing = False
    while not collector.stopped:
        try:
            proc = subprocess.run(
                ["docker", "exec", name("core"), "/bin/sh", "-c", shell],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace",
                timeout=7,
            )
            if proc.returncode:
                failures += 1
                if failures >= 5 and not missing:
                    collector.missing_sources.append(
                        {"source": "core-amf-files", "error_type": "RepeatedSnapshotFailure"})
                    missing = True
            else:
                failures = 0
                parts = decode_bounded_snapshot(proc.stdout)
                for path, total, lines in parts:
                    first_line = max(0, total - len(lines))
                    previous_total = last_total.get(path)
                    if previous_total is not None and total < previous_total:
                        # The synthetic AMF file was truncated/recreated.
                        last_emitted.pop(path, None)
                    if (previous_total is not None and
                            previous_total < first_line and not missing):
                        collector.missing_sources.append(
                            {"source": "core-amf-files", "error_type": "SourceTailGap"})
                        missing = True
                    for rel, cell_id in find_initial_ue_cells(lines):
                        index = first_line + rel
                        if index <= last_emitted.get(path, -1):
                            continue
                        # Never persist a SUPI, IMSI, NAS payload or RAN UE id.
                        collector.add("core", "amf_initial_ue_cell",
                                      {"cell_id": cell_id})
                        last_emitted[path] = index
                    last_total[path] = total
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            failures += 1
            if failures >= 5 and not missing:
                collector.missing_sources.append(
                    {"source": "core-amf-files",
                     "error_type": type(exc).__name__})
                missing = True
        # Event sampling is independent of the radio runner. A collector
        # exception must NEVER convert a failed UE recovery into PASS.
        deadline = time.monotonic() + POLL_SECONDS
        while not collector.stopped and time.monotonic() < deadline:
            time.sleep(0.15)
