#!/usr/bin/env python3
"""Test-only pinned CU NGAP failure-path patch. Not production authorization.

When an RRCSetupComplete's InitialUEMessage has no AMF association, NGAP
currently drops the NAS PDU but leaves the CU's RRC UE connected. A local
radio release is necessary to allow the UE to return to IDLE and retry.
The internal release command is *not* an AMF-originated NGAP message.
"""
import argparse
from pathlib import Path

ANCHOR = '''      case NGAP_NAS_FIRST_REQ:
        ngap_gNB_handle_nas_first_req(instance, &NGAP_NAS_FIRST_REQ(received_msg));
        free_byte_array(NGAP_NAS_FIRST_REQ(received_msg).nas_pdu);
        break;'''
REPLACEMENT = '''      case NGAP_NAS_FIRST_REQ: {
        /* Stage5I research-only: when the InitialUEMessage cannot reach an
         * AMF, release the local RRC UE instead of leaving it in a stranded
         * RRC_CONNECTED state. No synthetic NAS, no replay, no E2/RIC.
         * This is a local control action, NOT a network-originated release. */
        const uint32_t ue_id = NGAP_NAS_FIRST_REQ(received_msg).gNB_ue_ngap_id;
        const int outcome = ngap_gNB_handle_nas_first_req(instance, &NGAP_NAS_FIRST_REQ(received_msg));
        free_byte_array(NGAP_NAS_FIRST_REQ(received_msg).nas_pdu);
        if (outcome < 0) {
          NGAP_WARN("Stage5I: InitialUEMessage undelivered; release local RRC UE %u (research only)\n", ue_id);
          MessageDef *release = itti_alloc_new_message(TASK_NGAP, instance, NGAP_UE_CONTEXT_RELEASE_COMMAND);
          NGAP_UE_CONTEXT_RELEASE_COMMAND(release).gNB_ue_ngap_id = ue_id;
          itti_send_msg_to_task(TASK_RRC_GNB, instance, release);
        }
        break;
      }'''

def patch(source: str) -> str:
    if source.count(ANCHOR) != 1:
        raise ValueError(f"FAIL CLOSED: NGAP InitialUEMessage anchor count={source.count(ANCHOR)} (expected 1)")
    if "Stage5I: InitialUEMessage undelivered" in source:
        raise ValueError("FAIL CLOSED: already applied")
    return source.replace(ANCHOR, REPLACEMENT)

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("source", type=Path)
    p = ap.parse_args().source
    old = p.read_text()
    p.write_text(patch(old))
    print("Stage5I test-only CU local release failure path applied; NO_GO")

if __name__ == "__main__":
    main()
