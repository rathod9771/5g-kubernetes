#!/usr/bin/env python3
"""Stage5Q opt-in research patch: bounded Stage5M BWP diagnostic and cached BWP0 rebind.

Apply ONLY after the pinned W45 Stage5M guard, on isolated RFsim.
Never synthesize BWP configuration, bypass NGAP/NAS or touch a live cluster.
The fallback only reattaches already configured initial UL/DL BWP0 if:
  - both BWP0 structures exist in the MAC-owned arrays,
  - SIB1/MIB cell context is present,
  - UL RACH config and nonzero DL/UL allocation are available.
Otherwise RA remains fail-closed, with exponential diagnostic sampling.
This does NOT prove conformance or guarantee recovery.
"""
import argparse
from pathlib import Path

START = '  if (mac->current_UL_BWP == NULL || mac->current_DL_BWP == NULL) {\n'
END = '  RA_config_t *ra = &mac->ra;\n'
REPLACE = r'''  if (mac->current_UL_BWP == NULL || mac->current_DL_BWP == NULL) {
    /* Stage5Q: NO new BWP allocation. Rebind only SIB1-configured BWP0
     * already owned by this MAC and with a valid PRACH configuration. */
    NR_UE_UL_BWP_t *ul0 = mac->ul_BWPs.count > 0 ? get_ul_bwp_structure(mac, 0, false) : NULL;
    NR_UE_DL_BWP_t *dl0 = mac->dl_BWPs.count > 0 ? get_dl_bwp_structure(mac, 0, false) : NULL;
    if (mac->mib && ul0 && dl0 && ul0->rach_ConfigCommon &&
        ul0->BWPSize > 0 && dl0->BWPSize > 0) {
      mac->current_UL_BWP = ul0;
      mac->current_DL_BWP = dl0;
      LOG_I(NR_MAC, "Stage5Q: restored existing configured BWP0 pointers for bounded RA\n");
    } else {
      static unsigned int stage5q_deferrals = 0; /* one UE per isolated test process */
      if (stage5q_deferrals < 0xffffffffU)
        stage5q_deferrals++;
      unsigned int count = stage5q_deferrals;
      if ((count & (count - 1)) == 0) {
        LOG_W(NR_MAC,
              "Stage5M: defer random access (Stage5Q diagnostic count=%u mac_state=%d ra_state=%d "
              "ul_count=%d dl_count=%d mib=%d configured_ul0=%d configured_dl0=%d)\n",
              count, mac->state, mac->ra.ra_state,
              mac->ul_BWPs.count, mac->dl_BWPs.count, mac->mib != NULL,
              ul0 && ul0->rach_ConfigCommon && ul0->BWPSize > 0,
              dl0 && dl0->BWPSize > 0);
      }
      return false;
    }
  }
'''
def patch(root):
    file=root/"openair2/LAYER2/NR_MAC_UE/nr_ra_procedures.c"
    if not file.is_file():
        raise RuntimeError("FAIL CLOSED Stage5Q: required upstream file absent")
    raw=file.read_text()
    if "Stage5Q: restored existing configured BWP0 pointers" in raw:
        raise RuntimeError("FAIL CLOSED Stage5Q: already patched")
    if raw.count(START)!=1:
        raise RuntimeError("FAIL CLOSED Stage5Q: Stage5M guard anchor absent/non-unique")
    start=raw.index(START)
    end=raw.find(END,start)
    if end < 0:
        raise RuntimeError("FAIL CLOSED Stage5Q: expected RA state entry missing")
    old=raw[start:end]
    if old.count("Stage5M: defer random access")!=1 or old.count("return false;")!=1:
        raise RuntimeError("FAIL CLOSED Stage5Q: unexpected guard contents")
    patched=raw[:start]+REPLACE+raw[end:]
    if patched.count("Stage5Q: restored existing configured BWP0 pointers")!=1:
        raise RuntimeError("FAIL CLOSED Stage5Q: patch not applied exactly once")
    file.write_text(patched)
    print("PASS Stage5Q guarded cached-BWP0 rebind and bounded diagnostics; RESEARCH ONLY NO DEPLOY")

if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source",type=Path)
    args=parser.parse_args()
    patch(args.source.resolve())
