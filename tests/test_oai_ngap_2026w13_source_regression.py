#!/usr/bin/env python3
"""Source-derived, mocked-SCTP NGAP retry tests for OAI 2026.w13.

This deliberately tests compiled C handlers from the patched upstream checkout.
It does NOT start real SCTP sockets, Open5GS, UE softmodems, or Kubernetes.
"""
import os
import pathlib
import re
import subprocess
import tempfile
import unittest

SOURCE_DIR = os.environ.get("OAI_SOURCE_DIR", "").strip()
if not SOURCE_DIR:
    # This suite extracts and compiles patched upstream C code. A generic
    # repository checkout lacks that source. Missing a source tree must not
    # turn mocked tests into fabricated success; run them in their dedicated
    # source-regression job with OAI_SOURCE_DIR explicitly set.
    raise unittest.SkipTest("OAI_SOURCE_DIR not provided: requires dedicated OAI source-regression job")
ROOT = pathlib.Path(SOURCE_DIR)
if not ROOT.is_dir() or not (ROOT / "openair3/NGAP/ngap_gNB.c").is_file():
    raise SystemExit("OAI_SOURCE_DIR must point to the patched OAI 2026.w13 source tree")


def load(path):
    return (ROOT / path).read_text(encoding="utf-8")


def extract_function(source, symbol):
    # Extract a full, real upstream C function; do not recreate its control flow
    # in the test. Function bodies used here contain no braces inside strings.
    match = None
    opening = -1
    for candidate in re.finditer(r"\b" + re.escape(symbol) + r"\s*\(", source):
        candidate_open = source.find("{", candidate.end())
        candidate_semi = source.find(";", candidate.end())
        if candidate_open >= 0 and (candidate_semi < 0 or candidate_open < candidate_semi):
            match, opening = candidate, candidate_open
            break
    if match is None:
        raise AssertionError("Missing upstream function definition: " + symbol)
    start = source.rfind("\n", 0, match.start()) + 1
    depth = 0
    for i in range(opening, len(source)):
        if source[i] == "{":
            depth += 1
        if source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start : i + 1]
    raise AssertionError("Unclosed function: " + symbol)


SHIM = r"""
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define TASK_NGAP 1
#define TASK_SCTP 2
#define TASK_GNB_APP 3
#define TASK_RRC_GNB 4
#define SCTP_NEW_ASSOCIATION_REQ 5
enum {
  NGAP_REGISTER_GNB_CNF = 6,
  NGAP_DEREGISTERED_GNB_IND = 7,
  NGAP_UE_CONTEXT_RELEASE_COMMAND = 8
};
#define NGAP_PORT_NUMBER 38412
#define NGAP_SCTP_PPID 60
#define NGAP 0
#define SCTP_LOOKUP_TIMER_TIMEOUT_SEC 30
#define TIMER_ONE_SHOT 1
#define NGAP_GNB_STATE_DISCONNECTED 0
#define NGAP_GNB_STATE_CONNECTED 1
#define SCTP_STATE_CLOSED 0
#define SCTP_STATE_SHUTDOWN 1
#define SCTP_STATE_ESTABLISHED 2
#define SCTP_STATE_UNREACHABLE 3

#define NGAP_INFO(...) ((void)0)
#define NGAP_WARN(...) ((void)0)
#define LOG_A(...) ((void)0)
#define LOG_I(...) ((void)0)
#define DevAssert(cond) assert(cond)
#define DevCheck(cond, ...) assert(cond)
#define RB_FOREACH_SAFE(var, kind, head, next) for ((var)=NULL; (var)!=NULL; )

typedef long instance_t;
typedef int sctp_assoc_t;
typedef struct { char octets[64]; } net_ip_address_t;
typedef struct {
  instance_t instance;
  int ngap_amf_nb;
  int ngap_amf_pending_nb;
  int ngap_amf_associated_nb;
  net_ip_address_t gNB_ng_ip;
} ngap_gNB_instance_t;
typedef struct ngap_gNB_amf_data_s {
  long t_reconnect;
  int state;
  int assoc_id;
  uint16_t cnx_id;
  uint16_t in_streams;
  uint16_t out_streams;
  net_ip_address_t amf_s1_ip;
  ngap_gNB_instance_t *ngap_gNB_instance;
} ngap_gNB_amf_data_t;
typedef struct {
  ngap_gNB_amf_data_t *amf_ref;
  uint32_t gNB_ue_ngap_id;
  uint64_t amf_ue_ngap_id;
} ngap_gNB_ue_context_t;
typedef struct {
  uint16_t port;
  uint32_t ppid;
  uint16_t in_streams;
  uint16_t out_streams;
  uint16_t ulp_cnx_id;
  net_ip_address_t local_address;
  net_ip_address_t remote_address;
} sctp_new_association_req_t;
typedef struct {
  int sctp_state;
  int assoc_id;
  uint16_t ulp_cnx_id;
  uint16_t in_streams;
  uint16_t out_streams;
} sctp_new_association_resp_t;
typedef struct {
  uint32_t gNB_ue_ngap_id;
} ngap_ue_context_release_command_t;
typedef struct {int nb_amf;} ngap_gnb_amf_notice_t;
typedef struct {
  struct {
    sctp_new_association_req_t sctp_new_association_req;
    ngap_gnb_amf_notice_t ngap_register_gnb_cnf;
    ngap_gnb_amf_notice_t ngap_deregistered_gnb_ind;
    ngap_ue_context_release_command_t ngap_ue_context_release_command;
  } ittiMsg;
  int msg_type;
} MessageDef;
#define NGAP_REGISTER_GNB_CNF(m) ((m)->ittiMsg.ngap_register_gnb_cnf)
#define NGAP_DEREGISTERED_GNB_IND(m) ((m)->ittiMsg.ngap_deregistered_gnb_ind)
#define NGAP_UE_CONTEXT_RELEASE_COMMAND(m) ((m)->ittiMsg.ngap_ue_context_release_command)

static ngap_gNB_instance_t inst = {.instance=0, .ngap_amf_nb=1, .ngap_amf_pending_nb=1};
static ngap_gNB_amf_data_t amf;
static int timers_scheduled = 0, sctp_requests = 0, setup_requests = 0;
static int registration_notices = 0, deregistration_notices = 0;
static int last_timer_seconds = -1;
static sctp_new_association_req_t last_req;

static MessageDef *itti_alloc_new_message(int task, int origin, int msg_type) {
  (void)task; (void)origin;
  MessageDef *m = calloc(1, sizeof(*m));
  assert(m);
  m->msg_type = msg_type;
  return m;
}
static int itti_send_msg_to_task(int task, instance_t instance, MessageDef *m) {
  (void)task; (void)instance;
  if (m->msg_type == SCTP_NEW_ASSOCIATION_REQ) {
    ++sctp_requests;
    last_req = m->ittiMsg.sctp_new_association_req;
  } else if (m->msg_type == NGAP_REGISTER_GNB_CNF) {
    ++registration_notices;
  } else if (m->msg_type == NGAP_DEREGISTERED_GNB_IND) {
    ++deregistration_notices;
  }
  free(m);
  return 0;
}
static int timer_setup(int seconds, int micros, int task, instance_t instance,
                       int type, void *arg, long *timer_id) {
  (void)micros; (void)task; (void)instance; (void)type; (void)arg;
  last_timer_seconds = seconds;
  *timer_id = ++timers_scheduled;
  return 0;
}
static ngap_gNB_instance_t *ngap_gNB_get_instance(instance_t instance) {
  assert(instance == 0);
  return &inst;
}
static ngap_gNB_amf_data_t *ngap_gNB_get_AMF(ngap_gNB_instance_t *i, int assoc_id, int cnx_id) {
  (void)assoc_id; assert(i == &inst);
  assert(cnx_id == amf.cnx_id);
  return &amf;
}
static int ngap_gNB_generate_ng_setup_request(ngap_gNB_instance_t *i, ngap_gNB_amf_data_t *a) {
  assert(i == &inst && a == &amf);
  setup_requests++;
  return 0;
}
void ngap_release_ues_for_amf(ngap_gNB_amf_data_t *);
void ngap_handle_ng_setup_message(ngap_gNB_amf_data_t *, int);
"""

MAIN = r"""
static void failure(int state) {
  sctp_new_association_resp_t r = {.sctp_state=state, .ulp_cnx_id=amf.cnx_id};
  ngap_gNB_handle_sctp_association_resp(0, &r);
}
static void connected(int assoc) {
  sctp_new_association_resp_t r = {
    .sctp_state=SCTP_STATE_ESTABLISHED, .ulp_cnx_id=amf.cnx_id,
    .assoc_id=assoc, .in_streams=5, .out_streams=7
  };
  ngap_gNB_handle_sctp_association_resp(0, &r);
}
int main(void) {
  amf.ngap_gNB_instance = &inst;
  amf.cnx_id = 12;
  amf.state = NGAP_GNB_STATE_DISCONNECTED;
  amf.in_streams = 5;
  amf.out_streams = 7;
  strcpy(amf.amf_s1_ip.octets, "10.103.141.168");
  strcpy(inst.gNB_ng_ip.octets, "10.244.0.104");

  /* Initial unreachable SCTP: pending accounting and timer are correct. */
  failure(SCTP_STATE_UNREACHABLE);
  assert(inst.ngap_amf_nb == 1);
  assert(inst.ngap_amf_pending_nb == 0);
  assert(inst.ngap_amf_associated_nb == 0);
  assert(timers_scheduled == 1 && last_timer_seconds == 30);
  assert(registration_notices == 1);

  /* Simulated one-shot expiry calls the exact redial C function. */
  ngap_gNB_redial_amf(&amf);
  assert(sctp_requests == 1);
  assert(last_req.port == 38412);
  assert(last_req.ppid == NGAP_SCTP_PPID);
  assert(last_req.in_streams == 5 && last_req.out_streams == 7);
  assert(last_req.ulp_cnx_id == 12);
  assert(memcmp(&last_req.remote_address, &amf.amf_s1_ip, sizeof(net_ip_address_t)) == 0);
  assert(inst.ngap_amf_nb == 1 && inst.ngap_amf_pending_nb == 1);

  /* Repeated unreachable response triggers another bounded 30s timer. */
  failure(SCTP_STATE_UNREACHABLE);
  assert(timers_scheduled == 2);
  assert(inst.ngap_amf_nb == 1 && inst.ngap_amf_pending_nb == 0);

  /* Recovered transport sends NG Setup, then mocked successful NG Setup
     response updates associated/pending counts through real handler. */
  ngap_gNB_redial_amf(&amf);
  assert(inst.ngap_amf_pending_nb == 1);
  connected(42);
  assert(setup_requests == 1 && amf.assoc_id == 42);
  amf.state = NGAP_GNB_STATE_CONNECTED;
  inst.ngap_amf_associated_nb = 1;  /* Actual NG Setup decoder not exercised. */
  ngap_handle_ng_setup_message(&amf, 0);
  assert(inst.ngap_amf_pending_nb == 0);
  assert(inst.ngap_amf_associated_nb == 1);

  /* A prior connected association shuts down; recovery timer is armed. */
  failure(SCTP_STATE_SHUTDOWN);
  assert(amf.state == NGAP_GNB_STATE_DISCONNECTED);
  assert(inst.ngap_amf_associated_nb == 0);
  assert(timers_scheduled == 3);
  assert(deregistration_notices == 1);

  ngap_gNB_redial_amf(&amf);
  assert(inst.ngap_amf_nb == 1 && inst.ngap_amf_pending_nb == 1);
  connected(43);
  assert(setup_requests == 2 && amf.assoc_id == 43);
  amf.state = NGAP_GNB_STATE_CONNECTED;
  inst.ngap_amf_associated_nb = 1;  /* Mock response, not actual AMF. */
  ngap_handle_ng_setup_message(&amf, 0);
  assert(inst.ngap_amf_pending_nb == 0);
  assert(inst.ngap_amf_associated_nb == 1);
  puts("PASS: source-derived NGAP C retry, stream reuse, counters, and shutdown recovery");
  return 0;
}
"""

class OaiNgapRegression(unittest.TestCase):
    def test_patch_invariants_and_source_provenance(self):
        ngap = load("openair3/NGAP/ngap_gNB.c")
        register = extract_function(ngap, "ngap_gNB_register_amf")
        redial = extract_function(ngap, "ngap_gNB_redial_amf")
        response = extract_function(ngap, "ngap_gNB_handle_sctp_association_resp")
        for needle in ("ngap_amf_data_p->in_streams = in_streams;",
                       "ngap_amf_data_p->out_streams = out_streams;"):
            self.assertIn(needle, register)
        self.assertNotIn("inst->ngap_amf_nb++", redial)
        self.assertIn("inst->ngap_amf_pending_nb++", redial)
        self.assertIn("SCTP_STATE_UNREACHABLE", response)
        self.assertIn("ngap_release_ues_for_amf(ngap_amf_data_p)", response)

    def test_compiled_upstream_handlers_with_mocked_transport(self):
        ngap = load("openair3/NGAP/ngap_gNB.c")
        handlers = load("openair3/NGAP/ngap_gNB_handlers.c")
        contexts = load("openair3/NGAP/ngap_gNB_ue_context.c")
        compiled_c = "\n\n".join((
            SHIM,
            extract_function(ngap, "ngap_gNB_redial_amf"),
            extract_function(contexts, "ngap_release_ues_for_amf"),
            extract_function(handlers, "ngap_handle_ng_setup_message"),
            extract_function(ngap, "ngap_gNB_handle_sctp_association_resp"),
            MAIN,
        ))
        with tempfile.TemporaryDirectory(prefix="oai-ngap-regress-") as d:
            src = pathlib.Path(d) / "ngap_harness.c"
            binpath = pathlib.Path(d) / "ngap_harness"
            src.write_text(compiled_c, encoding="utf-8")
            subprocess.run(["gcc", "-std=c11", "-Wall", "-Wextra",
                            str(src), "-o", str(binpath)], check=True, timeout=60)
            result = subprocess.run([str(binpath)], check=True, timeout=20,
                                    capture_output=True, text=True)
            self.assertIn("PASS: source-derived NGAP C retry", result.stdout)

if __name__ == "__main__":
    unittest.main(verbosity=2)
