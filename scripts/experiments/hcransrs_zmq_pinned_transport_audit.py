#!/usr/bin/env python3
"""GET-only, exact-blob srsRAN ZMQ transport audit; no UE, RF, or deployment.

Pinned srsRAN channel source shows a ZMQ REQ/REP handshake. OCUDU-OAI
example documentation alone does not prove the candidate UE's transport
implements the reciprocal handshake or compatible complex IQ sample ABI.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import urllib.request

UPSTREAM="herlesupreeth/srsRAN_Project"
COMMIT="11c9bbabb69873752500d676f55e0034f6caa5c5"
BLOBS={
 "lib/radio/zmq/radio_zmq_tx_channel.cpp":"ee29e8696d0ccd7258655fb96e108ae92813b2fc",
 "lib/radio/zmq/radio_zmq_rx_channel.cpp":"e95f4ff4ac687bb4daab8c0de5520c95e0ff8b75",
 "lib/radio/zmq/radio_config_zmq_validator.cpp":"9db0ccc096ebec7f5f05047f33dc210bde435246",
}
ANCHORS={
 "lib/radio/zmq/radio_zmq_tx_channel.cpp":("VALID_SOCKET_TYPES = {ZMQ_REP}", "zmq_bind(", "zmq_recv(", "zmq_send(", "count * sizeof(cf_t)"),
 "lib/radio/zmq/radio_zmq_rx_channel.cpp":("VALID_SOCKET_TYPES = {ZMQ_REQ}", "zmq_connect(", "zmq_send(", "zmq_recv(", "sizeof(cf_t)"),
 "lib/radio/zmq/radio_config_zmq_validator.cpp":("Only default OTW format is currently supported", "Discontinuous transmission modes are not supported"),
}

def examine(sources):
    if set(sources)!=set(BLOBS):
        raise ValueError("Pinned ZMQ source file set incomplete")
    for path, checks in ANCHORS.items():
        data=sources[path]
        if not isinstance(data,str) or any(anchor not in data for anchor in checks):
            raise ValueError("Upstream radio ABI behavior changed or missing: "+path)
    return {
      "schema":"hcransrs-pinned-zmq-handshake-v1", "upstream_repository":UPSTREAM,
      "upstream_commit":COMMIT, "blob_shas":BLOBS,
      "verified_srsran_behavior":{"downlink_sender":"ZMQ_REP bind; awaits 1-byte REQ; replies with binary cf_t buffer",
                                 "uplink_receiver":"ZMQ_REQ connect; sends 1-byte request; receives cf_t binary buffer",
                                 "sample_format":"native sizeof(cf_t), binary complex float samples",
                                 "clocking":"srsRAN async buffer scheduling; peer timing unverified"},
      "oai_ocudu_document":"SystronLab/OCUDU-ZMQ@c5f4115f36203d48b8f56ff03d87448649036f61",
      "unverified":["OAI UE plugin reciprocal REQ/REP transport",
                    "binary complex sample ABI and endpoint direction",
                    "radio rates and independent cell/UE timing",
                    "runtime NR SIB1/RRC, NAS, GTP-U and mobility"],
      "decision":"ZMQ_HANDSHAKE_SOURCE_OBSERVED_INTEROPERABILITY_BLOCKED",
      "h2":"BLOCKED", "h3_h4_h5_h6":"NOT_RUN",
      "hp_cluster_touched":False,"production_decision":"NO_GO"}

def get_blob(path,sha):
    token=os.environ.get("GH_TOKEN")
    if not token:raise ValueError("Missing read-only GitHub access")
    url=f"https://api.github.com/repos/{UPSTREAM}/git/blobs/{sha}"
    req=urllib.request.Request(url,headers={"Authorization":"Bearer "+token,
         "Accept":"application/vnd.github+json","User-Agent":"hcransrs-zmq-source-audit"})
    with urllib.request.urlopen(req,timeout=20) as response:meta=json.load(response)
    if meta.get("sha")!=sha or meta.get("encoding")!="base64":raise ValueError("Pinned blob mismatch")
    raw=base64.b64decode(meta["content"],validate=False)
    if len(raw)>256*1024 or hashlib.sha1(b"blob "+str(len(raw)).encode()+b"\0"+raw).hexdigest()!=sha:
        raise ValueError("Git blob integrity mismatch")
    return raw.decode("utf-8")

def main():
    p=argparse.ArgumentParser();p.add_argument("--out",required=True)
    args=p.parse_args()
    result=examine({path:get_blob(path,sha) for path,sha in BLOBS.items()})
    Path(args.out).write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    print(json.dumps({"decision":result["decision"],"production_decision":"NO_GO"},sort_keys=True))
if __name__=="__main__":main()
