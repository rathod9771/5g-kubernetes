#!/usr/bin/env python3
"""Stage5M test-only OAI W45 MAC random-access BWP prerequisite guard.

Stage5L GDB independently located SIGSEGV at nr_ra_procedures.c:747 in
init_RA, called from nr_ue_ul_scheduler:2627. This candidate refuses to
enter RA initialization without configured active UL/DL BWP pointers.
Not a complete 3GPP mobility or recovery fix; no production deployment.
Apply only after the exact pinned cause9/T3517/TUN research patches.
"""
import argparse
from pathlib import Path

def one(body, before, after, label):
    n = body.count(before)
    if n != 1:
        raise RuntimeError(f"FAIL CLOSED {label}: exact upstream anchor count {n}, expected one")
    return body.replace(before, after)

def patch(root):
    ra_file = root / "openair2/LAYER2/NR_MAC_UE/nr_ra_procedures.c"
    scheduler = root / "openair2/LAYER2/NR_MAC_UE/nr_ue_scheduler.c"
    if not ra_file.is_file() or not scheduler.is_file():
        raise RuntimeError("FAIL CLOSED: pinned OAI MAC source paths missing")
    ra, ul = ra_file.read_text(), scheduler.read_text()
    if "Stage5M: defer random access" in ra or "Stage5M: defer random access" in ul:
        raise RuntimeError("FAIL CLOSED: Stage5M already patched")
    ra = one(ra,
        'bool init_RA(NR_UE_MAC_INST_t *mac)\n{\n  RA_config_t *ra = &mac->ra;\n',
        '''bool init_RA(NR_UE_MAC_INST_t *mac)
{
  /* Stage5M: After an RRC idle/reset transition, a new RA trigger can
   * race with the SIB1-derived UL/DL BWP configuration. Do not enter
   * RA initialization before both BWP pointers are installed. This
   * prevents the observed null dereference at upstream line 747, but
   * does not forge registration or restore any user-plane traffic. */
  if (mac->current_UL_BWP == NULL || mac->current_DL_BWP == NULL) {
    LOG_W(NR_MAC, "Stage5M: defer random access until UL/DL BWP configuration is ready\\n");
    return false;
  }
  RA_config_t *ra = &mac->ra;
''',
        "MAC init_RA BWP prerequisite")
    ul = one(ul,
        '''  if (mac->state == UE_PERFORMING_RA && ra->ra_state == nrRA_UE_IDLE) {
    init_RA(mac);
    // perform the Random Access Resource selection procedure (see clause 5.1.2 and .2a)
    ra_resource_selection(mac);
  }
''',
        '''  if (mac->state == UE_PERFORMING_RA && ra->ra_state == nrRA_UE_IDLE) {
    /* Stage5M: do not run RA resource selection after deferred initialization
     * because current_UL_BWP/current_DL_BWP may still be NULL. The RRC
     * configuration path must populate them before the next scheduler slot. */
    if (!init_RA(mac))
      return;
    // perform the Random Access Resource selection procedure (see clause 5.1.2 and .2a)
    ra_resource_selection(mac);
  }
''',
        "UL scheduler respects init_RA false")
    if "Stage5M: defer random access" not in ra or "if (!init_RA(mac))" not in ul:
        raise RuntimeError("FAIL CLOSED: BWP guard not present in both required functions")
    ra_file.write_text(ra)
    scheduler.write_text(ul)

if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("source", type=Path)
    args = p.parse_args()
    patch(args.source.resolve())
    print("PASS Stage5M anchored MAC RA BWP guard; TEST ONLY, NO DEPLOY")
