#!/usr/bin/env bash
# Phase B: enable E2 on the already-proven oran-oai CU/DU and prove the
# actual E2 Setup exchange with FlexRIC (RAN-side <-> RIC-side, both ends).
#
# Scope boundary: proves ONLY O-RAN OAI CU/DU -> E2 -> FlexRIC. Does NOT
# proceed to KPM subscription, xApp deployment, RC control, O1, Open
# Fronthaul, or USRP validation -- those are separate milestones and this
# script stops before any of them.
#
# Never touches: RF parameters, F1 parameters, Open5GS, cell configuration,
# FlexRIC's own deployment, cran-oai, helm/oai, srsRAN C-RAN.
set -u -o pipefail
export KUBECONFIG="${KUBECONFIG:-$HOME/osm-kubeconfig.yaml}"
NS=c63ff4ec-6bd4-46bc-90a2-d45fb0809c2c
REPO="$HOME/5g-kubernetes"
MIN_MB=3000
MAX_LOAD1=20
# OAI's E2 agent (e2ap_init_ep_agent) hardcodes: assert(strlen(addr) < 16).
# That is sized for an IPv4 dotted-quad, not a DNS name -- confirmed live:
# the FQDN form (flexric.<ns>.svc.cluster.local, 63 chars) crashed the CU
# with exactly this assertion. Every real, working OAI+FlexRIC example
# found uses a plain IPv4 address for near_ric_ip_addr, never DNS. Using
# the Service's own ClusterIP here: a real IP, well under the limit, and
# stable (the Service is not recreated by this or any other script).
RIC_ADDR="10.107.153.48"
TS=$(date +%Y%m%d_%H%M%S)
OUT="$HOME/oran_oai_phaseB_evidence_$TS.txt"
F='NR>1 {print "ST="$5, "LPORT="$12, "RPORT="$13, $14, "<->", $16}'

say()  { echo "$@" | tee -a "$OUT"; }
sect() { echo | tee -a "$OUT"; echo "##### $*" | tee -a "$OUT"; }
res()  { printf '%-38s %-6s %s\n' "$1" "$2" "$3" | tee -a "$OUT"; }
avail_mb() { awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo; }
load1() { cut -d' ' -f1 /proc/loadavg; }
sctp() { kubectl exec -n "$NS" "deploy/$1" -- cat /proc/net/sctp/assocs 2>&1 | awk "$F"; }
apiserver_restarts() { kubectl get pod -n kube-system -l component=kube-apiserver -o jsonpath='{.items[0].status.containerStatuses[0].restartCount}' 2>/dev/null; }
snapshot() {
  echo "load: $(cut -d' ' -f1-3 /proc/loadavg)  MemAvailable: $(avail_mb) MB"
  echo "oran-oai CU sctp:"; sctp oran-oai-cu 2>&1
  echo "oran-oai DU sctp:"; sctp oran-oai-du 2>&1
  echo "pod restarts:"; kubectl get pods -n "$NS" -o custom-columns=N:.metadata.name,R:.status.containerStatuses[0].restartCount --no-headers 2>&1 | grep -E 'oran-oai|open5gs|cran-srsran|cran-oai'
}
stop() {
  say "STOP: $*"
  say "Evidence so far: $OUT"
  say "No Helm upgrade was performed past this point. E2 was NOT enabled if this STOP occurred before section 5."
  say "If this STOP occurred after the upgrade, rollback (deterministic, uses the values saved this run):"
  say "  helm upgrade oran-oai-cu $REPO/helm/oran-oai/cu -n $NS --set e2.enabled=false -f /tmp/oranB_cu_values_before_$TS.yaml 2>/dev/null || helm upgrade oran-oai-cu $REPO/helm/oran-oai/cu -n $NS --set e2.enabled=false"
  say "  helm upgrade oran-oai-du $REPO/helm/oran-oai/du -n $NS -f $REPO/helm/oran-oai/du/values-rfsim.yaml --set e2.enabled=false"
  exit 1
}

sect "PHASE B: enable E2 on oran-oai, prove E2 Setup with FlexRIC"
say "RIC address to be used: $RIC_ADDR"
say "Evidence file: $OUT"

# ================================================================ SECTION 1
sect "1. PRE-CHANGE SAFETY GATE (nothing modified yet)"

say "1a. API reachable"
[ "$(kubectl get --raw=/readyz 2>/dev/null)" = "ok" ] || stop "API server not answering /readyz"
say "    OK"

say "1b. current Helm values, oran-oai-cu"
CU_VALUES_BEFORE=$(helm get values oran-oai-cu -n "$NS" 2>&1)
say "$CU_VALUES_BEFORE"
# Strip Helm's "USER-SUPPLIED VALUES:" header so this is a valid, directly
# reusable YAML file for a later `helm upgrade -f`.
grep -v '^USER-SUPPLIED VALUES:$' <<<"$CU_VALUES_BEFORE" > "/tmp/oranB_cu_values_before_$TS.yaml"

say "1c. current Helm values, oran-oai-du"
DU_VALUES_BEFORE=$(helm get values oran-oai-du -n "$NS" 2>&1)
say "$DU_VALUES_BEFORE"
grep -v '^USER-SUPPLIED VALUES:$' <<<"$DU_VALUES_BEFORE" > "/tmp/oranB_du_values_before_$TS.yaml"

say "1d. verify BOTH releases currently show e2.enabled=false (stop if either is already enabled)"
grep -qE 'enabled: true' <<<"$CU_VALUES_BEFORE" && stop "oran-oai-cu already has e2.enabled=true -- refusing to assume state, stopping"
grep -qE 'enabled: true' <<<"$DU_VALUES_BEFORE" && stop "oran-oai-du already has e2.enabled=true -- refusing to assume state, stopping"
say "    OK: both releases confirmed e2.enabled=false (or unset, which the chart defaults to false)"

say "1e. CU/DU pod name/status/restarts"
CU_POD=$(kubectl get pods -n "$NS" -l app=oran-oai-cu -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
DU_POD=$(kubectl get pods -n "$NS" -l app=oran-oai-du -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
read -r CU_PHASE0 CU_RESTARTS0 <<<"$(kubectl get pods -n "$NS" -l app=oran-oai-cu -o jsonpath='{.items[0].status.phase} {.items[0].status.containerStatuses[0].restartCount}' 2>/dev/null)"
read -r DU_PHASE0 DU_RESTARTS0 <<<"$(kubectl get pods -n "$NS" -l app=oran-oai-du -o jsonpath='{.items[0].status.phase} {.items[0].status.containerStatuses[0].restartCount}' 2>/dev/null)"
say "    CU: pod=$CU_POD phase=${CU_PHASE0:-?} restarts=${CU_RESTARTS0:-?}"
say "    DU: pod=$DU_POD phase=${DU_PHASE0:-?} restarts=${DU_RESTARTS0:-?}"
[ "${CU_PHASE0:-x}" = Running ] && [ "${DU_PHASE0:-x}" = Running ] || stop "oran-oai CU/DU not both Running before this change"

say "1f. F1 / NGAP state"
CUT0=$(sctp oran-oai-cu); DUT0=$(sctp oran-oai-du)
say "    CU sctp: $CUT0"
say "    DU sctp: $DUT0"
grep -q 'ST=3 LPORT=38472' <<<"$CUT0" || stop "F1-C not ESTABLISHED before this change"
grep -q 'ST=3 LPORT=[0-9]* RPORT=38412' <<<"$CUT0" || stop "NGAP not ESTABLISHED before this change"
say "    OK: F1-C and NGAP both ESTABLISHED"

say "1g. cell/scheduler state"
DL0=$(kubectl logs -n "$NS" deploy/oran-oai-du 2>&1)
CELL_OK0=$(grep -qE 'Configured DU: cell ID|Frame\.Slot [0-9]+\.[0-9]+' <<<"$DL0" && echo 1 || echo 0)
say "    cell/scheduler evidence present: $CELL_OK0"
[ "$CELL_OK0" -eq 1 ] || stop "no cell/scheduler evidence before this change"

say "1h. Open5GS health"
O5GS_BAD=$(kubectl get pods -n "$NS" --no-headers 2>/dev/null | grep open5gs | awk '$3!="Running"' | wc -l)
[ "$O5GS_BAD" -eq 0 ] || stop "$O5GS_BAD Open5GS pod(s) not Running"
say "    OK"

say "1i. srsRAN C-RAN health"
read -r SRS_CUPH SRS_CURS <<<"$(kubectl get pods -n "$NS" -l app=cran-srsran-cu -o jsonpath='{.items[0].status.phase} {.items[0].status.containerStatuses[0].restartCount}' 2>/dev/null)"
say "    srsRAN CU: ${SRS_CUPH:-?} restarts=${SRS_CURS:-?}"
[ "${SRS_CUPH:-x}" = Running ] || stop "srsRAN C-RAN baseline not Running"
say "    OK"

say "1j. cran-oai health (if deployed)"
CRAN_OAI_PRESENT=$(helm list -n "$NS" 2>/dev/null | grep -c 'cran-oai-cu' || true)
if [ "${CRAN_OAI_PRESENT:-0}" -ge 1 ]; then
  read -r CO_CUPH CO_CURS <<<"$(kubectl get pods -n "$NS" -l app=cran-oai-cu -o jsonpath='{.items[0].status.phase} {.items[0].status.containerStatuses[0].restartCount}' 2>/dev/null)"
  say "    cran-oai-cu: ${CO_CUPH:-?} restarts=${CO_CURS:-?}"
else
  say "    cran-oai not deployed, skipping (nothing to compare against later)"
fi

say "1k. CPU/memory/load"
A0=$(avail_mb); L0=$(load1)
say "    MemAvailable: ${A0} MB (need >= ${MIN_MB})   load1: ${L0} (need <= ${MAX_LOAD1})"
[ "$A0" -ge "$MIN_MB" ] || stop "low memory"
awk -v a="$L0" -v b="$MAX_LOAD1" 'BEGIN{exit !(a>b)}' && stop "load too high ($L0 > $MAX_LOAD1)"
say "    OK"

say "1l. baseline snapshot saved"
snapshot > /tmp/oranB_snapshot_before.txt

# ================================================================ SECTION 2
sect "2. VALIDATE FLEXRIC (read-only; FlexRIC is not modified by this script)"

say "2a. flexric Deployment Ready"
FLEXRIC_READY=$(kubectl get deploy flexric -n "$NS" -o jsonpath='{.status.readyReplicas}' 2>/dev/null)
say "    readyReplicas=${FLEXRIC_READY:-0}"
[ "${FLEXRIC_READY:-0}" -ge 1 ] || stop "flexric Deployment not Ready (is Phase B's prerequisite FlexRIC deployment actually up?)"

say "2b. flexric Service exists"
kubectl get svc flexric -n "$NS" >/dev/null 2>&1 || stop "flexric Service does not exist"
say "    OK"

say "2c. Service endpoints exist"
FLEXRIC_EP=$(kubectl get endpoints flexric -n "$NS" -o jsonpath='{.subsets[0].addresses[0].ip}' 2>/dev/null)
[ -n "$FLEXRIC_EP" ] || stop "flexric Service has no endpoints (pod may be Ready but not actually backing the Service)"
say "    endpoint IP: $FLEXRIC_EP"

say "2d/2e. SCTP ports 36421 and 36422 exposed on the Service"
SVC_PORTS=$(kubectl get svc flexric -n "$NS" -o jsonpath='{.spec.ports[*].port}')
say "    Service ports: $SVC_PORTS"
grep -qw 36421 <<<"$SVC_PORTS" || stop "port 36421 (E2) not exposed on the flexric Service"
grep -qw 36422 <<<"$SVC_PORTS" || stop "port 36422 (E42/xApp) not exposed on the flexric Service"
say "    OK: both ports present"

say "2f. FlexRIC configuration exists in the pod"
FLEXRIC_CONF=$(kubectl exec -n "$NS" deploy/flexric -- cat /usr/local/etc/flexric/flexric.conf 2>&1)
say "    $FLEXRIC_CONF"
[ -n "$FLEXRIC_CONF" ] || stop "flexric.conf missing or empty inside the pod"

say "2g. NEAR_RIC_IP correctly configured"
grep -q 'NEAR_RIC_IP' <<<"$FLEXRIC_CONF" || stop "NEAR_RIC_IP not present in flexric.conf"
say "    OK"

say "2h. RIC is listening on 0.0.0.0, not merely 127.0.0.1 (kernel state, not config claim)"
FLEXRIC_EPS=$(kubectl exec -n "$NS" deploy/flexric -- cat /proc/net/sctp/eps 2>&1)
say "    $FLEXRIC_EPS"
grep -qE '36421.*0\.0\.0\.0|0\.0\.0\.0.*36421' <<<"$FLEXRIC_EPS" || \
  grep -q '36421' <<<"$FLEXRIC_EPS" && grep -q '0\.0\.0\.0' <<<"$FLEXRIC_EPS" || \
  stop "FlexRIC does not appear to be listening on 0.0.0.0:36421 -- check flexric.conf NEAR_RIC_IP"
say "    OK: RIC listening on 0.0.0.0 (reachable from other pods, not loopback-only)"

say "2i. DNS resolution of the RIC address from within the namespace"
DNS_CHECK=$(kubectl exec -n "$NS" deploy/oran-oai-cu -- getent hosts "$RIC_ADDR" 2>&1)
say "    getent hosts $RIC_ADDR -> $DNS_CHECK"
echo "$DNS_CHECK" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' || stop "$RIC_ADDR does not resolve from the CU pod -- do not proceed on an unverified assumption"
say "    OK: $RIC_ADDR resolves"

# ================================================================ SECTION 3
sect "3. VALIDATE E2 RENDERING BEFORE APPLYING (dry-run via helm template)"

say "3(pre). RIC address length check (OAI's E2 agent hardcodes: assert(strlen(addr) < 16))"
RIC_ADDR_LEN=${#RIC_ADDR}
say "    ricAddress: \"$RIC_ADDR\" (length $RIC_ADDR_LEN, must be < 16)"
if [ "$RIC_ADDR_LEN" -ge 16 ]; then
  stop "e2.ricAddress (\"$RIC_ADDR\", $RIC_ADDR_LEN chars) is >= 16 characters. OAI's E2 agent (e2ap_init_ep_agent) asserts strlen(addr) < 16 -- this is sized for a plain IPv4 dotted-quad (max 15 chars: 255.255.255.255), NOT a DNS name or FQDN. A DNS/FQDN value like a Kubernetes Service's *.svc.cluster.local name (typically 40-70+ chars) WILL crash the CU/DU on startup. Use the Service's ClusterIP (a real IPv4 address) instead. This was confirmed live: the FQDN form crashed the CU with exactly this assertion."
fi
say "    OK: address length acceptable for OAI's E2 agent"

say "3a. render CU with e2.enabled=true, e2.ricAddress=$RIC_ADDR"
CU_RENDER_E2=$(helm template t "$REPO/helm/oran-oai/cu" --set e2.enabled=true --set e2.ricAddress="$RIC_ADDR" 2>&1)
CU_E2_BLOCK=$(grep -c 'e2_agent = {' <<<"$CU_RENDER_E2" || true)
CU_RIC_ADDR_RENDERED=$(grep -oE 'near_ric_ip_addr = "[^"]*"' <<<"$CU_RENDER_E2" || true)
say "    e2_agent {} blocks rendered: $CU_E2_BLOCK (want 1)"
say "    near_ric_ip_addr rendered:   $CU_RIC_ADDR_RENDERED"
[ "${CU_E2_BLOCK:-0}" -ge 1 ] || stop "CU render with e2.enabled=true does not contain an e2_agent {} block"
grep -q "$RIC_ADDR" <<<"$CU_RIC_ADDR_RENDERED" || stop "CU render's near_ric_ip_addr does not contain the intended RIC address ($RIC_ADDR)"
say "    OK: CU E2 rendering correct"

say "3b. render DU with e2.enabled=true, e2.ricAddress=$RIC_ADDR (rfsim mode, unchanged RF params)"
DU_RENDER_E2=$(helm template t "$REPO/helm/oran-oai/du" -f "$REPO/helm/oran-oai/du/values-rfsim.yaml" --set e2.enabled=true --set e2.ricAddress="$RIC_ADDR" 2>&1)
DU_E2_BLOCK=$(grep -c 'e2_agent = {' <<<"$DU_RENDER_E2" || true)
DU_RIC_ADDR_RENDERED=$(grep -oE 'near_ric_ip_addr = "[^"]*"' <<<"$DU_RENDER_E2" || true)
say "    e2_agent {} blocks rendered: $DU_E2_BLOCK (want 1)"
say "    near_ric_ip_addr rendered:   $DU_RIC_ADDR_RENDERED"
[ "${DU_E2_BLOCK:-0}" -ge 1 ] || stop "DU render with e2.enabled=true does not contain an e2_agent {} block"
grep -q "$RIC_ADDR" <<<"$DU_RIC_ADDR_RENDERED" || stop "DU render's near_ric_ip_addr does not contain the intended RIC address ($RIC_ADDR)"

say "3c. confirm RF/F1/cell parameters UNCHANGED by adding e2 (diff against the already-proven Phase A rfsim render)"
DU_RENDER_NOE2=$(helm template t "$REPO/helm/oran-oai/du" -f "$REPO/helm/oran-oai/du/values-rfsim.yaml" 2>&1)
# Remove the WHOLE e2_agent {...} block by line RANGE (not by filtering
# individual keyword lines) so its closing "};" doesn't leak through and
# register as a spurious one-sided diff. Also strip the checksum/config
# annotation, which legitimately changes because the values changed.
strip_e2_block() { sed -e '/e2_agent = {/,/^\s*};\s*$/d' -e '/checksum\/config/d' <<<"$1"; }
NONE2_DIFF_LINES=$(diff <(strip_e2_block "$DU_RENDER_NOE2") <(strip_e2_block "$DU_RENDER_E2") | wc -l)
say "    lines differing outside the e2_agent block: $NONE2_DIFF_LINES (want 0)"
[ "$NONE2_DIFF_LINES" -eq 0 ] || stop "enabling e2 changed something other than the e2_agent block -- refusing to proceed, inspect the diff manually"
say "    OK: only the e2_agent block differs; RF/F1/cell config confirmed unchanged"

# ================================================================ SECTION 4
sect "4. ROLLBACK STATE (already saved in section 1; confirming here)"
say "CU pre-change values saved: /tmp/oranB_cu_values_before_$TS.yaml"
say "DU pre-change values saved: /tmp/oranB_du_values_before_$TS.yaml"
[ -s "/tmp/oranB_cu_values_before_$TS.yaml" ] || stop "CU rollback file missing/empty -- refusing to proceed without a known-good rollback state"
[ -s "/tmp/oranB_du_values_before_$TS.yaml" ] || stop "DU rollback file missing/empty -- refusing to proceed without a known-good rollback state"
say "OK: rollback state present. Releases are NOT deleted at any point; rollback is always a helm upgrade back to e2.enabled=false."

echo
read -r -p "All pre-change gates passed. Enable E2 on oran-oai-cu and oran-oai-du now? [y/N] " ans
[ "$ans" = "y" ] || stop "not enabled (your choice)"

# ================================================================ SECTION 5
sect "5. ENABLE E2 (helm upgrade, values only -- chart source untouched)"

say "5a. upgrade oran-oai-cu (using the exact saved pre-change values, with e2 overridden)"
helm upgrade oran-oai-cu "$REPO/helm/oran-oai/cu" -n "$NS" \
  -f "/tmp/oranB_cu_values_before_$TS.yaml" \
  --set e2.enabled=true --set e2.ricAddress="$RIC_ADDR" 2>&1 | tee -a "$OUT" | head -8

say "5b. upgrade oran-oai-du (using the exact saved pre-change values, with e2 overridden)"
helm upgrade oran-oai-du "$REPO/helm/oran-oai/du" -n "$NS" \
  -f "/tmp/oranB_du_values_before_$TS.yaml" \
  --set e2.enabled=true --set e2.ricAddress="$RIC_ADDR" 2>&1 | tee -a "$OUT" | head -8

# ================================================================ SECTION 6
sect "6. VALIDATE STARTUP AFTER THE UPGRADE"

say "6a. wait for CU rollout"
kubectl rollout status deploy/oran-oai-cu -n "$NS" --timeout=150s 2>&1 | tee -a "$OUT" || stop "CU rollout did not complete after enabling E2"

say "6b. wait for DU rollout"
kubectl rollout status deploy/oran-oai-du -n "$NS" --timeout=150s 2>&1 | tee -a "$OUT" || stop "DU rollout did not complete after enabling E2"

say "6c. both Ready"
CU_READY1=$(kubectl get pods -n "$NS" -l app=oran-oai-cu -o jsonpath='{.items[0].status.containerStatuses[0].ready}' 2>/dev/null)
DU_READY1=$(kubectl get pods -n "$NS" -l app=oran-oai-du -o jsonpath='{.items[0].status.containerStatuses[0].ready}' 2>/dev/null)
say "    CU ready=$CU_READY1   DU ready=$DU_READY1"
[ "$CU_READY1" = true ] && [ "$DU_READY1" = true ] || stop "CU or DU not Ready after enabling E2"

say "6d. restart counts"
CU_RESTARTS1=$(kubectl get pods -n "$NS" -l app=oran-oai-cu -o jsonpath='{.items[0].status.containerStatuses[0].restartCount}' 2>/dev/null)
DU_RESTARTS1=$(kubectl get pods -n "$NS" -l app=oran-oai-du -o jsonpath='{.items[0].status.containerStatuses[0].restartCount}' 2>/dev/null)
say "    CU restarts=$CU_RESTARTS1 (was $CU_RESTARTS0)   DU restarts=$DU_RESTARTS1 (was $DU_RESTARTS0)"

say "6e. verify E2 config actually reached the RUNNING containers (not just Helm values -- the live rendered config)"
CU_LIVE_CONF=$(kubectl exec -n "$NS" deploy/oran-oai-cu -- cat /tmp/conf/cu.conf 2>&1)
DU_LIVE_CONF=$(kubectl exec -n "$NS" deploy/oran-oai-du -- cat /tmp/conf/du.conf 2>&1)
CU_LIVE_E2=$(grep -A3 'e2_agent' <<<"$CU_LIVE_CONF")
DU_LIVE_E2=$(grep -A3 'e2_agent' <<<"$DU_LIVE_CONF")
say "    CU live rendered config, e2_agent block: $CU_LIVE_E2"
say "    DU live rendered config, e2_agent block: $DU_LIVE_E2"
[ -n "$CU_LIVE_E2" ] || stop "e2_agent block not found in the CU's LIVE running config (Helm values say enabled, but the container never got it)"
[ -n "$DU_LIVE_E2" ] || stop "e2_agent block not found in the DU's LIVE running config (Helm values say enabled, but the container never got it)"
grep -q "$RIC_ADDR" <<<"$CU_LIVE_E2" || stop "CU's live config does not contain the intended RIC address"
grep -q "$RIC_ADDR" <<<"$DU_LIVE_E2" || stop "DU's live config does not contain the intended RIC address"
say "    OK: E2 config confirmed present in the ACTUALLY RUNNING containers, not just Helm's record of intent"

say "6f. verify the running DU command line (not just config content)"
DU_CMD=$(kubectl exec -n "$NS" deploy/oran-oai-du -- sh -c "tr '\0' ' ' < /proc/1/cmdline" 2>&1)
say "    $DU_CMD"
grep -q -- '--rfsim' <<<"$DU_CMD" || stop "DU no longer running with --rfsim after enabling E2 (RF mode regression)"
say "    OK: --rfsim still present, RF mode unchanged by enabling E2"

# ================================================================ SECTION 7
sect "7. PROVE E2 (the actual E2 Setup exchange, both sides)"
say "Waiting up to 60s for the E2 association to form..."
e2_up=0
for i in $(seq 1 12); do
  RIC_ASSOCS_WAIT=$(awk "$F" <<<"$(kubectl exec -n "$NS" deploy/flexric -- cat /proc/net/sctp/assocs 2>&1)")
  grep -q 'ST=3' <<<"$RIC_ASSOCS_WAIT" && { e2_up=1; break; }
  sleep 5
done

say "--- RIC-side: /proc/net/sctp/assocs (formatted) ---"
RIC_ASSOCS_RAW=$(kubectl exec -n "$NS" deploy/flexric -- cat /proc/net/sctp/assocs 2>&1)
RIC_ASSOCS_FINAL=$(awk "$F" <<<"$RIC_ASSOCS_RAW")
say "$RIC_ASSOCS_FINAL"
[ -z "$RIC_ASSOCS_FINAL" ] && say "(raw, for reference since formatting produced nothing): $RIC_ASSOCS_RAW"

say "--- CU-side: /proc/net/sctp/assocs (want a NEW association to FlexRIC, in addition to F1 and NGAP) ---"
CUT1=$(sctp oran-oai-cu)
say "$CUT1"

say "--- DU-side: /proc/net/sctp/assocs ---"
DUT1=$(sctp oran-oai-du)
say "$DUT1"

say "--- RIC logs (full, since a real E2 Setup should now produce output where startup alone did not) ---"
RIC_LOGS=$(kubectl logs -n "$NS" deploy/flexric 2>&1)
say "$RIC_LOGS"

say "--- CU logs: E2-related lines ---"
CL1=$(kubectl logs -n "$NS" deploy/oran-oai-cu 2>&1)
CU_E2_LINES=$(grep -iE 'E2AP|E2 Setup|E2SetupRequest|E2SetupResponse|near_ric|e2_agent' <<<"$CL1")
say "$CU_E2_LINES"

say "--- DU logs: E2-related lines ---"
DL1=$(kubectl logs -n "$NS" deploy/oran-oai-du 2>&1)
DU_E2_LINES=$(grep -iE 'E2AP|E2 Setup|E2SetupRequest|E2SetupResponse|near_ric|e2_agent' <<<"$DL1")
say "$DU_E2_LINES"

# Structural facts, not indirect inference
E2_SCTP_ASSOC=$(grep -c 'ST=3' <<<"$RIC_ASSOCS_FINAL" || true)
E2_SETUP_REQ_SEEN=$(grep -ciE 'E2 Setup Request|E2SetupRequest|Sending E2 Setup Request|E2AP.*SETUP-REQUEST' <<<"$CU_E2_LINES$DU_E2_LINES$RIC_LOGS" || true)
E2_SETUP_RESP_SEEN=$(grep -ciE 'E2 Setup Response|E2SetupResponse|Received E2 Setup Response|E2AP.*SETUP-RESPONSE|SETUP-RESP' <<<"$CU_E2_LINES$DU_E2_LINES$RIC_LOGS" || true)
DU_STILL_SAYS_DISABLED=$(grep -c 'E2 agent is DISABLED' <<<"$DL1" || true)

say
say "--- E2 structural facts ---"
say "  RIC-side ESTABLISHED (ST=3) associations: $E2_SCTP_ASSOC"
say "  E2 Setup Request evidence (either side):  $E2_SETUP_REQ_SEEN"
say "  E2 Setup Response evidence (either side): $E2_SETUP_RESP_SEEN"
say "  DU still logs 'E2 agent is DISABLED':     $DU_STILL_SAYS_DISABLED (want 0 -- Phase A showed this was 1 when e2.enabled=false)"

# ================================================================ SECTION 8
sect "8. REGRESSION GATES (Phase A functionality must remain healthy)"

REG_F1_REQ=$(grep -qi 'Received F1 Setup Request' <<<"$CL1" && echo 1 || echo 0)
REG_F1_RESP=$(grep -qi 'received F1 Setup Response' <<<"$DL1" && echo 1 || echo 0)
CELL_CONFIGURED1=$(grep -qiE 'Configured DU: cell ID|Configuring Cell [0-9]+ for (TDD|FDD)' <<<"$DL1" && echo 1 || echo 0)
CELL_SCHEDULER_LIVE1=$(grep -qE 'Frame\.Slot [0-9]+\.[0-9]+' <<<"$DL1" && echo 1 || echo 0)
REG_CELL=$(( CELL_CONFIGURED1 + CELL_SCHEDULER_LIVE1 ))
REG_NGAP=$(grep -q 'ST=3 LPORT=[0-9]* RPORT=38412' <<<"$CUT1" && echo 1 || echo 0)
O5GS_BAD1=$(kubectl get pods -n "$NS" --no-headers 2>/dev/null | grep open5gs | awk '$3!="Running"' | wc -l)

say "F1 Setup Request  still present: $REG_F1_REQ"
say "F1 Setup Response still present: $REG_F1_RESP"
say "cell/scheduler indicators met:   $REG_CELL (need >=1 of 2)"
say "NGAP still ESTABLISHED:          $REG_NGAP"
say "Open5GS pods not Running:        $O5GS_BAD1 (want 0)"

say
say "--- final srsRAN / cran-oai baseline diff ---"
snapshot > /tmp/oranB_snapshot_after.txt
diff /tmp/oranB_snapshot_before.txt /tmp/oranB_snapshot_after.txt | grep -v 'load:\|MemAvailable' | tee -a "$OUT"
BASELINE_DIFF_LINES=$(diff /tmp/oranB_snapshot_before.txt /tmp/oranB_snapshot_after.txt | grep -vc 'load:\|MemAvailable\|^[0-9]')

# ================================================================ SECTION 9
sect "9. RESOURCE COMPARISON"
A1=$(avail_mb); L1_now=$(load1)
APIR_AFTER=$(apiserver_restarts)
say "MemAvailable: before=${A0} MB  after=${A1} MB  (delta $((A1-A0)) MB)"
say "load1: before=${L0} after=${L1_now}"

# ================================================================ FINAL REPORT
sect "PHASE B VALIDATION REPORT"
fails=0
chk() { if [ "$2" = "1" ]; then res "$1" PASS "$3"; else res "$1" FAIL "$3"; fails=$((fails+1)); fi; }

chk "FlexRIC prerequisite healthy"       "1" "validated in section 2 (script would have stopped otherwise)"
chk "E2 config rendered correctly (dry-run)" "1" "validated in section 3 (script would have stopped otherwise)"
chk "Helm upgrade completed"             "$([ "$CU_READY1" = true ] && [ "$DU_READY1" = true ] && echo 1 || echo 0)" "both rollouts completed, both Ready"
chk "E2 config reached running containers" "1" "validated in section 6e (script would have stopped otherwise)"
chk "RF mode unchanged (--rfsim)"        "$(grep -q -- '--rfsim' <<<"$DU_CMD" && echo 1 || echo 0)" "confirmed in running cmdline"
chk "RIC-side SCTP association"          "$([ "${E2_SCTP_ASSOC:-0}" -ge 1 ] && echo 1 || echo 0)" "$E2_SCTP_ASSOC ESTABLISHED association(s) on the RIC"
chk "E2 Setup Request evidence"          "$([ "${E2_SETUP_REQ_SEEN:-0}" -ge 1 ] && echo 1 || echo 0)" "seen $E2_SETUP_REQ_SEEN time(s)"
chk "E2 Setup Response evidence"         "$([ "${E2_SETUP_RESP_SEEN:-0}" -ge 1 ] && echo 1 || echo 0)" "seen $E2_SETUP_RESP_SEEN time(s)"
chk "DU E2 agent actually initialized"   "$([ "${DU_STILL_SAYS_DISABLED:-1}" -eq 0 ] && echo 1 || echo 0)" "'E2 agent is DISABLED' no longer logged (was logged when e2.enabled=false)"
say
say "--- regression checks (Phase A functionality) ---"
chk "F1 Setup Request (regression)"      "$REG_F1_REQ" "unchanged from Phase A"
chk "F1 Setup Response (regression)"     "$REG_F1_RESP" "unchanged from Phase A"
chk "Cell/scheduler activity (regression)" "$([ "$REG_CELL" -ge 1 ] && echo 1 || echo 0)" "configured=$CELL_CONFIGURED1 scheduler-live=$CELL_SCHEDULER_LIVE1"
chk "NGAP (regression)"                  "$REG_NGAP" "still ESTABLISHED"
chk "Open5GS unaffected"                 "$([ "$O5GS_BAD1" -eq 0 ] && echo 1 || echo 0)" "$O5GS_BAD1 pod(s) not Running"
chk "CU/DU restarts still 0"             "$([ "${CU_RESTARTS1:-1}" -eq 0 ] && [ "${DU_RESTARTS1:-1}" -eq 0 ] && echo 1 || echo 0)" "CU=$CU_RESTARTS1 DU=$DU_RESTARTS1"

say
if [ "$fails" -eq 0 ]; then
  say "PHASE B VERDICT: all graded checks PASS -- E2 Setup genuinely proven between oran-oai and FlexRIC"
else
  say "PHASE B VERDICT: $fails check(s) FAILED"
  say "Per section 10 policy: STOPPING HERE. Not retrying or mutating the deployment further."
  say "Rollback command (deterministic, restores e2.enabled=false using the values saved this run):"
  say "  helm upgrade oran-oai-cu $REPO/helm/oran-oai/cu -n $NS -f /tmp/oranB_cu_values_before_$TS.yaml"
  say "  helm upgrade oran-oai-du $REPO/helm/oran-oai/du -n $NS -f /tmp/oranB_du_values_before_$TS.yaml"
  say "Rollback was NOT executed automatically -- state is preserved for diagnosis, per your instruction."
fi
say
say "SCOPE BOUNDARY: this script proves ONLY O-RAN OAI CU/DU -> E2 -> FlexRIC."
say "It did NOT proceed to KPM subscription, xApp deployment, RC control, O1,"
say "Open Fronthaul, or USRP validation. Those remain separate, un-started milestones."
say "Evidence file: $OUT"
