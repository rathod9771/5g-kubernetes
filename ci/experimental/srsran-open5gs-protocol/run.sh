#!/usr/bin/env bash
# Disposable GitHub runner only. NO OAI, no HP/OSM/Kubernetes credentials, no builds.
set -Eeuo pipefail
case "$-" in *x*) set +x ;; esac
PROFILE="$1"
case "$PROFILE" in cran|vcran|fran|cloud-ran|hcran|oran) ;; *) echo "UNKNOWN_PROFILE"; exit 2 ;; esac
test "$GITHUB_ACTIONS" = true
test "$RUNNER_ENVIRONMENT" = github-hosted
test "$(id -u)" != 0
test -z "$(env | grep '^KUBECONFIG=' || true)"
test -z "$(env | grep '^OSM_' || true)"
test -z "$(env | grep '^AWS_' || true)"
echo "SRSRAN_ONLY_DISPOSABLE_GITHUB_RUNNER profile=$PROFILE"
SRS='softwareradiosystems/srsran-project:release_avx2-4bf1543936__2026-02-16'
CORE='gradiant/open5gs:2.7.5'
WORK="$(mktemp -d "$RUNNER_TEMP/srsran-proto.XXXXXXXX")"
ROOT="$(pwd)"
SCRIPT="$ROOT/ci/experimental/srsran-open5gs-protocol"
OUT="$ROOT/verdict.json"
CAP_PID=""
finish() {
  rc="$1"
  if [ -n "$CAP_PID" ]; then
    sudo kill -INT "$CAP_PID" 2>/dev/null || true
    wait "$CAP_PID" 2>/dev/null || true
  fi
  if [ "$rc" != 0 ]; then
    echo "PROTOCOL_NOT_VERIFIED profile=$PROFILE exit_code=$rc"
    for c in l3-nrf l3-amf l3-cu l3-du1 l3-du2; do
      echo "----- $c last 30 container-log lines -----"
      docker logs --tail 30 "$c" 2>&1 || true
    done
    for f in cu du1 du2; do
      echo "----- $f application log tail -----"
      docker exec "l3-$f" sh -c "tail -n 30 /tmp/$f.log" 2>&1 || true
    done
  fi
  if [ ! -f "$OUT" ]; then
    python3 - "$PROFILE" "$OUT" "$rc" <<'PY'
import json,sys
from pathlib import Path
Path(sys.argv[2]).write_text(json.dumps({
    "schema_version":1, "profile":sys.argv[1],
    "classification":"NOT_VERIFIED_RUNTIME_OR_EVIDENCE_FAILED",
    "exit_code":int(sys.argv[3]),
    "ngap_ts_38_413":"NOT_VERIFIED",
    "f1ap_ts_38_473":"NOT_VERIFIED",
    "rrc_ts_38_331":"NOT_TESTED_NO_UE",
    "ue_registration_pdu_ts_23_502":"NOT_TESTED_NO_UE",
    "kpi_ts_28_552":"NOT_VERIFIED",
    "oai_touched":False, "hp_cluster_modified":False,
    "production_decision":"NO_GO"
},indent=2)+"\n")
PY
  fi
  for c in l3-du2 l3-du1 l3-cu l3-amf l3-nrf; do
    docker rm -f "$c" >/dev/null 2>&1 || true
  done
  docker network rm l3-protocol-net >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap 'finish "$?"' EXIT
python3 -m pip install --disable-pip-version-check -q PyYAML==6.0.2
python3 "$SCRIPT/prepare.py" "$PROFILE" "$WORK/config"
test -e "$WORK/config/plan.json"
grep -qw avx2 /proc/cpuinfo || { echo "RUNNER_AVX2_REQUIRED"; exit 1; }
echo "INSTALL_DECODE_TOOLS_ONLY"
sudo env DEBIAN_FRONTEND=noninteractive apt-get update -qq
sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq tcpdump tshark >/dev/null
echo "PULL_APPROVED_PUBLIC_PREBUILT_CONTAINER_IMAGES__NO_BUILD"
docker pull "$SRS" >/dev/null
docker pull "$CORE" >/dev/null
docker image inspect "$SRS" --format 'srsran id={{.Id}} digest={{index .RepoDigests 0}}'
docker image inspect "$CORE" --format 'open5gs id={{.Id}} digest={{index .RepoDigests 0}}'
docker run --rm --entrypoint /bin/sh "$SRS" -c 'command -v srscu && command -v srsdu'
docker run --rm --entrypoint /bin/sh "$CORE" -c 'command -v open5gs-amfd && command -v open5gs-nrfd'
echo "SCTP_KERNEL_PRECHECK"
python3 - <<'PY'
import socket
with socket.socket(socket.AF_INET,socket.SOCK_STREAM,socket.IPPROTO_SCTP):
    print("SCTP_SOCKET_SUPPORTED")
PY
docker network create --internal --subnet 172.30.250.0/24 \
  --opt com.docker.network.bridge.name=br-l3-proto l3-protocol-net >/dev/null
ip link show br-l3-proto >/dev/null
docker run -d --name l3-nrf --network l3-protocol-net --ip 172.30.250.30 \
  -v "$WORK/config:/l3:ro" --entrypoint /bin/sh "$CORE" \
  -c 'exec open5gs-nrfd -c /l3/nrf.yaml' >/dev/null
sleep 3
docker inspect -f '{{.State.Running}}' l3-nrf | grep -qx true
docker run -d --name l3-amf --network l3-protocol-net --ip 172.30.250.20 \
  -v "$WORK/config:/l3:ro" --entrypoint /bin/sh "$CORE" \
  -c 'exec open5gs-amfd -c /l3/amf.yaml' >/dev/null
sleep 4
docker inspect -f '{{.State.Running}}' l3-amf | grep -qx true
echo "START_BOUNDED_NGAP_F1AP_PACKET_CAPTURE_BEFORE_CU_DU"
sudo timeout -s INT 85 tcpdump -n -i br-l3-proto -s 2048 -c 2048 -U \
  -w "$WORK/ngap-f1ap.pcap" 'sctp and (port 38412 or port 38472)' &
CAP_PID="$!"
sleep 2
kill -0 "$CAP_PID"
docker run -d --name l3-cu --network l3-protocol-net --ip 172.30.250.10 \
  --cap-add SYS_NICE --ulimit rtprio=95 \
  -v "$WORK/config:/l3:ro" --entrypoint /bin/sh "$SRS" \
  -c 'exec srscu -c /l3/cu.yml' >/dev/null
sleep 7
docker inspect -f '{{.State.Running}}' l3-cu | grep -qx true
docker run -d --name l3-du1 --network l3-protocol-net --ip 172.30.250.11 \
  --cap-add SYS_NICE --ulimit rtprio=95 \
  -v "$WORK/config:/l3:ro" --entrypoint /bin/sh "$SRS" \
  -c 'exec srsdu -c /l3/du1.yml' >/dev/null
if [ "$PROFILE" = hcran ]; then
  sleep 3
  docker run -d --name l3-du2 --network l3-protocol-net --ip 172.30.250.12 \
    --cap-add SYS_NICE --ulimit rtprio=95 \
    -v "$WORK/config:/l3:ro" --entrypoint /bin/sh "$SRS" \
    -c 'exec srsdu -c /l3/du2.yml' >/dev/null
fi
sleep 25
for c in l3-nrf l3-amf l3-cu l3-du1; do
  docker inspect -f '{{.State.Running}}' "$c" | grep -qx true
done
if [ "$PROFILE" = hcran ]; then
  docker inspect -f '{{.State.Running}}' l3-du2 | grep -qx true
fi
sudo kill -INT "$CAP_PID" 2>/dev/null || true
wait "$CAP_PID" || true
CAP_PID=""
sudo chmod 0644 "$WORK/ngap-f1ap.pcap"
python3 "$SCRIPT/report.py" "$WORK/config/plan.json" "$WORK/ngap-f1ap.pcap" "$OUT"
echo "REAL_SRSRAN_OPEN5GS_NGAP_AND_F1AP_PASS__OTHER_PROTOCOLS_NOT_TESTED__PRODUCTION_NO_GO"
