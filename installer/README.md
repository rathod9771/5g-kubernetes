# Reproducible Installer

## One command

On a clean Ubuntu 24.04 machine:

    git clone https://github.com/rathod9771/5g-kubernetes.git
    cd 5g-kubernetes
    ./install.sh

The installer owns the machine bootstrap and is deliberately isolated under
installer/. It prepares the host, containerd, kubeadm Kubernetes, Flannel,
Multus, Longhorn, cert-manager, ingress-nginx, Rancher, Istio, OSM Release 19,
Prometheus/Grafana, the Flask dashboard, the Layer-3 watcher and validation.

It uses the repository's existing Helm charts, OSM packages, monitoring,
dashboard and Layer-3 code; it does not copy those application artifacts into
installer/.

## Stages

00 preflight
01 host dependencies and containerd
02 Kubernetes
03 cluster add-ons
04 OSM Release 19 + Flux/Gitea
05 Prometheus/Grafana
06 dashboard + Layer 3 systemd services
07 functional validation
08 final access information

Completed stages are marked in installer/state/ and logs are written to
installer/logs/. Re-running ./install.sh is therefore safe and resumable.

## Machine-specific configuration

config/global.env is generated from config/global.env.example when absent.
HOST_IP, HOST_INTERFACE and OSM_BASE_DOMAIN are auto-detected. The generated
global.env is gitignored and is never committed.

## Resource baseline

The reference platform uses Ubuntu 24.04, 8 CPU cores, 24+ GB RAM and at least
60 GB free disk. OSM plus Kubernetes plus monitoring plus a 5G core/RAN is a
heavy single-node workload.

## Important reproducibility boundary

OSM is the least deterministic component because the installer is supplied by
the upstream OSM project and is being run on an existing kubeadm cluster.
This installer pins the OSM devops tree to v19.0 and applies the environment
adjustments documented in docs/OSM19_INSTALL.md. If upstream changes those
scripts, the installer stops with its log preserved rather than silently
switching releases.

After success, use only the normal project commands:

    ./status.sh
    ./deploy.sh --list
    ./orchestrator.sh
    ./deploy.sh cloudran-oai
