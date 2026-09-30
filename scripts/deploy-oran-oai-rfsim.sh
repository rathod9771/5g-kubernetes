#!/usr/bin/env bash
# Phase A live-cluster validation: deploy helm/oran-oai/{cu,du} in RFSIM mode,
# e2.enabled=false. Does NOT touch FlexRIC, E2, xApps, Open5GS, cran-oai, or
# helm/oai. Safe to re-run: gates refuse to proceed if oran-oai objects
# already exist, rather than silently reinstalling over them.
set -u -o pipefail
export KUBECONFIG="${KUBECONFIG:-$HOME/osm-kubeconfig.yaml}"
NS=c63ff4ec-6bd4-46bc-90a2-d45fb0809c2c
REPO="$HOME/5g-kubernetes"
MIN_MB=3000
MAX_LOAD1=20
TS=$(date +%Y%m%d_%H%M%S)
OUT="$HOME/oran_oai_phaseA_evidence_$TS.txt"
F='NR>1 {print "ST="$5, "LPORT="$12, "RPORT="$13, $14, "<->", $16}'

say()  { echo "$@" | tee -a "$OUT"; }
sect() { echo | tee -a "$OUT"; echo "##### $*" | tee -a "$OUT"; }
res()  { printf '%-32s %-6s %s\n' "$1" "$2" "$3" | tee -a "$OUT"; }
avail_mb() { awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo; }
load1() { cut -d' ' -f1 /proc/loadavg; }
sctp() { kubectl exec -n "$NS" "deploy/$1" -- cat /proc/net/sctp/assocs 2>&1 | awk "$F"; }
snapshot() {
  echo "load: $(cut -d' ' -f1-3 /proc/loadavg)  MemAvailable: $(avail_mb) MB"
  echo "srsRAN sctp:"; sctp cran-srsran-cu 2>&1
  echo "srsRAN pods:"; kubectl get pods -n "$NS" -o custom-columns=N:.metadata.name,R:.status.containerStatuses[0].restartCount --no-headers 2>&1 | grep cran-srsran
  echo "open5gs pods (restarts):"; kubectl get pods -n "$NS" -o custom-columns=N:.metadata.name,R:.status.containerStatuses[0].restartCount --no-headers 2>&1 | grep open5gs
  echo "cran-oai pods (restarts):"; kubectl get pods -n "$NS" -o custom-columns=N:.metadata.name,R:.status.containerStatuses[0].restartCount --no-headers 2>&1 | grep cran-oai
}
stop() {
  say "STOP: $*"
  say "Evidence so far: $OUT"
  say "Rollback (removes ONLY the O-RAN OAI pair, nothing else): helm uninstall oran-oai-du oran-oai-cu -n $NS"
  exit 1
}

# ------------------------------------------------------------------ gates
sect "GATES (nothing deployed yet)"

say "1. API reachable"
[ "$(kubectl get --raw=/readyz 2>/dev/null)" = "ok" ] || stop "API server not answering /readyz"
say "   OK"

say "2. namespace exists"
kubectl get ns "$NS" >/dev/null 2>&1 || stop "namespace $NS does not exist"
say "   OK"

say "3. node Ready"
NR=$(kubectl get nodes --no-headers 2>/dev/null | awk '{print $2}' | grep -c '^Ready$')
[ "$NR" -ge 1 ] || stop "no node in Ready state"
kubectl get nodes --no-headers | tee -a "$OUT"

say "4. Open5GS pods healthy (Running, 0 recent restarts assumed if steady-state)"
O5GS_BAD=$(kubectl get pods -n "$NS" --no-headers 2>/dev/null | grep open5gs | awk '$3!="Running"' | wc -l)
kubectl get pods -n "$NS" --no-headers 2>/dev/null | grep open5gs | tee -a "$OUT"
[ "$O5GS_BAD" -eq 0 ] || stop "$O5GS_BAD Open5GS pod(s) not Running"
say "   OK: all Open5GS pods Running"

say "5. srsRAN C-RAN baseline healthy"
read -r CUPH CURS <<<"$(kubectl get pods -n "$NS" -l app=cran-srsran-cu -o jsonpath='{.items[0].status.phase} {.items[0].status.containerStatuses[0].restartCount}' 2>/dev/null)"
read -r DUPH DURS <<<"$(kubectl get pods -n "$NS" -l app=cran-srsran-du -o jsonpath='{.items[0].status.phase} {.items[0].status.containerStatuses[0].restartCount}' 2>/dev/null)"
say "   srsRAN CU: ${CUPH:-?} restarts=${CURS:-?}   srsRAN DU: ${DUPH:-?} restarts=${DURS:-?}"
[ "${CUPH:-x}" = Running ] && [ "${DUPH:-x}" = Running ] || stop "srsRAN C-RAN baseline not Running"
grep -q 'ST=3 LPORT=38472' <<<"$(sctp cran-srsran-cu)" || stop "srsRAN baseline F1 not ESTABLISHED"
say "   OK: srsRAN baseline F1 ESTABLISHED"

say "6. CPU/memory/load acceptable"
A=$(avail_mb); L1=$(load1)
say "   MemAvailable: ${A} MB (need >= ${MIN_MB})   load1: ${L1} (need <= ${MAX_LOAD1})"
[ "$A" -ge "$MIN_MB" ] || stop "low memory"
awk -v a="$L1" -v b="$MAX_LOAD1" 'BEGIN{exit !(a>b)}' && stop "load too high ($L1 > $MAX_LOAD1); let the node settle and re-run"
say "   OK"

say "7. no pre-existing oran-oai-* objects"
n=$(kubectl get deploy,svc,cm,pod -n "$NS" --no-headers 2>/dev/null | grep -c 'oran-oai' || true)
[ "$n" -eq 0 ] || stop "oran-oai objects already exist ($n) - this script does not re-run over an existing deployment; uninstall first if intentional"
helm list -n "$NS" 2>/dev/null | grep -q 'oran-oai' && stop "an oran-oai Helm release already exists"
say "   OK: no pre-existing oran-oai objects"

say "8. cran-oai and helm/oai unaffected by this run (repo check, informational)"
if command -v git >/dev/null 2>&1 && [ -d "$REPO/.git" ]; then
  DIRTY=$(cd "$REPO" && git status --short helm/cran-oai helm/oai 2>/dev/null | wc -l)
  say "   git status on cran-oai + helm/oai: ${DIRTY} changed file(s) (expect 0; this script will not touch them either way)"
fi

say "9. chart validity (lint, both RF modes)"
helm lint "$REPO/helm/oran-oai/cu" >/dev/null 2>&1 || stop "oran-oai/cu lint failed"
helm lint "$REPO/helm/oran-oai/du" -f "$REPO/helm/oran-oai/du/values-rfsim.yaml" >/dev/null 2>&1 || stop "oran-oai/du rfsim lint failed"
say "   OK: both charts lint clean"

say "10. AMF name resolves (same requirement as cran-oai)"
kubectl exec -n "$NS" deploy/cran-srsran-cu -- getent hosts amf-ngap-stable >/dev/null 2>&1 || stop "amf-ngap-stable does not resolve"
say "   OK"

# --------------------------------------------------------------- baseline
sect "BASELINE (before deploy)"
snapshot | tee /tmp/oran_bl_before.txt
APIR0=$(kubectl get pod -n kube-system -l component=kube-apiserver -o jsonpath='{.items[0].status.containerStatuses[0].restartCount}' 2>/dev/null)
say "kube-apiserver restartCount: ${APIR0:-unknown}"
START=$(date -u +%FT%TZ); say "deploy start: $START"

# ---------------------------------------------------------------- CU
sect "DEPLOY CU (helm/oran-oai/cu, e2.enabled=false)"
helm install oran-oai-cu "$REPO/helm/oran-oai/cu" -n "$NS" --set e2.enabled=false 2>&1 | tee -a "$OUT" | head -8
kubectl rollout status deploy/oran-oai-cu -n "$NS" --timeout=150s 2>&1 | tee -a "$OUT" || stop "CU rollout did not complete"

CU_READY=$(kubectl get pods -n "$NS" -l app=oran-oai-cu -o jsonpath='{.items[0].status.containerStatuses[0].ready}' 2>/dev/null)
CU_PHASE=$(kubectl get pods -n "$NS" -l app=oran-oai-cu -o jsonpath='{.items[0].status.phase}' 2>/dev/null)
say "CU pod: phase=${CU_PHASE:-?} ready=${CU_READY:-?}"
[ "$CU_PHASE" = Running ] && [ "$CU_READY" = true ] || stop "CU pod not Running+Ready"

ng=0
for i in $(seq 1 18); do
  L=$(kubectl logs -n "$NS" deploy/oran-oai-cu 2>&1)
  grep -q 'Received NGSetupResponse' <<<"$L" && { ng=1; break; }
  sleep 5
done
CL=$(kubectl logs -n "$NS" deploy/oran-oai-cu 2>&1)
say "$(grep 'AMF_IP' <<<"$CL")"
say "--- CU log tail ---"; tail -15 <<<"$CL" | tee -a "$OUT"
CU_RESTARTS=$(kubectl get pods -n "$NS" -l app=oran-oai-cu -o jsonpath='{.items[0].status.containerStatuses[0].restartCount}')
say "CU restarts: $CU_RESTARTS"
[ "$ng" -eq 1 ]                    || stop "CU never logged NGSetupResponse"
grep -q 'AMF_IP=[0-9]' <<<"$CL"    || stop "CU AMF_IP is empty"
[ "${CU_RESTARTS:-1}" -eq 0 ]      || stop "CU restarted"
say "CU startup gate: PASSED (NGAP up, AMF_IP resolved, 0 restarts)"

CU_MANIFEST=$(helm get manifest oran-oai-cu -n "$NS" 2>&1)
CU_VALUES=$(helm get values oran-oai-cu -n "$NS" 2>&1)

# ---------------------------------------------------------------- DU
sect "DEPLOY DU (helm/oran-oai/du, rf.mode=rfsim, e2.enabled=false)"
helm install oran-oai-du "$REPO/helm/oran-oai/du" -n "$NS" -f "$REPO/helm/oran-oai/du/values-rfsim.yaml" --set e2.enabled=false 2>&1 | tee -a "$OUT" | head -8
kubectl rollout status deploy/oran-oai-du -n "$NS" --timeout=150s 2>&1 | tee -a "$OUT" || stop "DU rollout did not complete"

DU_READY=$(kubectl get pods -n "$NS" -l app=oran-oai-du -o jsonpath='{.items[0].status.containerStatuses[0].ready}' 2>/dev/null)
DU_PHASE=$(kubectl get pods -n "$NS" -l app=oran-oai-du -o jsonpath='{.items[0].status.phase}' 2>/dev/null)
say "DU pod: phase=${DU_PHASE:-?} ready=${DU_READY:-?}"
[ "$DU_PHASE" = Running ] && [ "$DU_READY" = true ] || stop "DU pod not Running+Ready"
sleep 20

DU_MANIFEST=$(helm get manifest oran-oai-du -n "$NS" 2>&1)
DU_VALUES=$(helm get values oran-oai-du -n "$NS" 2>&1)

# --------------------------------------------------------------- evidence
sect "EVIDENCE (raw)"
DL=$(kubectl logs -n "$NS" deploy/oran-oai-du 2>&1)
CL=$(kubectl logs -n "$NS" deploy/oran-oai-cu 2>&1)
say "--- CU: F1/NGAP lines ---"; grep -iE 'f1ap|f1 |DU|Added cell|NGSetup|assert|fatal' <<<"$CL" | tail -14 | tee -a "$OUT"
say "--- DU: init/F1/rfsim/cell lines ---"; grep -iE 'OAI DU|f1ap|f1 |setup|rfsim|cell|assert|fatal|error|listen|server' <<<"$DL" | head -20 | tee -a "$OUT"
say "--- DU raw tail ---"; tail -8 <<<"$DL" | tee -a "$OUT"

CUT=$(sctp oran-oai-cu); DUT=$(sctp oran-oai-du)
say "--- sctp CU ---"; say "$CUT"; say "--- sctp DU ---"; say "$DUT"

CMD=$(kubectl exec -n "$NS" deploy/oran-oai-du -- sh -c "tr '\0' ' ' < /proc/1/cmdline" 2>&1)
LST=$(kubectl exec -n "$NS" deploy/oran-oai-du -- sh -c "grep -i ':0FCB' /proc/net/tcp /proc/net/tcp6" 2>&1)
say "--- DU pid1 ---"; say "$CMD"
say "--- rfsim listener :4043 ---"; say "$LST"

# Real check: does the RENDERED DEPLOYMENT actually declare the usb-bus
# hostPath volume and mount? /dev/bus/usb being visible inside the container
# is NOT proof either way -- privileged: true exposes host devices in every
# mode, with or without an explicit volume. The only thing that distinguishes
# rfsim from usrp is whether the usb-bus volume/mount are IN THE MANIFEST.
USB_VOL_PRESENT=$(grep -c 'name: usb-bus' <<<"$DU_MANIFEST" || true)
USB_MOUNT_PRESENT=$(grep -c 'mountPath: /dev/bus/usb' <<<"$DU_MANIFEST" || true)
say "--- rendered DU manifest: usb-bus volume declarations=$USB_VOL_PRESENT mount declarations=$USB_MOUNT_PRESENT (want 0 for rfsim) ---"

# Real check: distinguish (1) a rendered e2_agent {} config block existing,
# (2) a rendered near_ric_ip_addr line existing, (3) Helm's own recorded
# values, and (4) OAI's own explicit runtime statement -- these are four
# separate facts. A bare `grep e2_agent` on logs cannot tell "no E2 config"
# apart from "OAI logging that E2 is absent" (both contain the substring).
E2_BLOCK_CU=$(grep -c 'e2_agent = {' <<<"$CU_MANIFEST" || true)
E2_BLOCK_DU=$(grep -c 'e2_agent = {' <<<"$DU_MANIFEST" || true)
E2_RIC_ADDR_CU=$(grep -c 'near_ric_ip_addr' <<<"$CU_MANIFEST" || true)
E2_RIC_ADDR_DU=$(grep -c 'near_ric_ip_addr' <<<"$DU_MANIFEST" || true)
E2_VALUES_CU_OK=$(grep -qE 'enabled: false' <<<"$CU_VALUES" && echo 1 || echo 0)
E2_VALUES_DU_OK=$(grep -qE 'enabled: false' <<<"$DU_VALUES" && echo 1 || echo 0)
E2_OAI_DISABLED_STMT=$(grep -c 'E2 agent is DISABLED' <<<"$DL$CL" || true)
E2_ACTUAL_ATTEMPT=$(grep -ciE 'E2 Setup Request|E2SetupRequest|E2AP.*[Ss]etup' <<<"$DL$CL" || true)
say "--- E2 structural facts ---"
say "  rendered e2_agent {} block: CU=$E2_BLOCK_CU DU=$E2_BLOCK_DU (want 0 both)"
say "  rendered near_ric_ip_addr:  CU=$E2_RIC_ADDR_CU DU=$E2_RIC_ADDR_DU (want 0 both)"
say "  helm values e2.enabled=false: CU=$E2_VALUES_CU_OK DU=$E2_VALUES_DU_OK (want 1 both)"
say "  OAI's own \"E2 agent is DISABLED\" statement seen: $E2_OAI_DISABLED_STMT time(s) (want >=1)"
say "  actual E2 Setup Request/attempt seen in logs: $E2_ACTUAL_ATTEMPT (want 0)"

DUCONF=$(kubectl exec -n "$NS" deploy/oran-oai-du -- cat /tmp/conf/du.conf 2>&1)
say "--- DU rendered cell_cfg-equivalent RF params (rfsim block) ---"
grep -A8 'RUs = ' <<<"$DUCONF" | tee -a "$OUT"
grep -A6 'rfsimulator' <<<"$DUCONF" | tee -a "$OUT"

RC=$(kubectl get pods -n "$NS" -l app=oran-oai-cu -o jsonpath='{.items[0].status.containerStatuses[0].restartCount}')
RD=$(kubectl get pods -n "$NS" -l app=oran-oai-du -o jsonpath='{.items[0].status.containerStatuses[0].restartCount}')

# -------------------------------------------------------- post-deploy snap
sect "SNAPSHOT after CU+DU"
snapshot | tee /tmp/oran_bl_after.txt
APIR1=$(kubectl get pod -n kube-system -l component=kube-apiserver -o jsonpath='{.items[0].status.containerStatuses[0].restartCount}' 2>/dev/null)
A2=$(avail_mb); L1_2=$(load1)

# ---------------------------------------------------------------- grading
sect "VALIDATION REPORT"
fails=0
chk() { if [ "$2" = "1" ]; then res "$1" PASS "$3"; else res "$1" FAIL "$3"; fails=$((fails+1)); fi; }

chk "Helm install"                  "$([ "$ng" -eq 1 ] && [ "$DU_PHASE" = Running ] && echo 1 || echo 0)" "both releases installed and rolled out"
chk "CU Running"                    "$([ "$CU_PHASE" = Running ] && [ "$CU_READY" = true ] && [ "${RC:-1}" -eq 0 ] && echo 1 || echo 0)" "phase=$CU_PHASE ready=$CU_READY restarts=$RC"
chk "DU Running"                    "$([ "$DU_PHASE" = Running ] && [ "$DU_READY" = true ] && [ "${RD:-1}" -eq 0 ] && echo 1 || echo 0)" "phase=$DU_PHASE ready=$DU_READY restarts=$RD"
chk "CU<->DU connectivity"          "$(grep -q 'ST=3 LPORT=38472' <<<"$CUT" && grep -q 'ST=3 LPORT=[0-9]* RPORT=38472' <<<"$DUT" && echo 1 || echo 0)" "kernel SCTP ESTABLISHED both ends"
chk "F1 Setup Request (CU log)"     "$(grep -qi 'Received F1 Setup Request' <<<"$CL" && echo 1 || echo 0)" "CU log line"
chk "F1 Setup Response (DU log)"    "$(grep -qi 'received F1 Setup Response' <<<"$DL" && echo 1 || echo 0)" "DU log line"
CELL_CONFIGURED=$(grep -qiE 'Configured DU: cell ID|Configuring Cell [0-9]+ for (TDD|FDD)' <<<"$DL" && echo 1 || echo 0)
CELL_ACTIVATED_WORDING=$(grep -qiE 'Cell was activated|Cell scheduling was activated' <<<"$DL" && echo 1 || echo 0)
CELL_SCHEDULER_LIVE=$(grep -qE 'Frame\.Slot [0-9]+\.[0-9]+' <<<"$DL" && echo 1 || echo 0)
CELL_INDICATORS_MET=$(( CELL_CONFIGURED + CELL_ACTIVATED_WORDING + CELL_SCHEDULER_LIVE ))
chk "Cell activation"               "$([ "$CELL_INDICATORS_MET" -ge 2 ] && echo 1 || echo 0)" "configured=$CELL_CONFIGURED activated-wording=$CELL_ACTIVATED_WORDING scheduler-live=$CELL_SCHEDULER_LIVE (need >=2 of 3 independent indicators, since exact OAI log wording varies by build)"
RFSIMULATOR_BLOCK=$(grep -c 'rfsimulator =' <<<"$DU_MANIFEST" || true)
chk "RFSIM confirmed"               "$(grep -q -- '--rfsim' <<<"$CMD" && grep -qi 'Running as server' <<<"$DL" && grep -q ' 0A ' <<<"$LST" && [ "$RFSIMULATOR_BLOCK" -ge 1 ] && echo 1 || echo 0)" "cmdline has --rfsim, DU logs server mode, :4043 LISTEN, rfsimulator block in rendered manifest ($RFSIMULATOR_BLOCK)"
chk "USB gating correct for rf.mode=rfsim" "$([ "${USB_VOL_PRESENT:-1}" -eq 0 ] && [ "${USB_MOUNT_PRESENT:-1}" -eq 0 ] && echo 1 || echo 0)" "usb-bus volume=$USB_VOL_PRESENT mount=$USB_MOUNT_PRESENT in rendered manifest (want both 0 for rfsim mode)"
chk "Open5GS unaffected"            "$(diff <(grep -A20 'open5gs pods' /tmp/oran_bl_before.txt) <(grep -A20 'open5gs pods' /tmp/oran_bl_after.txt) >/dev/null && echo 1 || echo 0)" "restart counts unchanged"
chk "srsRAN C-RAN unaffected"       "$(diff <(grep -A5 'srsRAN sctp' /tmp/oran_bl_before.txt) <(grep -A5 'srsRAN sctp' /tmp/oran_bl_after.txt) >/dev/null && diff <(grep -A5 'srsRAN pods' /tmp/oran_bl_before.txt) <(grep -A5 'srsRAN pods' /tmp/oran_bl_after.txt) >/dev/null && echo 1 || echo 0)" "F1 table and restart counts unchanged"
chk "cran-oai unaffected"           "$(diff <(grep -A5 'cran-oai pods' /tmp/oran_bl_before.txt) <(grep -A5 'cran-oai pods' /tmp/oran_bl_after.txt) >/dev/null && echo 1 || echo 0)" "restart counts unchanged (if cran-oai was deployed)"
chk "E2 config block absent (manifest)" "$([ "${E2_BLOCK_CU:-1}" -eq 0 ] && [ "${E2_BLOCK_DU:-1}" -eq 0 ] && echo 1 || echo 0)" "e2_agent {} blocks: CU=$E2_BLOCK_CU DU=$E2_BLOCK_DU"
chk "near_ric_ip_addr absent (manifest)" "$([ "${E2_RIC_ADDR_CU:-1}" -eq 0 ] && [ "${E2_RIC_ADDR_DU:-1}" -eq 0 ] && echo 1 || echo 0)" "near_ric_ip_addr lines: CU=$E2_RIC_ADDR_CU DU=$E2_RIC_ADDR_DU"
chk "Helm values confirm e2.enabled=false" "$([ "${E2_VALUES_CU_OK:-0}" -eq 1 ] && [ "${E2_VALUES_DU_OK:-0}" -eq 1 ] && echo 1 || echo 0)" "helm get values: CU=$E2_VALUES_CU_OK DU=$E2_VALUES_DU_OK"
chk "OAI reports E2 agent DISABLED"  "$([ "${E2_OAI_DISABLED_STMT:-0}" -ge 1 ] && echo 1 || echo 0)" "seen $E2_OAI_DISABLED_STMT time(s) in logs"
chk "No actual E2 Setup attempt"    "$([ "${E2_ACTUAL_ATTEMPT:-1}" -eq 0 ] && echo 1 || echo 0)" "E2 Setup Request/attempt lines found: $E2_ACTUAL_ATTEMPT"

say
say "--- CPU/memory/load impact ---"
say "MemAvailable: before=${A} MB  after=${A2} MB  (delta $((A2-A)) MB)"
say "load1: before=${L1} after=${L1_2}"
say "kube-apiserver restartCount: before=${APIR0:-?} after=${APIR1:-?}"

say
say "IMPORTANT: 'Running'/'Ready' above means the pod/process is alive."
say "F1 Setup Request/Response and Cell activation lines above are the actual"
say "protocol-level proof that O-CU and O-DU are functioning, not just running."
say
if [ "$fails" -eq 0 ]; then
  say "PHASE A VERDICT: all graded checks PASS -- O-RAN OAI CU/DU/F1 baseline proven in RFSIM mode"
else
  say "PHASE A VERDICT: $fails check(s) FAILED -- diagnose before proceeding to Phase B (FlexRIC/E2)"
fi
say "E2 was NOT enabled. FlexRIC was NOT started. No xApp was introduced."
say "Evidence file: $OUT"
