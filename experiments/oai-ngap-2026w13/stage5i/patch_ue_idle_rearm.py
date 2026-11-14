#!/usr/bin/env python3
"""Stage5I test-only UE fix: rearm idle TUN after RRC release interrupts SR.

Applies *after* the source-pinned Stage5G cause-9 patch and only at the
single NR_NAS_CONN_RELEASE_IND transition. Does not fabricate NAS success or
implement the missing T3517 timer: NOT 3GPP production-compliant.
"""
import argparse
from pathlib import Path

ANCHOR = '''        abort_service_request(nas);
        nas->fiveGMM_mode = FGS_IDLE;
        /* Perform only the one-shot fallback initiated by SERVICE REJECT'''
REPLACEMENT = r'''        const bool stage5i_interrupted_sr = nas->fiveGMM_state == FGS_SERVICE_REQUEST_INITIATED;
        abort_service_request(nas);
        nas->fiveGMM_mode = FGS_IDLE;
        /* The uplink TUN reader was stopped while the interrupted Service
         * Request was pending. Without rearming the idle listener, no later
         * application packet can initiate a fresh Service Request. This is
         * a test-only recovery probe; T3517/backoff/attempt limits remain
         * required before any production claim. */
        if (stage5i_interrupted_sr) {
          send_nas_tun_req_action(nas, NAS_TUN_START_IDLE_LISTENER);
          LOG_I(NAS, "Stage5I: RRC release interrupted Service Request; idle TUN listener rearmed\n");
        }
        /* Perform only the one-shot fallback initiated by SERVICE REJECT'''

def patch(source: str) -> str:
    if source.count(ANCHOR) != 1:
        raise ValueError(f"FAIL CLOSED: UE RRC-release anchor count={source.count(ANCHOR)} (expected 1)")
    if "Stage5I: RRC release interrupted Service Request" in source:
        raise ValueError("FAIL CLOSED: already applied")
    return source.replace(ANCHOR, REPLACEMENT)

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("source", type=Path)
    p = ap.parse_args().source
    p.write_text(patch(p.read_text()))
    print("Stage5I test-only interrupted-SR listener rearm applied; NO_GO")

if __name__ == "__main__":
    main()
