#!/usr/bin/env python3
"""Test-only race-safe AMF process counter for Stage5Y disposable RFsim.

The frozen Stage4P counter concatenates docker-exec stdout and stderr before
int(), so a disappearing /proc/<pid>/comm can turn a normal AMF restart
into a ValueError. This override ONLY fixes that observation race; recovery,
NAS, NGAP, GTP-U and per-cell ingress gates remain unchanged.
"""
import re
import subprocess

# A disappearing /proc entry is expected during AMF stop/start. Redirect the
# cat error and test its status in an if statement, even under sh -e.
# Only the final numeric counter is written to stdout.
COUNT_SCRIPT = """n=0
for f in /proc/[0-9]*/comm; do
  if comm=$(cat "$f" 2>/dev/null); then
    if [ "$comm" = "open5gs-amfd" ]; then
      n=$((n+1))
    fi
  fi
done
printf '%s\\n' "$n"
"""


def count_amf_processes(core_container, *, run=None):
    """Return a strict numeric AMF count; do not accept mixed/noisy output."""
    runner = run if run is not None else subprocess.run
    proc = runner(
        ["docker", "exec", core_container, "/bin/sh", "-ec", COUNT_SCRIPT],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=20,
    )
    count_text = proc.stdout.removesuffix(chr(10))
    if proc.returncode != 0 or proc.stderr or re.fullmatch("[0-9]+", count_text) is None:
        raise RuntimeError("Stage5Y AMF process-count observation invalid")
    return int(count_text)
