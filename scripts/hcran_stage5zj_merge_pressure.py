#!/usr/bin/env python3
"""Stage 5ZJ: locally combine bounded per-node PSI observations for S1 preflight.

No network, kubectl, OSM or cluster mutation. Only reads files produced by
hcran_node_pressure.py on actual hosting nodes. This does not approve staging.
The explicit --expect-node list must be transcribed from a separately authorized,
read-only target inventory; this tool cannot verify where samples originated.
"""
import argparse
import datetime as dt
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile

MAX_NODES = 32
MAX_INPUT_BYTES = 16384
REQUIRED = {"node", "collected_utc", "cpu_some_avg10", "io_some_avg10", "load_per_cpu"}
NODE_RE = re.compile(r"^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$", re.ASCII)


class PressureError(ValueError):
    pass


def validate_node(name):
    if not isinstance(name, str) or len(name) > 253 or not NODE_RE.fullmatch(name) or ".." in name:
        raise PressureError("Invalid Kubernetes node identifier")
    for label in name.split("."):
        if len(label) > 63:
            raise PressureError("Node DNS label too long")
    return name


def validate_observation(value, now):
    if not isinstance(value, dict) or set(value) != REQUIRED:
        raise PressureError("Pressure observation must contain only the five approved fields")
    name = validate_node(value["node"])
    try:
        stamp = dt.datetime.fromisoformat(value["collected_utc"].replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError, OverflowError) as exc:
        raise PressureError("Malformed or missing UTC pressure timestamp") from exc
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise PressureError("Pressure timestamp must be timezone-aware")
    offset = (now - stamp.astimezone(dt.timezone.utc)).total_seconds()
    if not -30 <= offset <= 300:
        raise PressureError("Pressure timestamp is stale or more than 30 seconds in the future")
    for field in ("cpu_some_avg10", "io_some_avg10", "load_per_cpu"):
        val = value[field]
        if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val):
            raise PressureError("Non-finite or non-numeric pressure metric: " + field)
        maximum = 100 if field != "load_per_cpu" else 1_000_000
        if not 0 <= val <= maximum:
            raise PressureError("Out-of-range pressure metric: " + field)
    return {"node": name, **{field: value[field] for field in REQUIRED if field != "node"}}


def combine(sources, expected_nodes, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    if not sources or len(sources) > MAX_NODES:
        raise PressureError("Expected 1–32 node pressure files")
    if not expected_nodes or len(expected_nodes) > MAX_NODES:
        raise PressureError("At least one and no more than 32 expected nodes required")
    checked = [validate_node(n) for n in expected_nodes]
    if len(checked) != len(set(checked)):
        raise PressureError("Duplicate expected node")
    found = {}
    for path in sources:
        path = Path(path)
        if not path.is_file() or path.stat().st_size > MAX_INPUT_BYTES:
            raise PressureError("Input missing or exceeds 16 KiB: " + path.name)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise PressureError("Invalid pressure JSON input: " + path.name) from exc
        if not isinstance(raw, dict) or set(raw) != {"nodes"} or not isinstance(raw["nodes"], list) or len(raw["nodes"]) != 1:
            raise PressureError("Each input must contain exactly one local node sample")
        sample = validate_observation(raw["nodes"][0], now)
        name = sample["node"]
        if name in found:
            raise PressureError("Duplicate node evidence: " + name)
        found[name] = sample
    if set(checked) != set(found):
        raise PressureError("Expected node coverage mismatch; missing=" + ",".join(sorted(set(checked) - set(found))) +
                            " unexpected=" + ",".join(sorted(set(found) - set(checked))))
    return {"nodes": [found[name] for name in sorted(found)]}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", action="append", type=Path, required=True,
                    help="One local hcran_node_pressure.py JSON per hosting node; repeat")
    ap.add_argument("--expect-node", action="append", required=True,
                    help="One exact node from separately authorized read-only target inventory; repeat")
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args(argv)
    try:
        data = combine(args.input, args.expect_node)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=args.output.parent,
                                         prefix=".stage5zj-pressure-", delete=False) as tmp:
            tmpname = tmp.name
            json.dump(data, tmp, sort_keys=True, indent=2, allow_nan=False)
            tmp.write("\n")
        try:
            os.chmod(tmpname, 0o600)
            os.replace(tmpname, args.output)
        finally:
            if os.path.exists(tmpname):
                os.unlink(tmpname)
    except (PressureError, OSError) as exc:
        print("FAIL CLOSED Stage5ZJ: " + str(exc), file=sys.stderr)
        return 2
    print(json.dumps({"status": "PRESSURE_EVIDENCE_COMBINED_ONLY",
                      "nodes": len(data["nodes"]), "production_decision": "NO_GO",
                      "staging_authorized": False, "target_cluster_touched": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
