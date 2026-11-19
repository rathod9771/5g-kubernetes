#!/usr/bin/env python3
"""Stage5R observe-only, bounded and redacted two-UE protocol timeline.

Wraps the genuine Stage5P three-cycle AMF fault harness. No source patching,
retry changes, fake NAS/RRC responses, cluster access or production deployment.

Protocol indicators are derived from LIVE docker log streams and header-only
SCTP packet metadata, then reduced to a strictly allowlisted JSON timeline.
This collector saves no raw logs, subscriber IDs, NAS payloads or SCTP packets.
The underlying Stage5P harness separately archives synthetic test logs/GTP PCAPs.
"""
import argparse
from collections import Counter, defaultdict
import datetime
import json
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import types

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "stage5s"))
from run_three_cycle_recovery import configure
from amf_process_count_stage5y import count_amf_processes
from cu_amf_selection_stage5z import CUSelectionObserver
from rrc_radio_boundary_stage5zb import classify as classify_radio_boundary
from sib1_reacquisition_stage5zc import classify as classify_sib1_reacquisition
from mib_sib1_handoff_stage5zd import classify as classify_mib_handoff
from rrc_si_decision_stage5zf import classify as classify_si_decision

MAX_EVENTS = 1800
MAX_ROLE_CATEGORY_EVENTS = 100
MAX_LOG_LINE_BYTES = 4096

UE_RULES = (
    ("mac_bwp0_cached_restore", r"Stage5Q: restored existing configured BWP0 pointers"),
    ("mac_bwp_deferral", r"Stage5M: defer random access"),
    ("rrc_sib1_decoded", r"SIB1 decoded"),
    ("rrc_idle", r"RRC moved into IDLE state"),
    ("rrc_setup_request", r"RRCSetupRequest"),
    ("rrc_setup_complete", r"RRCSetupComplete"),
    ("rrc_setup", r"\bRRCSetup\b"),
    ("rrc_reconfiguration_complete", r"RRCReconfigurationComplete"),
    ("rrc_release", r"RRCRelease"),
    ("mac_prach", r"\bPRACH\b"),
    ("mac_ra_start", r"Starting Random Access|RA procedure|Initiating RA"),
    ("mac_rar", r"\bRAR\b|Random Access Response"),
    ("nas_mo_ul_ind", r"NAS_MO_UL_DATA_IND"),
    ("nas_service_request", r"Generate initial NAS message: Service Request"),
    ("nas_service_reject", r"Received NAS Service Reject message"),
    ("nas_service_accept", r"Received NAS Service Accept"),
    ("nas_t3517_timeout", r"Stage5I: T3517-like expiry"),
    ("nas_t3517_rearmed", r"Stage5I: schedule one bounded Service Request retry"),
    ("nas_retries_exhausted", r"Stage5I: bounded Service Request attempts exhausted"),
    ("nas_cause9", r"Stage5G: cause #9"),
    ("nas_suci_register", r"Stage5G: sent SUCI-based initial Registration Request"),
    ("nas_registration_accept", r"Received Registration Accept"),
    ("nas_pdu_accept", r"PDU Session Establishment Accept"),
    ("tun_ipv4_refresh", r"Stage5K: refreshed existing PDU TUN"),
)
CU_RULES = (
    ("cu_ngsetup_request", r"Send NGSetupRequest"),
    ("cu_ngsetup_response", r"Received NGSetupResponse"),
    ("cu_no_amf", r"No associated AMF|not associated with any AMF|no AMF associated"),
    ("cu_rrc_context_create", r"Create UE context"),
    ("cu_rrc_setup_complete", r"Received RRCSetupComplete"),
    ("cu_ul_nas", r"Received RRC UL Information Transfer"),
    ("cu_dl_nas", r"Send DL Information Transfer"),
    ("cu_ngap_ue_create", r"Create UE context .* AMF"),
    ("cu_rrc_context_release", r"Remove UE context"),
    ("cu_f1setup", r"F1 Setup"),
    ("cu_sctp_shutdown", r"Received SCTP SHUTDOWN EVENT"),
)
DU_RULES = (
    ("du_f1setup", r"F1 Setup"),
    ("du_sctp", r"SCTP association|SCTP_SHUTDOWN|SCTP.*established"),
    ("du_ra_ind", r"Random Access|PRACH|RACH"),
    ("du_ue_context", r"UE Context"),
)
CORE_RULES = (
    ("amf_ngap_accept", r"gNB-N2 accepted"),
    ("amf_ngap_initial_ue", r"InitialUEMessage"),
    ("amf_initial_ue_cell", r"RAN_UE_NGAP_ID\[[0-9]+\].*CellID\[0x[0-9a-f]+\]"),
    ("amf_unknown_tmsi", r"Unknown UE by 5G-S_TMSI"),
    ("amf_nas_service", r"Service Request"),
    ("amf_nas_registration", r"Registration Request|Registration complete"),
    ("amf_ue_context_release", r"UE Context Release"),
    ("amf_ngap_path", r"NGSetup|NG Setup"),
)
RULES = {
    "ue": tuple((k, re.compile(v, re.I)) for k,v in UE_RULES),
    "ue-small": tuple((k, re.compile(v, re.I)) for k,v in UE_RULES),
    "cu": tuple((k, re.compile(v, re.I)) for k,v in CU_RULES),
    "macro": tuple((k, re.compile(v, re.I)) for k,v in DU_RULES),
    "small": tuple((k, re.compile(v, re.I)) for k,v in DU_RULES),
    "amf-snapshot": tuple((k, re.compile(v, re.I)) for k,v in CORE_RULES),
    "core": tuple((k, re.compile(v, re.I)) for k,v in CORE_RULES),
}
DIAG_KEYS = ("count", "mac_state", "ra_state", "ul_count", "dl_count",
             "mib", "configured_ul0", "configured_dl0")
STAGE_MARKERS = {
    "STAGE5P-CYCLE-START", "STAGE5P-CYCLE", "T31-AMF-actually-stopped",
    "T32-patched-CU-AMF-outage-redial", "T33-AMF-process-restarted",
    "T34-new-NGSetupResponse-after-AMF-restart", "T91-reconnected-NGAP-with-both-F1-DUs",
    "T94-MO-UL-ping-probe", "T95-NGAP-and-F1C-after-UE-service",
}

def classify_lines(role, lines):
    """Deterministic privacy-safe categorization; NEVER returns original lines."""
    out = []
    for line in lines:
        clipped = line[:MAX_LOG_LINE_BYTES]
        for category, pattern in RULES[role]:
            if not pattern.search(clipped):
                continue
            metadata = {}
            if category == "mac_bwp_deferral":
                for key in DIAG_KEYS:
                    m = re.search(r"\b" + key + r"=(-?\d+)\b", clipped)
                    if m:
                        metadata[key] = int(m.group(1))
            if role == "cu" and category in ("cu_rrc_setup_complete","cu_ul_nas"):
                m = re.search(r"\[UL\] \(cellID ([0-9]+), UE ID [0-9]+", clipped)
                if m:
                    metadata["cell_id"] = int(m.group(1))
            if role == "core" and category == "amf_initial_ue_cell":
                m = re.search(r"CellID\[(0x[0-9a-f]+)\]", clipped, re.I)
                if m:
                    metadata["cell_id"] = int(m.group(1), 16)
            out.append((category, metadata))
    return out

class DiagnosticCollector:
    def __init__(self, output):
        self.path = Path(output) / "stage5r-protocol-timeline.json"
        self.lock = threading.Lock()
        self.started = time.monotonic()
        self.events = []
        self.counts = defaultdict(Counter)
        self.emitted = Counter()
        self.procs = []
        self.threads = []
        self.missing_sources = []
        self.limit_reached = False
        self.header_samples_suppressed = Counter()  # expected high-volume header-only SCTP sampling
        self.wire_counts = Counter()
        self.wire_events = []
        self.stopped = False

    def stamp(self):
        return {
            "elapsed_seconds": round(time.monotonic() - self.started, 3),
            "observed_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

    def add(self, role, category, metadata=None):
        with self.lock:
            self.counts[role][category] += 1
            ck = (role, category)
            if len(self.events) >= MAX_EVENTS:
                self.limit_reached = True  # a real bounded-observation gap
                return
            if self.emitted[ck] >= MAX_ROLE_CATEGORY_EVENTS:
                # Stage5V: wire-level packet metadata is counted exhaustively
                # but only the first 100 events per type are displayed.
                # The intentional header sampling cap is NOT missing UE
                # RRC, NAS, BWP, or harness protocol evidence.
                if role == "wire-headers":
                    self.header_samples_suppressed[category] += 1
                else:
                    self.limit_reached = True  # a safety-critical category was capped
                return
            self.emitted[ck] += 1
            self.events.append({"source": role, "event": category,
                                **self.stamp(), **(metadata or {})})

    def consume(self, role, process):
        try:
            cu_selection = CUSelectionObserver() if role == "cu" else None
            for line in process.stdout:
                for key, metadata in classify_lines(role, (line,)):
                    self.add(role, key, metadata)
                # Stage5ZB: category-only, unmodified OAI UE/DU log observation.
                for category in classify_radio_boundary(role, line):
                    self.add(role, category)
                # Stage5ZC: independent bounded, category-only SIB1/BWP0 timeline.
                for category, metadata in classify_sib1_reacquisition(role, line):
                    self.add(role, category, metadata)
                # Stage5ZD: live source-pinned MAC MIB/SIB1 stage markers, no IDs.
                for category, metadata in classify_mib_handoff(role, line):
                    self.add(role, category, metadata)
                # Stage5ZF: pinned RRC's SI validity and SIB1 scheduling decision.
                for category, metadata in classify_si_decision(role, line):
                    self.add(role, category, metadata)
                if cu_selection is not None:
                    # Only allowlisted categories and synthetic cell labels.
                    # Log adjacency is not UE identity or NGAP delivery proof.
                    for key, metadata in cu_selection.process(line):
                        self.add("cu", key, metadata)
        except (OSError, ValueError) as exc:
            with self.lock:
                self.missing_sources.append({"source":role,"error_type":type(exc).__name__})

    def sniff_sctp(self, process):
        try:
            for line in process.stdout:
                if "38412" in line:
                    label = "ngap_n2_sctp_packet"
                elif "38472" in line:
                    label = "f1c_sctp_packet"
                else:
                    label = "other_sctp_packet"
                self.add("wire-headers", label)
        except (OSError, ValueError) as exc:
            with self.lock:
                self.missing_sources.append({"source":"wire-headers","error_type":type(exc).__name__})

    def start(self):
        from run_ue_registration import name
        for role in ("ue", "ue-small", "cu", "macro", "small", "core"):
            try:
                p = subprocess.Popen(
                    ["docker", "logs", "--timestamps", "--follow", "--tail", "0", name(role)],
                    # Docker logs may emit Open5GS AMF messages on STDERR.
                    # Merge into the same allowlist parser; never archive raw text.
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace", bufsize=1)
                self.procs.append(p)
                t = threading.Thread(target=self.consume, args=(role,p), daemon=True)
                t.start()
                self.threads.append(t)
            except (OSError, ValueError) as exc:
                self.missing_sources.append({"source":role,"error_type":type(exc).__name__})
        try:
            p = subprocess.Popen(
                ["sudo", "tcpdump", "-i", "any", "-nn", "-tt", "-l", "-q",
                 "sctp and (port 38412 or port 38472)"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace", bufsize=1)
            self.procs.append(p)
            t = threading.Thread(target=self.sniff_sctp, args=(p,), daemon=True)
            t.start()
            self.threads.append(t)
        except (OSError, ValueError) as exc:
            self.missing_sources.append({"source":"wire-headers","error_type":type(exc).__name__})
        # Stage5Y: Open5GS writes AMF InitialUEMessage and its cell ID
        # on separate lines of files in /tmp/stage4k-log/. The generic
        # docker-logs stream alone does NOT establish per-cell ingress.
        from amf_ingress_stream_stage5y import observe_amf_files
        t = threading.Thread(target=observe_amf_files, args=(self,), daemon=True)
        t.start()
        self.threads.append(t)

    def milestone(self, label, status, more):
        if label not in STAGE_MARKERS:
            return
        meta = {"stage": label, "status": status}
        if isinstance(more.get("cycle"), int):
            meta["cycle"] = more["cycle"]
        if label == "T94-MO-UL-ping-probe":
            meta["attempt"] = int(more.get("attempt", 0))
            ping = more.get("pings", {})
            for role in ("ue", "ue-small"):
                item = ping.get(role, {})
                meta[role + "_icmp_received"] = int(item.get("received", 0))
        self.add("harness", "milestone", meta)

    def append_amf_snapshot(self):
        """Read existing synthetic 5GC AMF logs into memory, store only category counts."""
        from run_ue_registration import name
        cmd = ["docker","exec",name("core"),"/bin/sh","-c",
               'for f in /tmp/stage4k-log/*amf*.log; do if test -f "$f"; then tail -n 650 "$f"; fi; done']
        try:
            p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               text=True, encoding="utf-8",errors="replace",timeout=15)
            if p.returncode:
                self.missing_sources.append({"source":"amf-snapshot","error_type":"NonZeroExit"})
                return
            for category,meta in classify_lines("amf-snapshot",p.stdout.splitlines()):
                self.add("amf-snapshot",category,meta)
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            self.missing_sources.append({"source":"amf-snapshot","error_type":type(exc).__name__})

    def stop(self, verdict):
        if self.stopped: return
        self.stopped = True
        for p in self.procs:
            if p.poll() is None:
                p.terminate()
        for p in self.procs:
            try:
                p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(timeout=3)
        for t in self.threads:
            t.join(timeout=4)
        self.append_amf_snapshot()
        with self.lock:
            report = {
                "schema": "hcran-stage5r-bounded-protocol-observation-v1",
                "scope": "isolated-GitHub-RFsim-observe-only",
                "source": "docker-log-allowlist+SCTP-headers+synthetic-AMF-allowlist",
                "verdict_from_genuine_stage5p_harness": verdict,
                "production_decision": "NO_GO", "production_authorized": False,
                "privacy": "Only synthetic lab cell IDs and allowlisted protocol categories; no IMSI/SUPI/SUCI, NAS payload, RNTI or subscriber credentials saved in this timeline",
                "stage5x_streams": ["cu", "core"],
                "stage5zb_radio_boundary_categories": True,
                "stage5zc_sib1_reacquisition_categories": True,
                "stage5zd_mib_sib1_handoff_categories": True,
                "stage5zf_rrc_si_schedule_decision_categories": True,
                "collection_limit_reached": self.limit_reached,
                "wire_header_sampling": {
                    "per_category_display_cap": MAX_ROLE_CATEGORY_EVENTS,
                    "suppressed_header_only_samples": dict(self.header_samples_suppressed),
                    "all_header_packets_counted": True,
                    "required_ue_events_truncated": self.limit_reached,
                },
                "collection_issues": list(self.missing_sources),
                "category_counts": {role:dict(counts) for role,counts in self.counts.items()},
                "events": sorted(self.events,key=lambda e:e["elapsed_seconds"]),
            }
        self.path.write_text(json.dumps(report, indent=2) + "\n")
        print("STAGE5R: wrote privacy-reduced protocol timeline:",
              json.dumps({"event_count":len(report["events"]),
                          "issues":len(report["collection_issues"]),
                          "verdict":verdict}), flush=True)

def make_probe(args):
    probe = configure(args)
    # Stage5Y ONLY: keep the Stage4P baseline frozen, but avoid its AMF-count
    # stdout+stderr integer parse race when /proc/<pid>/comm disappears.
    # Every original NAS/NGAP/F1-U/NG-U and AMF-ingress gate remains active.
    from run_ue_registration import name
    if not callable(getattr(probe, "amf_count", None)):
        raise RuntimeError("Stage5Y expected baseline AMF process-count gate missing")

    def race_safe_amf_count(self):
        return count_amf_processes(name("core"))

    probe.amf_count = types.MethodType(race_safe_amf_count, probe)
    actual = probe.fault_and_recover
    def instrumented(self):
        collector = DiagnosticCollector(self.out)
        old_record = self.record
        def record(label, status, **more):
            old_record(label,status,**more)
            collector.milestone(label,status,more)
        self.record = record
        collector.start()
        result = "FAIL"
        try:
            actual()
            result = "PASS"
        finally:
            collector.stop(result)
            self.record = old_record
    probe.fault_and_recover = types.MethodType(instrumented,probe)
    return probe

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--baseline",required=True)
    for key in ("rendered","output","cu-image","du-image","ue-image"):
        ap.add_argument("--"+key,required=True)
    ap.add_argument("--cycles",type=int,default=3,choices=[3])
    ap.add_argument("--verify-gtpu",action="store_true")
    opts=ap.parse_args()
    if not opts.verify_gtpu:
        ap.error("FAIL CLOSED: real independent F1-U and NG-U GTP-U capture required")
    return make_probe(opts).run()

if __name__=="__main__":
    raise SystemExit(main())
