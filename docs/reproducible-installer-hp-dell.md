# Consolidated HP/Dell reference installer — research only

Staging integration branch: `p5/reproducible-installer-hp-dell`.
Source: existing `reproducible-installer` (HP and reference baseline) plus reviewed Dell P4/P5 release workflow and observer code.
This is not proof of a fresh install, rollback, OSM NS/VIM binding, signed release, or production readiness.

## Fresh-host guard (read-only)

On a **new** Ubuntu 24.04 amd64 host with 8+ logical CPUs, 16+ GiB RAM and 100+ GiB free disk:

```bash
./install.sh --profile hp --check-only      # NEW HP host
./install.sh --profile dell --check-only    # NEW Dell host
```

These checks inspect OS/hardware and reject local Kubernetes, k3s, etcd or kubeconfig markers.
No Kubernetes/OSM access, sudo, credential inspection, or mutation is performed.
On currently installed HP/Dell cluster nodes, `BLOCKED / EXISTING_CLUSTER_STATE` is expected.

Only after a **clean-host admission**, private subscriber/image input review, resource check,
and explicit operator approval should a fresh host run the existing mutating installer:

```bash
./install.sh --profile hp
# or on a different fresh target:
./install.sh --profile dell
```

`--profile` can precede the existing `--srsran-image-archive /approved/path.tar` option.
The original unprofiled `./install.sh` remains for legacy compatibility, but is not the recommended clean-install path.
See `docs/reference-installer.md` and `INSTALLATION.md` for private inputs and current limitations.

## GUI/NBI accessibility

The `ingress-nginx` installation uses NodePorts, not necessarily TCP/80 on the host.
After installation inspect `ingress-nginx-controller` Service port mapping and the actual hostname;
do not assume `http://gui.<node-ip>.nip.io:80` is reachable. For authorized local debugging
use a temporary loopback-only `kubectl -n osm port-forward svc/ng-ui 18080:80`.
Do not bypass OSM authentication.

## Deployment and state preservation

The Dell current-cluster observer has a pinned Dell address/cluster UID: **diagnostic only**.
The historical HP/P4 observer is also **not** a fresh-install profile.
No secret, NBI token, hardcoded old NS ID, or private subscriber file belongs in Git.
HP release policy remains pinned; P4 upgrade approval remains closed; signing still requires
a genuine, publication-eligible package and independent provenance verification.

Do **not** remove old HP/Dell Git worktrees, persistent volumes, etcd, OSM NS instances,
subscriber databases, credentials, or Git branches before verified backups and an isolated
successful fresh bootstrap plus rollback. Deleting worktrees does not uninstall Kubernetes.
