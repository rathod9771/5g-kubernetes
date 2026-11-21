#!/usr/bin/env bash
# Preflight ONLY: inspect existing published srsRAN and srsUE images for 5G-SA ZMQ capability.
# Never builds, deploys, injects faults, adds subscribers, or accesses HP/OSM/K8s.
set -Eeuo pipefail
case "$-" in *x*) set +x;; esac
TYPE="${1:-}"
case "$TYPE" in
  gnb) IMAGE='ghcr.io/herlesupreeth/docker_srsran:master' ;;
  srsue) IMAGE='ghcr.io/herlesupreeth/docker_srslte:master' ;;
  *) echo "USAGE: $0 gnb|srsue"; exit 64 ;;
esac
test "${GITHUB_ACTIONS:-}" = true
test "${RUNNER_ENVIRONMENT:-}" = github-hosted
test "$(id -u)" != 0
test -z "${KUBECONFIG:-}"
test -z "${OSM_HOSTNAME:-}"
test -z "${OSM_PASSWORD:-}"
mkdir -p evidence
RESULT="evidence/${TYPE}.json"
status="BLOCKED_NO_IMAGE_QUALIFICATION"
digest=""
function receipt() {
  rc="$?"
  export RESULT TYPE IMAGE status digest
  python3 - <<'PY'
import json,os,pathlib
p=pathlib.Path(os.environ['RESULT'])
r={'schema':1,
   'component':os.environ['TYPE'],
   'candidate_prebuilt_image':os.environ['IMAGE'],
   'image_digest':os.environ.get('digest',''),
   'classification':os.environ.get('status','BLOCKED'),
   'scope':'DISPOSABLE_CI_BINARY_CAPABILITY_PREFLIGHT_ONLY',
   'native_build_executed':False, 'protocol_runtime_executed':False,
   'subscriber_data_used':False, 'hp_cluster_accessed':False,
   'oai_or_hcran_modified':False,
   'production_approval':'NO_GO'}
p.write_text(json.dumps(r,indent=2,sort_keys=True)+'\n')
PY
  return "$rc"
}
trap receipt EXIT
echo "IMAGE_MANIFEST_CHECK ${TYPE}"
timeout 30 docker manifest inspect "$IMAGE" >/dev/null
echo "IMAGE_PULL_PREBUILT_ONLY ${TYPE}"
timeout 600 docker pull "$IMAGE" >/dev/null
digest="$(docker image inspect "$IMAGE" --format '{{index .RepoDigests 0}}')"
test -n "$digest" && [[ "$digest" == *@sha256:* ]]
echo "RESOLVED_IMAGE ${TYPE} $digest"
if [ "$TYPE" = gnb ]; then
  timeout 35 docker run --rm --entrypoint /bin/sh "$IMAGE" -ec '
    command -v gnb
    command -v srscu
    command -v srsdu
    command -v ldconfig || true
    p="$(find /usr/local /usr/lib /opt -name libsrsran_radio_zmq.so -type f -print -quit 2>/dev/null)"
    test -n "$p"
    test -r "$p"
    echo "GNB_ZMQ_PLUGIN_PRESENT"
  '
else
  timeout 35 docker run --rm --entrypoint /bin/sh "$IMAGE" -ec '
    command -v srsue
    command -v ldconfig || true
    test -n "$(find /usr/local /usr/lib /opt -name "lib*zmq*.so*" -type f -print -quit 2>/dev/null)"
    echo "SRSUE_ZMQ_LIBRARIES_PRESENT"
  '
fi
status="PASS_IMAGE_BINARY_CAPABILITIES_ONLY"
echo "$status $TYPE"
