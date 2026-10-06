# Private Installation Inputs

The public repository intentionally does **not** contain passwords, subscriber authentication material, or private subscriber database archives.

These inputs must be transferred securely to each new installation machine before running the installer.

## Why they are not stored in Git

Do not commit:

- `config/global.env`
- `OSM_PASSWORD`
- `OSM_BOOTSTRAP_PASSWORD`
- `GRAFANA_ADMIN_PASSWORD`
- `RANCHER_BOOTSTRAP_PASSWORD`
- subscriber authentication keys
- Open5GS subscriber database archives

The repository already ignores `config/global.env` and runtime state.

## Fresh-machine setup

Run these commands on the **new Ubuntu machine** after cloning the repository.

### 1. Create the private configuration file

If you are building configuration from the repository template:

```bash
cd ~/5g-kubernetes
cp config/global.env.example config/global.env
chmod 600 config/global.env
```

If you already have a validated `config/global.env` from the Precision reference system, transfer that file securely to:

```text
~/5g-kubernetes/config/global.env
```

Then protect it:

```bash
chmod 600 ~/5g-kubernetes/config/global.env
```

Do not commit this file.

### 2. Copy the private subscriber archive

The installer requires the file referenced by:

```text
SUBSCRIBER_DATABASE_INPUT
```

Copy the authorized subscriber archive securely to the new machine, for example:

```text
/home/<user>/private-5g-input/open5gs-subscribers.archive.gz
```

Protect the directory and file:

```bash
chmod 700 /home/<user>/private-5g-input
chmod 600 /home/<user>/private-5g-input/open5gs-subscribers.archive.gz
```

Then set the corresponding path in:

```text
~/5g-kubernetes/config/global.env
```

Example:

```text
SUBSCRIBER_DATABASE_INPUT=/home/<user>/private-5g-input/open5gs-subscribers.archive.gz
```

Use the actual username and actual copied file path on the target system.

## Machine-specific values

`HOST_IP` and `HOST_INTERFACE` may be left empty when using the supported installer defaults. The runtime configuration detects the selected default-route address/interface when those fields are empty.

Do not copy generated OSM UUIDs from another machine. Fields such as project/VIM/Kubernetes-cluster identities are discovered for the new installation.

## Verify configuration without printing secrets

Use this check to confirm required keys are populated without displaying their values:

```bash
cd ~/5g-kubernetes

grep -E '^(OSM_USER|OSM_PASSWORD|OSM_BOOTSTRAP_PASSWORD|GRAFANA_ADMIN_PASSWORD|RANCHER_BOOTSTRAP_PASSWORD|SUBSCRIBER_DATABASE_INPUT|LAYER3_FAILOVER_SCENARIO)='   config/global.env   | sed -E 's/=.*/=<set>/'
```

This should display only key names followed by `<set>`.

## Install

After the private inputs are present:

```bash
cd ~/5g-kubernetes
./install.sh
```

The installer preflight validates the private subscriber archive before host/bootstrap deployment proceeds.

If a required private value is missing, the installer stops with an explicit configuration error instead of silently continuing.

## Security rule

Keep private inputs outside public Git history. If credentials are ever committed accidentally, treat them as exposed and rotate them rather than relying only on deleting the commit.
