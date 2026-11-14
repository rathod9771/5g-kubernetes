#!/usr/bin/env python3
"""Test-only OAI W45 bounded NAS Service Request timeout/retry patch.

Apply AFTER the exact-pinned Stage5G cause-9 patch. This adds an experimental
T3517-like 15-second timer, at most five Service Request attempts, an explicit
NAS->RRC idle request when access is stuck, and a 12-second backoff. It does
not claim complete TS 24.501 timer/conformance coverage. NEVER for production.
"""
import argparse
from pathlib import Path

STATE = r'''/* Stage5I: experimental, bounded T3517 Service Request recovery.
 * A timer expiry does not imply a Service Reject or cause #9. */
enum { STAGE5I_NONE, STAGE5I_WAIT_SERVICE, STAGE5I_WAIT_IDLE, STAGE5I_WAIT_RETRY };
typedef struct {
  long timer_id;
  unsigned int attempts;
  bool mo_ul;
  int phase;
} stage5i_state_t;
static stage5i_state_t stage5i_state[MAX_NUM_NR_UE_INST] = {0};

static void stage5i_stop_timer(nr_ue_nas_t *nas)
{
  stage5i_state_t *s = &stage5i_state[nas->UE_id];
  if (s->timer_id > 0)
    timer_remove(s->timer_id);
  s->timer_id = 0;
  s->phase = STAGE5I_NONE;
}

static void stage5i_reset(nr_ue_nas_t *nas)
{
  stage5i_stop_timer(nas);
  stage5i_state[nas->UE_id].attempts = 0;
}

static bool stage5i_start_timer(nr_ue_nas_t *nas, unsigned int seconds, int phase)
{
  stage5i_state_t *s = &stage5i_state[nas->UE_id];
  long id = 0;
  if (timer_setup(seconds, 0, TASK_NAS_NRUE, nas->UE_id, TIMER_ONE_SHOT, NULL, &id) < 0 || id <= 0) {
    LOG_E(NAS, "Stage5I: failed to arm recovery timer, fail closed\n");
    return false;
  }
  s->timer_id = id;
  s->phase = phase;
  return true;
}

static void stage5i_schedule_retry(nr_ue_nas_t *nas)
{
  stage5i_state_t *s = &stage5i_state[nas->UE_id];
  if (nas->termination_procedure || nas->fiveGMM_state != FGS_REGISTERED)
    return;
  if (s->attempts >= 5) {
    LOG_E(NAS, "Stage5I: bounded Service Request attempts exhausted, no further auto-retry\n");
    return;
  }
  if (stage5i_start_timer(nas, 12, STAGE5I_WAIT_RETRY))
    LOG_W(NAS, "Stage5I: schedule one bounded Service Request retry in 12 seconds (attempts=%u)\n", s->attempts);
}

static void stage5i_arm_service(nr_ue_nas_t *nas, bool mo_ul)
{
  stage5i_state_t *s = &stage5i_state[nas->UE_id];
  stage5i_stop_timer(nas);
  s->mo_ul = mo_ul;
  s->attempts++;
  if (stage5i_start_timer(nas, 15, STAGE5I_WAIT_SERVICE))
    LOG_I(NAS, "Stage5I: T3517-like timer armed for Service Request attempt %u/5\n", s->attempts);
}

'''
TIMER_CASE=r'''      case TIMER_HAS_EXPIRED: {
        stage5i_state_t *s = &stage5i_state[nas->UE_id];
        long id = TIMER_HAS_EXPIRED(msg_p).timer_id;
        if (id <= 0 || id != s->timer_id)
          break; /* An old cancelled timer must never trigger recovery. */
        int phase = s->phase;
        s->timer_id = 0;
        s->phase = STAGE5I_NONE;
        if (phase == STAGE5I_WAIT_SERVICE && nas->fiveGMM_state == FGS_SERVICE_REQUEST_INITIATED) {
          LOG_W(NAS, "Stage5I: T3517-like expiry without Service Accept/Reject; request AS idle\n");
          abort_service_request(nas);
          if (nas->fiveGMM_mode == FGS_IDLE) {
            stage5i_schedule_retry(nas);
          } else {
            s->phase = STAGE5I_WAIT_IDLE;
            MessageDef *idle = itti_alloc_new_message(TASK_NAS_NRUE, nas->UE_id, NAS_STAGE5I_FORCE_IDLE_REQ);
            itti_send_msg_to_task(TASK_RRC_NRUE, nas->UE_id, idle);
          }
        } else if (phase == STAGE5I_WAIT_RETRY) {
          if (nas->fiveGMM_mode == FGS_IDLE && nas->fiveGMM_state == FGS_REGISTERED)
            initiate_service_request(nas, s->mo_ul);
          else
            LOG_W(NAS, "Stage5I: retry timer fired outside 5GMM IDLE/REGISTERED: no forced NAS request\n");
        }
        break;
      }

'''
def replace_once(source, old, new, label):
    n=source.count(old)
    if n != 1:
        raise RuntimeError(f"FAIL CLOSED: {label}: expected one anchor, observed {n}")
    return source.replace(old,new)

def apply(nas_path, rrc_path, defs_path):
    nas=nas_path.read_text()
    rrc=rrc_path.read_text()
    defs=defs_path.read_text()
    if "Stage5G: cause #9, cleared stale GUTI" not in nas:
        raise RuntimeError("Stage5G source patch must be applied first")
    for marker in ("stage5i_state_t", "NAS_STAGE5I_FORCE_IDLE_REQ"):
        if marker in nas or marker in rrc or marker in defs:
            raise RuntimeError("FAIL CLOSED: Stage5I patch already applied")
    nas=replace_once(nas,
       "static bool initiate_service_request(nr_ue_nas_t *nas, bool mo_ul_data)\n{",
       STATE+"static bool initiate_service_request(nr_ue_nas_t *nas, bool mo_ul_data)\n{",
       "NAS state insertion")
    nas=replace_once(nas,
       '  send_nas_initial_ul_transfer_req(nas, &initialNasMsg);\n  LOG_I(NAS, "[UE %ld] Service Request',
       '  send_nas_initial_ul_transfer_req(nas, &initialNasMsg);\n  stage5i_arm_service(nas, mo_ul_data);\n  LOG_I(NAS, "[UE %ld] Service Request',
       "arm T3517 on initial NAS")
    nas=replace_once(nas,
       'static void handle_service_accept(nr_ue_nas_t *nas, const byte_array_t *buffer)\n{\n',
       'static void handle_service_accept(nr_ue_nas_t *nas, const byte_array_t *buffer)\n{\n  stage5i_reset(nas);\n',
       "accept clears T3517")
    nas=replace_once(nas,
       'static void handle_service_reject(nr_ue_nas_t *nas, const byte_array_t *buffer)\n{\n',
       'static void handle_service_reject(nr_ue_nas_t *nas, const byte_array_t *buffer)\n{\n  stage5i_reset(nas);\n',
       "reject clears T3517")
    nas=replace_once(nas,
       '      case MESSAGE_TEST:\n        break;\n',
       '      case MESSAGE_TEST:\n        break;\n\n'+TIMER_CASE,
       "NAS task timer event")
    nas=replace_once(nas,
       '        abort_service_request(nas);\n        nas->fiveGMM_mode = FGS_IDLE;\n        /* Perform only the one-shot fallback',
       '''        bool stage5i_sr_pending = stage5i_state[nas->UE_id].phase == STAGE5I_WAIT_SERVICE
                               || stage5i_state[nas->UE_id].phase == STAGE5I_WAIT_IDLE;
        if (stage5i_sr_pending)
          stage5i_stop_timer(nas);
        abort_service_request(nas);
        nas->fiveGMM_mode = FGS_IDLE;
        if (stage5i_sr_pending)
          stage5i_schedule_retry(nas);
        /* Perform only the one-shot fallback''',
       "RRC release re-arms pending SR")
    defs=replace_once(defs,
       'MESSAGE_DEF(NAS_DETACH_REQ, MESSAGE_PRIORITY_MED, nas_detach_req_t, nas_detach_req)',
       '''MESSAGE_DEF(NAS_DETACH_REQ, MESSAGE_PRIORITY_MED, nas_detach_req_t, nas_detach_req)
MESSAGE_DEF(NAS_STAGE5I_FORCE_IDLE_REQ, MESSAGE_PRIORITY_MED, IttiMsgEmpty, nas_stage5i_force_idle_req)''',
       "dedicated RRC recovery message")
    rrc=replace_once(rrc,
       '  case NAS_DETACH_REQ:\n',
       '''  case NAS_STAGE5I_FORCE_IDLE_REQ: {
    /* Research only: local RRC reset after the UE NAS Service Request
     * timed out; never send a fake NAS Service Reject or detach the UE. */
    if (rrc->nrRrcState != RRC_STATE_IDLE_NR && rrc->nrRrcState != RRC_STATE_DETACH_NR) {
      RRCLOG_W("Stage5I: T3517-like expiry, return AS to RRC_IDLE for bounded NAS retry\n");
      nr_rrc_going_to_IDLE(rrc, OTHER, NULL);
    }
    break;
  }

  case NAS_DETACH_REQ:
'''.replace('bounded NAS retry\n");','bounded NAS retry\\n");'),
       "RRC local idle action")
    if not all(x in nas for x in ("Stage5I: T3517-like expiry", "Stage5G: sent SUCI-based initial")):
        raise RuntimeError("FAIL CLOSED: expected independent cause9/T3517 paths")
    return {nas_path:nas,rrc_path:rrc,defs_path:defs}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("root",type=Path)
    opt=p.parse_args()
    root=opt.root.resolve()
    paths=(
      root/"openair3/NAS/NR_UE/nr_nas_msg.c",
      root/"openair2/RRC/NR_UE/rrc_UE.c",
      root/"openair2/COMMON/rrc_messages_def.h",
    )
    if not all(x.is_file() for x in paths):
        raise SystemExit("FAIL CLOSED: wrong OAI source tree")
    changed=apply(*paths)
    for path,body in changed.items():
        path.write_text(body)
    print("Stage5I experimental bounded NAS/RRC timeout patch applied; production NO_GO")

if __name__ == "__main__":
    main()
