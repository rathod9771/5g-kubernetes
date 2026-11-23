#!/usr/bin/env python3
"""Disposable *real socket* ZMQ REQ/REP sample-transport smoke test.

This Python model follows the observed srsRAN channel-level wire design:
gNB DL TX = REP, gNB UL RX = REQ, cf_t-like complex float32 IQ bytes.
Neither srsRAN nor OAI UE binaries run here. This is NOT UE interoperability,
NR waveform, RRC/NAS, or conformance evidence.
"""
import hashlib
import json
import math
import struct
import threading
import time
import zmq

FRAMES = 12
SAMPLES = 256
SOCKET_TIMEOUT_MS = 2500
CELLS = ("macro", "small")


def iq(sequence, cell, direction):
    # Representative little-endian float32 I,Q, NOT a proven cross-ABI format.
    offset = 0 if cell == "macro" else 100
    offset += 0 if direction == "dl" else 10
    return b"".join(struct.pack("<ff", (offset + sequence + i) / 1000,
                                -(offset + sequence + i) / 1000)
                    for i in range(SAMPLES))


def run_cell(ctx, cell):
    # A real pinned srsRAN TX binds REP; UE RX polls with REQ. UL is inverse.
    gnb_dl = ctx.socket(zmq.REP)
    gnb_ul = ctx.socket(zmq.REQ)
    ue_dl = ctx.socket(zmq.REQ)
    ue_ul = ctx.socket(zmq.REP)
    sockets = (gnb_dl, gnb_ul, ue_dl, ue_ul)
    errors = []
    counts = {"dl": 0, "ul": 0}
    try:
        for s in sockets:
            s.setsockopt(zmq.LINGER, 0)
            s.setsockopt(zmq.RCVTIMEO, SOCKET_TIMEOUT_MS)
            s.setsockopt(zmq.SNDTIMEO, SOCKET_TIMEOUT_MS)
        dl_port = gnb_dl.bind_to_random_port("tcp://127.0.0.1")
        ul_port = ue_ul.bind_to_random_port("tcp://127.0.0.1")
        if dl_port == ul_port:
            raise ValueError("Non-independent UL/DL sockets")
        ue_dl.connect(f"tcp://127.0.0.1:{dl_port}")
        gnb_ul.connect(f"tcp://127.0.0.1:{ul_port}")

        def serve():
            try:
                for i in range(FRAMES):
                    request = gnb_dl.recv()
                    if request != b"\x01":
                        raise ValueError("DL request malformed")
                    gnb_dl.send(iq(i, cell, "dl"))
                    request = ue_ul.recv()
                    if request != b"\x01":
                        raise ValueError("UL request malformed")
                    ue_ul.send(iq(i, cell, "ul"))
            except Exception as exc:
                errors.append(type(exc).__name__ + ": " + str(exc))

        server = threading.Thread(target=serve, daemon=True)
        server.start()
        dl_hash = hashlib.sha256()
        ul_hash = hashlib.sha256()
        for i in range(FRAMES):
            ue_dl.send(b"\x01")
            payload = ue_dl.recv()
            if len(payload) != SAMPLES * 8 or payload != iq(i, cell, "dl"):
                raise ValueError("Corrupted or truncated DL IQ frame")
            dl_hash.update(payload)
            counts["dl"] += 1
            gnb_ul.send(b"\x01")
            payload = gnb_ul.recv()
            if len(payload) != SAMPLES * 8 or payload != iq(i, cell, "ul"):
                raise ValueError("Corrupted or truncated UL IQ frame")
            ul_hash.update(payload)
            counts["ul"] += 1
        server.join(timeout=3)
        if server.is_alive() or errors or counts != {"dl": FRAMES, "ul": FRAMES}:
            raise ValueError(f"ZMQ server did not finish: {errors}")
        return {"cell": cell, "ul_frames": counts["ul"], "dl_frames": counts["dl"],
                "sample_bytes_per_frame": SAMPLES * 8, "dl_sha256": dl_hash.hexdigest(),
                "ul_sha256": ul_hash.hexdigest(),
                "zmq_socket_roles": "gnb_DL_REP_to_UE_REQ;UE_UL_REP_to_gnb_REQ"}
    finally:
        for s in sockets:
            s.close(linger=0)


def smoke():
    ctx = zmq.Context()
    started = time.monotonic()
    try:
        results = [run_cell(ctx, cell) for cell in CELLS]
        if results[0]["dl_sha256"] == results[1]["dl_sha256"]:
            raise ValueError("Cell samples not independent")
        return {"schema": "hcransrs-zmq-real-socket-smoke-v1",
                "status": "INDEPENDENT_PYTHON_ZMQ_PEER_TRANSPORT_PASS",
                "network": "127.0.0.1 only; real TCP sockets",
                "cells": results, "elapsed_seconds": round(time.monotonic()-started, 3),
                "limitations": ["No real srsRAN binary in this experiment",
                                "No real OAI UE binary or IQ ABI verification",
                                "No NR timing/synchronization, RRC, NAS, PDU, GTP-U, or mobility"],
                "h2_ue_interoperability": "NOT_VALIDATED", "production_decision": "NO_GO",
                "hp_cluster_touched": False}
    finally:
        ctx.term()


if __name__ == "__main__":
    import argparse
    from pathlib import Path
    p=argparse.ArgumentParser()
    p.add_argument("--output",required=True)
    args=p.parse_args()
    receipt=smoke()
    Path(args.output).write_text(json.dumps(receipt,indent=2,sort_keys=True)+"\n")
    print(json.dumps({"status":receipt["status"],
                      "h2":receipt["h2_ue_interoperability"],
                      "production_decision":"NO_GO"}))
