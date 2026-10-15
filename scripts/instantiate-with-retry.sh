#!/usr/bin/env bash
# Retired: uncertain lifecycle outcomes must be reconciled, never force-cleaned.
set -euo pipefail
printf '%s\n' 'ERROR: Automatic lifecycle retry/forced cleanup is disabled. Use reviewed ownership-bound operations; the CI/CD MVP is simulation-only.' >&2
exit 1
