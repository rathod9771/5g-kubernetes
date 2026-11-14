#!/usr/bin/env python3
"""Deterministic fail-closed experimental OAI 2026.w45 NAS Service Reject #9 patch.

3GPP TS 24.501 Release 16 clause 5.6.1.5: after SERVICE REJECT cause #9
(UE identity cannot be derived by network), the UE shall delete 5G-GUTI
and ngKSI, transition to 5GMM-DEREGISTERED and (non-emergency) initiate
initial registration. We send a fresh SUCI-based Registration Request
AFTER RRCRelease/NR_NAS_CONN_RELEASE_IND, never while SRB is connected.

Only apply to the SHA-pinned source in a disposable GitHub runner.
This research candidate is NOT fully standards-conformant or production.
"""
import argparse
import pathlib
import sys

HANDLER='static void handle_service_reject(nr_ue_nas_t *nas, const byte_array_t *buffer)\n{'
OLD_REJECT='''  /* Retrying the Service Request would loop forever, and re-registering is not supported:
   * terminate as after a Registration Reject. */
  LOG_E(NAS, "Service Reject: cannot connect, exiting\\n");'''
NEW_REJECT='''  /* 3GPP TS 24.501 §5.6.1.5, cause #9 (non-emergency):
   * the AMF could not derive an identity from the stale 5G-GUTI after
   * state loss. Invalidate identity and security context; register
   * again using SUCI ONLY after RRC enters IDLE. A Service Request
   * retry with the stale context is not permitted. Experimental. */
  if (msg.cause == UE_identity_cannot_be_derived_by_the_network) {
    DevAssert(nas->UE_id < MAX_NUM_NR_UE_INST);
    if (nas->guti) {
      free(nas->guti);
      nas->guti = NULL;
    }
    if (nas->ksi) {
      free(nas->ksi);
      nas->ksi = NULL;
    }
    if (nas->security_container) {
      stream_security_container_delete(nas->security_container);
      nas->security_container = NULL;
    }
    memset(&nas->security, 0, sizeof(nas->security));
    nas->fiveGMM_state = FGS_DEREGISTERED;
    stage5g_pending_initial_registration[nas->UE_id] = true;
    LOG_W(NAS, "Stage5G: cause #9, cleared stale GUTI and NAS security; await RRC IDLE for initial registration\\n");
    return;
  }

  /* No unreviewed automatic retry for other reject causes. */
  LOG_E(NAS, "Service Reject: cannot connect, exiting\\n");'''
MARK='''        nas->fiveGMM_mode = FGS_IDLE;
        // TODO handle connection release'''
REPLACEMENT='''        nas->fiveGMM_mode = FGS_IDLE;
        /* Perform only the one-shot fallback initiated by SERVICE REJECT
         * cause #9. NAS_INITIAL_UL_TRANSFER_REQ is buffered by RRC and
         * transmitted in RRCSetupComplete once new SRB1 is established. */
        if (nas->UE_id < MAX_NUM_NR_UE_INST && stage5g_pending_initial_registration[nas->UE_id]) {
          stage5g_pending_initial_registration[nas->UE_id] = false;
          as_nas_info_t registration = {0};
          generateRegistrationRequest(&registration, nas, false);
          if (!registration.nas_data || registration.length == 0) {
            LOG_E(NAS, "Stage5G: failed to encode initial Registration Request after cause #9\\n");
            free(registration.nas_data);
          } else {
            send_nas_initial_ul_transfer_req(nas, &registration);
            LOG_I(NAS, "Stage5G: sent SUCI-based initial Registration Request after cause #9 and RRC IDLE\\n");
          }
        }
        // TODO handle connection release'''
DECL='''/* Experimental Release 16 cause #9 resumption: each synthetic UE may
 * attempt at most one initial Registration Request per rejected Service
 * Request; never retry the stale GUTI/NGKSI state. */
static bool stage5g_pending_initial_registration[MAX_NUM_NR_UE_INST] = {false};

'''

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("source",type=pathlib.Path)
    opt=parser.parse_args()
    original=opt.source.read_text()
    for label,old in (("cause-9 handler",HANDLER),
                      ("reject default",OLD_REJECT),
                      ("RRC IDLE transition",MARK)):
        count=original.count(old)
        if count!=1:
            raise SystemExit(f"FAIL CLOSED: expected 1 {label} anchor, found {count}")
    if "stage5g_pending_initial_registration" in original:
        raise SystemExit("FAIL CLOSED: patch already installed or upstream changed")
    changed=(original
        .replace(HANDLER,DECL+HANDLER)
        .replace(OLD_REJECT,NEW_REJECT)
        .replace(MARK,REPLACEMENT))
    if "send_nas_initial_ul_transfer_req(nas, &registration)" not in changed:
        raise SystemExit("FAIL CLOSED: initial registration trigger absent")
    if "nas->termination_procedure = true" not in changed:
        raise SystemExit("FAIL CLOSED: non-cause-9 reject behavior was lost")
    opt.source.write_text(changed)
    print("Stage 5G limited source patch applied. Await build and live test; NO_GO.")

if __name__=="__main__":
    main()
