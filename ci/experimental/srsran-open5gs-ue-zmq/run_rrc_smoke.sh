#!/usr/bin/env bash
# Bounded disposable CI experiment. Real srsRAN gNB + ZMQ + srsUE + Open5GS AMF/NRF.
# Deliberately no UDR/AUSF/SMF/UPF: cannot establish registration or PDU session here.
set -Eeuo pipefail
case "$-" in *x*) set +x ;; esac
test "${GITHUB_ACTIONS:-}" = true
test "${RUNNER_ENVIRONMENT:-}" = github-hosted
test "$(id -u)" != 0
test -z "${KUBECONFIG:-}" && test -z "${OSM_HOSTNAME:-}"
ROOT="$(pwd)"
SCRIPTS="$ROOT/ci/experimental/srsran-open5gs-ue-zmq"
GNB='openmcx/openmcx-srsran-gnb:25.10-mcx'
UE='openmcx/openmcx-srsue:25.10'
CORE='gradiant/open5gs:2.7.5'
WORK="$(mktemp -d "$RUNNER_TEMP/srsran-srsue.XXXXXXXX")"
OUT="$ROOT/srsue-rrc-verdict.json"
CAP_PID=""
stop_capture() {
  if [ -z "$CAP_PID" ]; then return; fi
  # Only the newly created capture process group is signalled.
  sudo kill -INT -- "-$CAP_PID" 2>/dev/null || true
  for attempt in 1 2 3; do
    kill -0 "$CAP_PID" 2>/dev/null || break
    sleep 1
  done
  if kill -0 "$CAP_PID" 2>/dev/null; then
    sudo kill -TERM -- "-$CAP_PID" 2>/dev/null || true
  fi
  wait "$CAP_PID" 2>/dev/null || true
  CAP_PID=""
}
done_cleanup() {
  rc="$1"
  stop_capture
  if [ "$rc" != 0 ]; then
    echo "REAL_SRSUE_RRC_SMOKE_NOT_VERIFIED code=$rc"
    for c in l3-ue l3-gnb l3-amf l3-nrf; do
      echo "=== $c brief diagnostic ==="
      docker logs --tail 20 "$c" 2>&1 | sed -E 's/[0-9]{15}/[REDACTED-IMSI]/g; s/[0-9a-fA-F]{32}/[REDACTED-SECRET]/g' | tail -20 || true
    done
  fi
  if [ ! -f "$OUT" ]; then
    python3 - "$OUT" "$rc" <<'PY'
import json,sys,pathlib
pathlib.Path(sys.argv[1]).write_text(json.dumps({
 "schema":1,"classification":"BLOCKED_NO_UE_RRC_PROTOCOL_PASS",
 "exit_code":int(sys.argv[2]),
 "ngap_initial_ue_message":"NOT_VERIFIED","rrc_ts_38_331":"NOT_VERIFIED",
 "registration_ts_23_502":"NOT_TESTED_FULL_5GC_ABSENT",
 "pdu_session_ts_23_502":"NOT_TESTED_FULL_5GC_ABSENT",
 "kpi_ts_28_552":"NOT_VERIFIED",
 "full_3gpp_conformance":"NOT_CLAIMED",
 "production_approval":"NO_GO","hp_oai_modified":False
},indent=2)+"\n")
PY
  fi
  for c in l3-ue l3-gnb l3-amf l3-nrf; do docker rm -f "$c" >/dev/null 2>&1 || true; done
  docker network rm l3-ue-rrc-net >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap 'done_cleanup "$?"' EXIT
python3 -m pip install --disable-pip-version-check -q PyYAML==6.0.2
python3 "$SCRIPTS/prepare_ue_rrc.py" "$WORK/config"
test -f "$WORK/config/gnb.yml" && test -f "$WORK/config/ue.conf"
test "$(stat -c '%a' "$WORK/config/ue.conf")" = 600
grep -q 'device_driver: zmq' "$WORK/config/gnb.yml"
grep -q 'common_scs: 15' "$WORK/config/gnb.yml"
grep -q 'band: 3' "$WORK/config/gnb.yml"
grep -qw avx2 /proc/cpuinfo || { echo "AVX2_RUNNER_REQUIRED"; exit 1; }
echo "PREBUILT_IMAGE_PULL_ONLY__NO_NATIVE_SOURCE_BUILD"
for image in "$GNB" "$UE" "$CORE"; do
  timeout 45 docker manifest inspect "$image" >/dev/null
  timeout 600 docker pull "$image" >/dev/null
done
for image in "$GNB" "$UE" "$CORE"; do
  docker image inspect "$image" --format 'resolved={{index .RepoDigests 0}}'
done
echo "INSTALL_PACKET_DECODER_ONLY"
sudo env DEBIAN_FRONTEND=noninteractive apt-get update -qq
sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq tcpdump tshark >/dev/null
python3 - <<'PY'
import socket
with socket.socket(socket.AF_INET,socket.SOCK_STREAM,socket.IPPROTO_SCTP):
    print("SCTP_READY")
PY
docker network create --internal --subnet 172.30.250.0/24 \
  --opt com.docker.network.bridge.name=br-l3-ue l3-ue-rrc-net >/dev/null
docker run -d --name l3-nrf --network l3-ue-rrc-net --ip 172.30.250.30 \
 -v "$WORK/config:/l3:ro" --entrypoint /bin/sh "$CORE" \
 -c 'exec open5gs-nrfd -c /l3/nrf.yaml' >/dev/null
sleep 3
docker run -d --name l3-amf --network l3-ue-rrc-net --ip 172.30.250.20 \
 -v "$WORK/config:/l3:ro" --entrypoint /bin/sh "$CORE" \
 -c 'exec open5gs-amfd -c /l3/amf.yaml' >/dev/null
sleep 4
for c in l3-nrf l3-amf; do docker inspect -f '{{.State.Running}}' "$c" | grep -qx true; done
setsid sudo -n timeout -s INT 140 tcpdump -n -i br-l3-ue -s 2048 -c 2048 -U \
  -w "$WORK/rrc-ngap.pcap" 'sctp and port 38412' &
CAP_PID="$!"
sleep 2
kill -0 "$CAP_PID"
CAP_PGID="$(ps -o pgid= -p "$CAP_PID" | tr -d ' ')"
test "$CAP_PGID" = "$CAP_PID" || { echo "CAPTURE_PROCESS_GROUP_NOT_ISOLATED"; exit 1; }
echo "START_REAL_SRSRAN_GNB_WITH_ZMQ"
docker run -d --name l3-gnb --network l3-ue-rrc-net --ip 172.30.250.10 \
 --cap-add SYS_NICE --ulimit rtprio=95 -v "$WORK/config:/l3:ro" \
 --entrypoint /bin/sh "$GNB" -c 'exec gnb -c /l3/gnb.yml' >/dev/null
sleep 9
docker inspect -f '{{.State.Running}}' l3-gnb | grep -qx true
echo "START_REAL_SRSUE_5G_SA_VIRTUAL_RF"
test -e /dev/net/tun
docker run -d --name l3-ue --network l3-ue-rrc-net --ip 172.30.250.40 \
 --cap-add NET_ADMIN --device=/dev/net/tun -v "$WORK/config:/l3:ro" \
 --log-opt max-size=4m --log-opt max-file=1 \
 --entrypoint /bin/sh "$UE" -c 'exec srsue /l3/ue.conf --log.filename=stdout --log.all_hex_limit=0 --log.phy_level=debug --log.rrc_level=debug --log.stack_level=debug' >/dev/null
sleep 65
echo "GITHUB_RUNNER_AVAILABLE_CPUS=$(nproc)"
echo "CFS_CPU_MAX=$(cat /sys/fs/cgroup/cpu.max 2>/dev/null || echo unknown)"
echo "RADIO_CONTAINER_CPU_AND_NETWORK_SNAPSHOT"
timeout 15 docker stats --no-stream --format 'name={{.Name}} cpu={{.CPUPerc}} net={{.NetIO}}' l3-gnb l3-ue || true
echo "ZMQ_VIRTUAL_TCP_SOCKET_STATES"
python3 - <<'PY'
import subprocess
for container in ('l3-gnb','l3-ue'):
    z=subprocess.run(['docker','exec',container,'cat','/proc/net/tcp'],
                     capture_output=True,text=True,timeout=8)
    if z.returncode:
        print(container,'TCP_TABLE_UNAVAILABLE')
        continue
    # Only endpoints for ZMQ 2000/2001. Never emit unrelated application endpoints.
    rows=[]
    for line in z.stdout.splitlines()[1:]:
        cols=line.split()
        if len(cols)<4: continue
        local=cols[1].split(':')[-1]
        remote=cols[2].split(':')[-1]
        if not {local,remote}.intersection({'07D0','07D1'}): continue
        rows.append((int(local,16),int(remote,16),cols[3]))
    established=sum(s=='01' for _,_,s in rows)
    listening=sum(s=='0A' for _,_,s in rows)
    print(f'{container} zmq_tcp_established={established} listening={listening} states={sorted(set(s for _,_,s in rows))}')
PY
echo "ZMQ_TCP_TRANSPORT_COUNTERS"
for component in l3-gnb l3-ue; do
  echo "ZMQ_COUNTERS $component"
  timeout 8 docker exec "$component" sh -c 'if command -v ss >/dev/null 2>&1; then ss -tin "( sport = :2000 or dport = :2000 or sport = :2001 or dport = :2001 )"; else echo SS_NOT_INSTALLED; fi' || true
done
docker inspect -f '{{.State.Running}}' l3-gnb | grep -qx true
stop_capture
sudo chmod 0644 "$WORK/rrc-ngap.pcap"
docker cp l3-gnb:/tmp/gnb.log "$WORK/gnb.log" >/dev/null 2>&1 || :
docker logs --tail 5000 l3-ue > "$WORK/ue.log" 2>&1 || :
echo "BOUNDED_SYNTHETIC_UE_RADIO_DIAGNOSTICS"
echo "----- CI_SYNTHETIC_UE_STDOUT_ALLOWLIST -----"
docker logs --tail 80 l3-ue 2>&1 | \
 grep -Ei 'srsUE|Starting|Attaching|RF|ZMQ|cell|sync|RRC|PBCH|SIB|NAS|PSS|SSS|RACH|registration|error|fail|not found|configured|waiting|scan|Searching' | \
 sed -E 's/[0-9]{15}/[REDACTED-SYNTHETIC-ID]/g; s/[0-9A-Fa-f]{32}/[REDACTED-KEY]/g' | tail -n 35 || true
for component in ue gnb; do
  echo "----- CI_SYNTHETIC_${component}_STATUS -----"
  docker inspect -f '{{.State.Status}} {{.State.ExitCode}}' "l3-$component" || true
  if [ -r "$WORK/$component.log" ]; then
    grep -Ei 'ZMQ|RF|radio|Cell Selection|Cell search|cell selection|sync|RRC|PBCH|SIB|NAS5G|PSS|SSS|PDU|Random Access|RACH|RLC|search|timeout|error|fail|connected|Triggering NAS|Switching on|waiting for' "$WORK/$component.log" | \
    sed -E 's/[0-9]{15}/[REDACTED-SYNTHETIC-ID]/g; s/[0-9A-Fa-f]{32}/[REDACTED-KEY]/g' | \
    tail -n 35 || true
  else
    echo "APP_LOG_UNAVAILABLE"
  fi
done
python3 "$SCRIPTS/report_ue_rrc.py" "$WORK/rrc-ngap.pcap" "$WORK/ue.log" "$WORK/gnb.log" "$OUT"
echo "SRSUE_RRC_AND_NGAP_INITIAL_UE_SMOKE_PASS__REGISTRATION_PDU_NOT_TESTED__NO_PRODUCTION_APPROVAL"
