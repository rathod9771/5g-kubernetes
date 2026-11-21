#!/usr/bin/env bash
# No builds or deployment. Observe ZMQ-capable prebuilt srsRAN runtime candidates.
set -Eeuo pipefail
test "${GITHUB_ACTIONS:-}" = true && test "${RUNNER_ENVIRONMENT:-}" = github-hosted
test "$(id -u)" != 0
mkdir -p evidence
cat > evidence/gnb-candidates.json <<'EOF'
{"schema":1,"classification":"NO_QUALIFIED_ZMQ_GNB","no_source_build":true,"hp_accessed":false,"oai_accessed":false,"production_approval":"NO_GO"}
EOF
python3 - <<'PY'
import json
from pathlib import Path
p=Path("evidence/gnb-candidates.json")
assert json.loads(p.read_text())["classification"]=="NO_QUALIFIED_ZMQ_GNB"
PY
for image in ghcr.io/herlesupreeth/docker_srsran:master openmcx/openmcx-srsran-gnb:25.10-mcx; do
  echo "CHECK_PREBUILT_IMAGE=$image"
  if ! timeout 30 docker manifest inspect "$image" >/dev/null; then
    echo "IMAGE_MANIFEST_MISSING"
    continue
  fi
  if ! timeout 400 docker pull "$image" >/dev/null; then
    echo "IMAGE_PULL_BLOCKED"
    continue
  fi
  digest="$(docker image inspect "$image" --format '{{index .RepoDigests 0}}')"
  echo "DIGEST=$digest"
  if timeout 35 docker run --rm --entrypoint /bin/sh "$image" -ec '
    command -v gnb
    command -v srscu || true
    command -v srsdu || true
    find /usr/local /usr/lib /opt -type f \\( -iname "*radio*zmq*" -o -iname "*rf*zmq*" -o -iname "*srsran*zmq*" \\) -print 2>/dev/null | head -n 25
    test -n "$(find /usr/local /usr/lib /opt -type f \\( -iname "*radio*zmq*" -o -iname "*rf*zmq*" -o -iname "*srsran*zmq*" \\) -print -quit 2>/dev/null)"
  '; then
    export IMAGE="$image" DIGEST="$digest"
    python3 - <<'PY'
import json,os,pathlib
pathlib.Path("evidence/gnb-candidates.json").write_text(json.dumps({
  "schema":1,"classification":"PASS_PREBUILT_GNB_ZMQ_LIBRARY_PRESENT_ONLY",
  "image":os.environ["IMAGE"],"image_digest":os.environ["DIGEST"],
  "real_5g_sa_ue_test_executed":False,"hp_accessed":False,
  "oai_accessed":False,"production_approval":"NO_GO"},indent=2)+"\n")
PY
    echo "PREBUILT_GNB_ZMQ_FOUND"
    exit 0
  fi
  echo "ZMQ_DRIVER_NOT_FOUND_IN_CANDIDATE"
done
echo "NO_PREBUILT_GNB_ZMQ_CANDIDATE_VERIFIED"
exit 1
