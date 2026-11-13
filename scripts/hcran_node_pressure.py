#!/usr/bin/env python3
"""Read-only local Linux PSI snapshot for H-CRAN staging preflight.

Run once per actual node carrying a RAN pod, with --node set to Kubernetes
.spec.nodeName. This reads /proc only and never contacts a network or cluster.
It does not assess any 3GPP procedure or authorize deployment.
"""
import argparse
import json
import os
import pathlib
import sys


def some_avg10(path):
    text = pathlib.Path(path).read_text(encoding="utf-8")
    for line in text.splitlines():
        if not line.startswith("some "):
            continue
        fields = dict(pair.split("=", 1) for pair in line.split()[1:] if "=" in pair)
        value = float(fields["avg10"])
        if not 0 <= value <= 100:
            raise ValueError("Invalid PSI avg10 range")
        return value
    raise ValueError("PSI 'some' line unavailable: " + path)


def collect(node):
    if not node or node.isspace():
        raise ValueError("Provide the exact Kubernetes node name")
    count = os.cpu_count()
    if not count or count < 1:
        raise ValueError("Cannot determine CPU count")
    load = os.getloadavg()[0]
    return {"nodes": [{
        "node": node,
        "cpu_some_avg10": some_avg10("/proc/pressure/cpu"),
        "io_some_avg10": some_avg10("/proc/pressure/io"),
        "load_per_cpu": round(load / count, 5),
    }]}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--node", required=True)
    ap.add_argument("--output", type=pathlib.Path, required=True)
    args = ap.parse_args(argv)
    result = collect(args.node)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
