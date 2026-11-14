# 5G Orchestrator Platform — Installation Guide

This guide installs the complete 5G Orchestrator platform on a fresh Ubuntu machine.

> **Validated target:** Ubuntu 24.04 LTS, single-node lab/demo deployment.

---

## FINAL HANDOVER — one command on the target machine

This is the recommended handover method for the lab/demo system.

The operator receiving the machine does **not** clone the repository, copy subscriber files, build srsRAN, or pull RAN/container images manually. A validated source machine creates one private self-extracting file:

```text
5g-orchestrator-installer.run
```

That single handover file contains:

- the exact tracked `5g-kubernetes` repository snapshot used to build the bundle;
- the private Open5GS subscriber database input;
- the private OAI UE subscriber JSON used for simulated UE/rfsim testing;
- the complete `k8s.io` containerd image cache from the validated source machine;
- every additional image declared in `config/reference-versions.json`;
- all declared RAN runtime images, including the locally built pinned srsRAN image and OAI/UE images;
- SHA-256 checksums for the embedded repository, subscriber input, image archive and image-reference inventory.

The generated `.run` file is **private**. It contains subscriber data and can be very large because it embeds all container images. Never commit it to GitHub, upload it to a public file share, or email it insecurely.

### A. Build the handover file on the validated source machine

Use the already-validated machine after `./install.sh` has completed successfully and after the required RAN images have been exercised/cached.

Example on the validated Precision machine:

```bash
cd ~/5g-kubernetes

git checkout reproducible-installer
git pull origin reproducible-installer

scripts/build-private-installer.sh \
  --subscriber-archive /home/administrator/open5gs-subscribers.archive.gz \
  --ue-subscriber-input /home/administrator/private-5g-input/oai-ue-subscriber.json \
  --output ~/5g-orchestrator-installer.run
```

The builder will automatically:

1. verify both private subscriber inputs without printing their authentication values;
2. verify that the source repository has no tracked local changes;
3. require the pinned local srsRAN image to exist;
4. pull any policy-declared registry image that is missing;
5. export the complete Kubernetes/containerd image cache;
6. archive the exact repository commit;
7. checksum the embedded payload;
8. create `~/5g-orchestrator-installer.run`.

Check the output file:

```bash
ls -lh ~/5g-orchestrator-installer.run
sha256sum ~/5g-orchestrator-installer.run
```

Record the SHA-256 value separately and transfer the file to the target machine using an approved secure method.

### B. Target machine — the operator runs one command

Copy only this file to the target user's home directory:

```text
~/5g-orchestrator-installer.run
```

Then the target operator runs exactly:

```bash
bash ~/5g-orchestrator-installer.run
```

The handover installer will automatically:

1. verify the embedded payload checksums;
2. install the minimum Ubuntu/containerd prerequisites;
3. start containerd;
4. import the complete bundled image archive into the Kubernetes `k8s.io` namespace;
5. install the pinned repository snapshot as `~/5g-kubernetes`;
6. install the Open5GS subscriber archive and OAI UE subscriber JSON under `~/private-5g-input/` with restrictive permissions;
7. generate `config/global.env` with the Open5GS private subscriber input path;
8. preserve the OAI UE JSON for later `prepare_simulated_ue.py` use without printing authentication values;
9. run the normal `./install.sh` workflow;
10. build nothing from srsRAN source when the bundled approved local image is already present;
11. continue through Kubernetes, infrastructure, OSM, Open5GS, monitoring and service installation.

The target machine must be a fresh intended Ubuntu 24.04 lab host and the user must have sudo access.

> **Internet note:** all container images are carried inside the handover file. The current installer can still use the network for Ubuntu packages, Helm/chart acquisition and pinned source/package metadata. This handover mode removes manual image transfers and registry pulls from the operator workflow; it is not yet a fully air-gapped OS/package installer.

### C. Security rule

Do not place any of the following in the public repository:

- `5g-orchestrator-installer.run`;
- Open5GS subscriber archives;
- OAI/UE authentication JSON/files;
- exported private credentials;
- generated private image/input bundles.

Only the builder script and installation instructions belong in Git.

---

## 1. Prerequisites

Recommended host:

- Ubuntu 24.04 LTS
- x86_64 / amd64
- 8+ CPU cores
- 16 GB RAM minimum, 32 GB preferred
- 100 GB+ free disk
- Internet access
- sudo access

Check the machine:

```bash
echo "===== OS ====="
cat /etc/os-release

echo
echo "===== CPU ====="
nproc

echo
echo "===== MEMORY ====="
free -h

echo
echo "===== DISK ====="
df -h /

echo
echo "===== NETWORK ====="
ip -br a

echo
echo "===== INTERNET ====="
ping -c 3 8.8.8.8
ping -c 3 github.com
```

Expected:

```text
Ubuntu 24.04 LTS
8 or more CPU cores recommended
16 GB or more RAM
Sufficient free disk space
Network interface with an IPv4 address
Internet ping succeeds
```

---

## 2. Manual/developer path — Install Git

```bash
sudo apt update
sudo apt install -y git
git --version
```

Expected:

```text
git version 2.x.x
```

---

## 3. Manual/developer path — Clone the project

```bash
cd ~

git clone -b reproducible-installer \
  https://github.com/rathod9771/5g-kubernetes.git

cd ~/5g-kubernetes
```

Verify:

```bash
git branch --show-current
git log -1 --oneline
```

Expected branch:

```text
reproducible-installer
```

---

## 4. Automatic lab credentials

For this lab/demo submission build, the installer uses the following defaults automatically:

```text
OSM username: admin
OSM password: admin
OSM project: admin
OSM VIM name: dummyvim
OSM bootstrap password: admin
Grafana admin password: admin
Rancher bootstrap password: admin
Layer-3 failover scenario: cran-srsran
```

No manual password entry is required for these application credentials.

> These defaults are intended for the lab/demo environment. They should be changed for a production deployment.

The installer also automatically discovers the host IP address and network interface when they are not explicitly configured.

---

## 5. Manual/developer path — Private subscriber database input

The current installer still requires the authorized Open5GS subscriber database archive.

The file is **not stored in the public Git repository**.

Place the archive on the target machine, for example:

```text
/home/<user>/open5gs-subscribers.archive.gz
```

Create the local installer configuration:

```bash
cd ~/5g-kubernetes
cp config/global.env.example config/global.env
chmod 600 config/global.env
```

Set only the subscriber archive path:

```bash
nano config/global.env
```

Set:

```text
SUBSCRIBER_DATABASE_INPUT=/home/<user>/open5gs-subscribers.archive.gz
```

Replace `<user>` with the actual Ubuntu username.

Save the file.

Verify the file exists:

```bash
ls -lh /home/$USER/open5gs-subscribers.archive.gz
```

Expected:

```text
A regular .gz file is listed.
```

Do not print or share subscriber authentication material.

---

## 6. Manual/developer path — Run the installer

```bash
cd ~/5g-kubernetes
chmod +x install.sh
./install.sh
```

The installer performs:

```text
Preflight
  ↓
Host preparation
  ↓
containerd
  ↓
Kubernetes
  ↓
Flannel / Longhorn
  ↓
cert-manager / ingress-nginx
  ↓
Rancher
  ↓
Istio
  ↓
OSM
  ↓
Open5GS
  ↓
Prometheus / Grafana
  ↓
RAN Selector dashboard
  ↓
Validation
```

Expected behavior:

- required Ubuntu packages are installed;
- Kubernetes is initialized;
- platform namespaces and workloads are created;
- OSM is installed and configured;
- Open5GS is deployed;
- monitoring is deployed;
- dashboard services are installed;
- final validation runs.

If the installer stops at a later stage, fix the reported issue and rerun:

```bash
./install.sh
```

Do not delete already-successful stages unless the installer explicitly instructs you to do so.

---

## 7. Verify Kubernetes

```bash
kubectl get nodes -o wide
```

Expected:

```text
STATUS   Ready
```

Check all workloads:

```bash
kubectl get pods -A -o wide
```

Expected:

```text
Most platform pods should be Running and Ready.
No unexpected CrashLoopBackOff or ImagePullBackOff states.
```

Check major namespaces:

```bash
kubectl get namespaces
```

Expected namespaces include platform namespaces such as:

```text
kube-system
longhorn-system
cert-manager
ingress-nginx
cattle-system
istio-system
osm
```

---

## 8. Run project validation

```bash
cd ~/5g-kubernetes
bash scripts/validate.sh
```

Expected:

```text
Major platform checks report PASS / READY.
```

---

## 9. Access the dashboard

Check that the dashboard service is running:

```bash
sudo systemctl status ran-selector --no-pager
```

Expected:

```text
Active: active (running)
```

If needed, check recent logs:

```bash
sudo journalctl -u ran-selector -n 100 --no-pager
```

### Find the server IP address

`<SERVER-IP>` means the IP address of the Ubuntu machine where the platform is installed.

Run:

```bash
hostname -I
```

or:

```bash
ip -br a
```

Example:

```text
192.168.1.50
```

### Open the dashboard on the same machine

Either of these can be used:

```text
http://127.0.0.1:8090
```

or:

```text
http://<SERVER-IP>:8090
```

Example:

```text
http://192.168.1.50:8090
```

### Open the dashboard from another machine

Use:

```text
http://<SERVER-IP>:8090
```

Example:

```text
http://192.168.1.50:8090
```

Do not use `127.0.0.1` from another computer because it refers to that computer itself.

---

## 10. Supported RAN scenarios

| Architecture | OAI | srsRAN | Status |
|---|---|---|---|
| C-RAN | Enabled | Enabled | Validated |
| Cloud-RAN | Enabled | Enabled | Validated |
| vC-RAN | Enabled | Enabled | Validated |
| O-RAN | Enabled | Disabled | RAN-level validated |
| H-CRAN | Disabled | Disabled | Future |
| F-RAN | Disabled | Disabled | Future |

Current limitation:

```text
O-RAN + OAI: RAN-level accepted
E2 / FlexRIC: pending
```

---

## 11. Test a RAN deployment

Use the dashboard.

Recommended flow:

```text
Open dashboard
  ↓
Select supported architecture
  ↓
Select OAI or srsRAN
  ↓
Deploy / Switch
  ↓
Operational Status
  ↓
Technical Logs
  ↓
Runtime / Recent Events
```

Check active RAN state:

```bash
cd ~/5g-kubernetes
cat .runtime/active-ran.yaml
```

Then inspect the active namespace:

```bash
NS=<namespace-from-active-ran.yaml>
kubectl get pods -n "$NS" -o wide
```

Expected:

```text
Selected RAN CU/DU or gNB workloads are Running and Ready.
```

---

## 12. Useful checks

```bash
sudo systemctl status containerd --no-pager
sudo systemctl status kubelet --no-pager
sudo systemctl status ran-selector --no-pager

kubectl get nodes -o wide
kubectl get pods -A -o wide
kubectl get svc -A
kubectl get pvc -A

cd ~/5g-kubernetes
git branch --show-current
git log -5 --oneline
```

---

## 13. Quick installation summary

```bash
sudo apt update
sudo apt install -y git

cd ~

git clone -b reproducible-installer \
  https://github.com/rathod9771/5g-kubernetes.git

cd ~/5g-kubernetes

cp config/global.env.example config/global.env
chmod 600 config/global.env

# Set SUBSCRIBER_DATABASE_INPUT in config/global.env,
# then run:

chmod +x install.sh
./install.sh
```

After installation:

```bash
kubectl get nodes -o wide
kubectl get pods -A
sudo systemctl status ran-selector --no-pager
```

Open:

```text
http://<SERVER-IP>:8090
```

---

## 14. Final handover checklist

- [ ] Ubuntu 24.04 installed
- [ ] Git clone completed
- [ ] Subscriber archive copied locally
- [ ] `SUBSCRIBER_DATABASE_INPUT` configured
- [ ] `./install.sh` completed
- [ ] Kubernetes node is Ready
- [ ] Platform pods are healthy
- [ ] OSM is healthy
- [ ] Open5GS is healthy
- [ ] Monitoring is healthy
- [ ] ran-selector is active
- [ ] Dashboard opens on port 8090
- [ ] At least one supported RAN scenario deploys successfully

