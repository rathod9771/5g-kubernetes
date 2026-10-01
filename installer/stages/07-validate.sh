#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "\${BASH_SOURCE[0]}")/../.." && pwd)"
source "$ROOT/installer/lib/common.sh"
"$ROOT/scripts/validate.sh" --verbose
date -Is > "$STATE/07-validate.ok"
exec bash "$ROOT/installer/stages/08-access.sh"
