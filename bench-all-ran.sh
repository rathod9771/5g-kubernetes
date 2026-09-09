#!/usr/bin/env bash
# Benchmark every RAN combination through the RAN-selector dashboard.
# Identical procedure per scenario so the numbers are comparable.
#
#   ./bench-all-ran.sh            # all ten
#   ./bench-all-ran.sh oran-oai   # just one or a few
#
# Results append to results.csv and results.log in the current directory.

set -uo pipefail

NS=free5gc
DASH=http://localhost:8090
IPERF_SECS=10
PING_COUNT=15
SETTLE_MAX=300          # seconds to wait for the UE session
CSV=results.csv
LOG=results.log

ALL=(cran-srsran cran-oai oran-srsran oran-oai cloudran-srsran cloudran-oai \
     hcran-srsran hcran-oai vcran-srsran vcran-oai)
TARGETS=("${@:-${ALL[@]}}")
[ $# -gt 0 ] && TARGETS=("$@")

log(){ echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }

[ -f "$CSV" ] || echo "scenario,throughput_mbps,retransmits,rtt_min_ms,rtt_avg_ms,rtt_max_ms,du_cpu_m,cu_cpu_m,note" > "$CSV"

fix_edge_route(){
  local upf
  upf=$(kubectl get pods -n $NS -l app.kubernetes.io/name=upf -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
  [ -z "$upf" ] && return
  local ip
  ip=$(kubectl get pod -n $NS "$upf" -o jsonpath='{.status.podIP}' 2>/dev/null)
  [ -n "$ip" ] && sudo ip route replace 10.47.0.0/16 via "$ip" 2>/dev/null
}

ue_pod(){ kubectl get pods -n $NS -l component=ue -o jsonpath='{.items[0].metadata.name}' 2>/dev/null; }

wait_for_session(){
  local deadline=$((SECONDS + SETTLE_MAX)) ue
  while [ $SECONDS -lt $deadline ]; do
    ue=$(ue_pod)
    if [ -n "$ue" ] && kubectl exec -n $NS "$ue" -- ip addr show uesimtun0 2>/dev/null | grep -q "10\.45\."; then
      sleep 10          # let the link settle before measuring
      return 0
    fi
    sleep 10
  done
  return 1
}

cpu_of(){   # cpu_of <component-label>  -> millicores, or empty
  kubectl top pod -n $NS -l component="$1" --no-headers 2>/dev/null \
    | awk '{gsub(/m/,"",$2); print $2; exit}'
}

for key in "${TARGETS[@]}"; do
  log "=============================================================="
  log "SCENARIO: $key"
  log "=============================================================="

  log "deploying..."
  curl -s -X POST "$DASH/api/deploy" -H "Content-Type: application/json" \
       -d "{\"ran\":\"$key\"}" -o /dev/null

  log "waiting for UE session (up to ${SETTLE_MAX}s)..."
  if ! wait_for_session; then
    log "NO SESSION - skipping $key"
    echo "$key,,,,,,,,no UE session established" >> "$CSV"
    continue
  fi
  fix_edge_route

  UE=$(ue_pod)
  log "UE pod: $UE"

  # the UE image ships without iperf3 and the install does not survive a pod
  # restart, so put it back after every deploy
  if ! kubectl exec -n $NS "$UE" -- which iperf3 >/dev/null 2>&1; then
    log "installing iperf3 into the UE pod..."
    kubectl exec -n $NS "$UE" -- sh -c \
      "apt-get update -qq >/dev/null 2>&1; DEBIAN_FRONTEND=noninteractive apt-get install -y -qq iperf3 >/dev/null 2>&1"
    if ! kubectl exec -n $NS "$UE" -- which iperf3 >/dev/null 2>&1; then
      log "iperf3 install FAILED - recording latency only"
    fi
  fi

  # throughput
  pkill iperf3 2>/dev/null; sleep 1
  iperf3 -s -D
  sleep 2
  IPERF=$(kubectl exec -n $NS "$UE" -- iperf3 -c 10.244.0.1 -B 10.45.0.2 -t $IPERF_SECS 2>&1)
  pkill iperf3 2>/dev/null
  echo "$IPERF" >> "$LOG"

  # summary line: [  4]  0.00-10.00 sec  382 MBytes  320 Mbits/sec  473  sender
  SUM=$(echo "$IPERF" | grep -E '(sender|SUM.*sender)' | tail -1)
  TPUT=$(echo "$SUM" | awk '{for(i=1;i<=NF;i++) if($i ~ /bits\/sec/) {print $(i-1); exit}}')
  TUNIT=$(echo "$SUM" | awk '{for(i=1;i<=NF;i++) if($i ~ /bits\/sec/) {print $i; exit}}')
  # normalise Gbits/sec to Mbit/s so the column stays comparable
  if [ "$TUNIT" = "Gbits/sec" ] && [ -n "$TPUT" ]; then
    TPUT=$(awk -v v="$TPUT" 'BEGIN{printf "%.1f", v*1000}')
  fi
  RETR=$(echo "$SUM" | awk '{for(i=1;i<=NF;i++) if($i=="sender") {print $(i-1); exit}}')
  case "$RETR" in (*[!0-9]*) RETR="" ;; esac

  # latency
  PING=$(kubectl exec -n $NS "$UE" -- ping -I uesimtun0 -c $PING_COUNT 8.8.8.8 2>&1 | tail -2)
  echo "$PING" >> "$LOG"
  # line looks like: rtt min/avg/max/mdev = 7.932/8.325/10.857/0.718 ms
  RTT=$(echo "$PING" | sed -n 's/.*= \([0-9.]*\)\/\([0-9.]*\)\/\([0-9.]*\)\/.*/\1 \2 \3/p')
  RMIN=$(echo "$RTT" | awk '{print $1}')
  RAVG=$(echo "$RTT" | awk '{print $2}')
  RMAX=$(echo "$RTT" | awk '{print $3}')

  DUCPU=$(cpu_of du); CUCPU=$(cpu_of cu)

  NOTE=""
  [[ "$key" == cran-srsran ]] && NOTE="real B210 radio - not directly comparable to ZMQ-simulated paths"

  log "RESULT $key: ${TPUT:-?} Mbit/s, ${RETR:-?} retr, rtt ${RAVG:-?} ms avg, du ${DUCPU:-n/a}m cu ${CUCPU:-n/a}m"
  echo "$key,${TPUT},${RETR},${RMIN},${RAVG},${RMAX},${DUCPU},${CUCPU},${NOTE}" >> "$CSV"
done

log ""
log "DONE. Summary:"
column -s, -t "$CSV" | tee -a "$LOG"

# leave the node clean - the last scenario would otherwise run indefinitely
log "tearing down the last scenario..."
curl -s -X POST "$DASH/api/deploy" -H "Content-Type: application/json" -d '{"ran":"none"}' -o /dev/null
sleep 20
log "teardown done"
