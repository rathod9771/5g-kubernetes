#!/usr/bin/env bash
# Reconciles /etc/containerd/config.toml for kubeadm/CRI compatibility without
# silently discarding an operator's existing, possibly hand-customized file.
#
# Prints exactly one word to stdout on success:
#   changed    - no prior config existed; a CRI-compatible default was written.
#   preserved  - a prior config existed and already satisfies kubeadm's
#                requirements; it was left untouched.
# Exits non-zero with an actionable message on stderr when an existing config
# cannot be safely reconciled automatically (never overwritten in that case).
#
# CONTAINERD_CONFIG_PATH overrides the config file location; used by tests to
# avoid touching real system paths. Real invocations (from host.sh) use the
# default.
set -euo pipefail
CONFIG_PATH="${CONTAINERD_CONFIG_PATH:-/etc/containerd/config.toml}"
MIN_MAJOR=1
MIN_MINOR=6

fail() {
  echo "ERROR: $1" >&2
  exit 1
}

version_output="$(containerd --version 2>/dev/null || true)"
version="$(printf '%s' "$version_output" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -n1 || true)"
[[ -n "$version" ]] || fail "could not determine the containerd version from '${version_output:-<no output>}'; install or repair containerd before continuing."
major="${version%%.*}"
minor="${version#*.}"; minor="${minor%%.*}"
if ! { (( major > MIN_MAJOR )) || { (( major == MIN_MAJOR )) && (( minor >= MIN_MINOR )); }; }; then
  fail "containerd ${version} is older than the minimum ${MIN_MAJOR}.${MIN_MINOR} required for the Kubernetes CRI v1 API; upgrade containerd before continuing."
fi

if [[ ! -e "$CONFIG_PATH" ]]; then
  containerd config default | sed 's/SystemdCgroup = false/SystemdCgroup = true/' | sudo tee "$CONFIG_PATH" >/dev/null
  echo changed
  exit 0
fi

existing="$( { sudo cat "$CONFIG_PATH" 2>/dev/null || cat "$CONFIG_PATH" 2>/dev/null; } || true)"
[[ -n "$existing" ]] || fail "${CONFIG_PATH} exists but could not be read; check its permissions."

if printf '%s\n' "$existing" | grep -qE '^[[:space:]]*disabled_plugins[[:space:]]*=.*"io\.containerd\.(grpc\.v1\.cri|cri\.v1\.(images|runtime))"'; then
  fail "${CONFIG_PATH} already exists and explicitly disables the Kubernetes CRI plugin (disabled_plugins). This installer will not re-enable it automatically, since that may be an intentional operator choice. Remove the CRI entry from disabled_plugins, or remove ${CONFIG_PATH} to let the installer generate a compatible default, then re-run."
fi

if ! printf '%s\n' "$existing" | grep -qE '^[[:space:]]*SystemdCgroup[[:space:]]*=[[:space:]]*true\b'; then
  fail "${CONFIG_PATH} already exists but does not set SystemdCgroup = true, which the pinned kubelet requires on this host. This installer will not silently overwrite a customized configuration. Set SystemdCgroup = true under the runc runtime options in ${CONFIG_PATH} and re-run, or remove ${CONFIG_PATH} to let the installer generate a compatible default."
fi

echo preserved
