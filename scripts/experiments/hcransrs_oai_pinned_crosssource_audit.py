#!/usr/bin/env python3
"""Pinned OAI NR-UE / srsRAN Project ZeroMQ driver cross-source compatibility audit.

This is source-code evidence only. Complementary ZMQ socket roles and cf_t
message payloads are necessary but NOT sufficient to prove waveform,
sample-clock, synchronization, NR UE attach, RRC, NAS or GTP-U interop.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import urllib.request

SOURCES={
 "oai":{
  "repository":"OPENAIRINTERFACE/openairinterface5g",
  "commit":"dfb296ab867050a7bde81b393fa31b757ada2c83",
  "path":"radio/zmq/zmq_radio.cpp",
  "blob":"ec92501831936d2cb1036425deacf1e119b14fd1",
 },
 "srsran_tx":{
  "repository":"herlesupreeth/srsRAN_Project",
  "commit":"11c9bbabb69873752500d676f55e0034f6caa5c5",
  "path":"lib/radio/zmq/radio_zmq_tx_channel.cpp",
  "blob":"ee29e8696d0ccd7258655fb96e108ae92813b2fc",
 },
 "srsran_rx":{
  "repository":"herlesupreeth/srsRAN_Project",
  "commit":"11c9bbabb69873752500d676f55e0034f6caa5c5",
  "path":"lib/radio/zmq/radio_zmq_rx_channel.cpp",
  "blob":"e95f4ff4ac687bb4daab8c0de5520c95e0ff8b75",
 }
}
OAI_TX=("zmq_socket(zmq_state->context, ZMQ_REP)","zmq_bind(socket, tx_channels[i])","zmq_recv(chan->socket_, &dummy, 1, 0)","zmq_msg_send(&msg, chan->socket_, 0)")
OAI_RX=("zmq_socket(zmq_state->context, ZMQ_REQ)","zmq_connect(socket, rx_channels[i])","zmq_send(channel->socket_, &dummy, 1, 0)","zmq_recv(chan->socket_, rx_buffer, rx_buffer_size, 0)")
SRS_TX=("VALID_SOCKET_TYPES = {ZMQ_REP}","zmq_bind(sock, config.address.c_str())","zmq_recv(sock, &dummy, sizeof(dummy), ZMQ_DONTWAIT)","zmq_send(sock, (void*)buffer.data(), nbytes, 0)")
SRS_RX=("VALID_SOCKET_TYPES = {ZMQ_REQ}","zmq_connect(sock, config.address.c_str())","zmq_send(sock, &dummy, sizeof(dummy), 0)","zmq_recv(sock, (void*)buffer.data(), nbytes, ZMQ_DONTWAIT)")
PAYLOAD=("sizeof(cf_t)","num_samples_received","zmq_msg_send")

def inspect(sources):
    if set(sources)!=set(SOURCES) or not all(isinstance(sources[k],str) for k in SOURCES):
        raise ValueError("Missing complete pinned OAI and srsRAN radio source")
    oai=sources["oai"]; stx=sources["srsran_tx"]; srx=sources["srsran_rx"]
    for role,code,checks in (("oai_tx",oai,OAI_TX),("oai_rx",oai,OAI_RX),
                              ("srsran_tx",stx,SRS_TX),("srsran_rx",srx,SRS_RX)):
        if any(item not in code for item in checks):
            raise ValueError("Missing source anchor for "+role)
    if "const size_t sample_size = sizeof(cf_t);" not in oai or "count * sizeof(cf_t)" not in stx or "int sample_size = sizeof(cf_t);" not in srx:
        raise ValueError("Complex sample payload layout anchors unavailable")
    return {
      "schema":"hcran-srsran-oai-pinned-zmq-crosssource-v1",
      "status":"SOURCE_SOCKET_ROLES_AND_PAYLOAD_FRAMING_COMPATIBLE",
      "source_pins":SOURCES,
      "socket_proof":{
        "oai_tx":"REP bind","oai_rx":"REQ connect",
        "srsran_tx":"REP bind","srsran_rx":"REQ connect",
        "cross_link_dl":"srsran TX REP bind -> OAI RX REQ connect",
        "cross_link_ul":"OAI TX REP bind -> srsran RX REQ connect",
        "request_reply":"one-byte request and cf_t sample response",
      },
      "not_validated":["Exact floating point cf_t ABI across both builds",
          "Matched sampling rates, carrier and timing on an actual dual-DU deployment",
          "NR SSB/SIB1 synchronization and random access",
          "RRC, NAS, PDU session, F1-U/NG-U traffic and mobility"],
      "actual_oai_ue_connected":False,"actual_srsran_du_connected":False,
      "protocol_validation":"NOT_RUN","production_decision":"NO_GO",
      "hp_cluster_touched":False,
    }

def get_blob(meta):
    token=os.environ.get("GH_TOKEN")
    if not token:raise ValueError("GitHub token missing")
    repo=meta["repository"]; sha=meta["blob"]
    req=urllib.request.Request(f"https://api.github.com/repos/{repo}/git/blobs/{sha}",
        headers={"Authorization":"Bearer "+token,"Accept":"application/vnd.github+json",
                 "User-Agent":"hcran-crosssource-interoperability-audit"})
    with urllib.request.urlopen(req,timeout=20) as rsp:
        doc=json.load(rsp)
    if doc.get("sha")!=sha or doc.get("encoding")!="base64":
        raise ValueError("GitHub pinned blob metadata mismatch")
    raw=base64.b64decode(doc["content"])
    if len(raw)>256*1024 or hashlib.sha1(b"blob "+str(len(raw)).encode()+b"\0"+raw).hexdigest()!=sha:
        raise ValueError("Upstream source blob integrity mismatch")
    return raw.decode("utf-8")

def main():
    p=argparse.ArgumentParser();p.add_argument("--output",required=True)
    a=p.parse_args()
    receipt=inspect({name:get_blob(meta) for name,meta in SOURCES.items()})
    Path(a.output).write_text(json.dumps(receipt,indent=2,sort_keys=True)+"\n")
    print(json.dumps({"status":receipt["status"],"protocol_validation":"NOT_RUN","production_decision":"NO_GO"}))
if __name__=="__main__":
    main()
