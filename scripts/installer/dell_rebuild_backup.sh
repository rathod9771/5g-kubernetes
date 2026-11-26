#!/usr/bin/env bash
# Dell-only pre-reset evidence and local-file backup. Never resets Kubernetes.
set -euo pipefail
case $- in *x*) set +x ;; esac
umask 077
die() { printf 'REFUSED: %s\n' "$*" >&2; exit 2; }
[[ "$#" -eq 1 ]] || die "Usage: $0 /mountpoint/of/separate-external-backup"
[[ "$(hostname -s | tr '[:upper:]' '[:lower:]')" == precision-3680 ]] || die "Dell Precision-3680 only"
[[ "$(id -u)" -ne 0 ]] || die "Run as the normal research user"
for cmd in findmnt lsblk mountpoint realpath tar sha256sum kubectl helm stat grep; do
  command -v "$cmd" >/dev/null || die "Missing command: $cmd"
done
dest="$(realpath -e -- "$1")" || die "Backup mount not found"
[[ "$dest" == /media/* || "$dest" == /run/media/* || "$dest" == /mnt/* ]] ||
  die "Use a mounted external or remote filesystem under /media or /mnt"
mountpoint -q -- "$dest" || die "Destination is not a mountpoint"
[[ "$(stat -c %d /)" != "$(stat -c %d "$dest")" ]] || die "Destination is on root filesystem"
src="$(findmnt -rn -T "$dest" -o SOURCE | head -n1)"
fstype="$(findmnt -rn -T "$dest" -o FSTYPE | head -n1)"
case "$fstype" in
  ext4|xfs|btrfs)
    [[ "$src" == /dev/* ]] || die "Unexpected local backup device"
    root_src="$(findmnt -rn -T / -o SOURCE | head -n1)"
    root_disk="$(lsblk -no PKNAME "$root_src" | head -n1)"
    [[ -n "$root_disk" ]] || die "Cannot determine OS disk"
    if lsblk -s -pnro NAME "$(realpath -e "$src")" | grep -Fxq "/dev/$root_disk"; then
      die "Destination is on the internal Windows/Ubuntu disk"
    fi
    if ! lsblk -s -nr -o TRAN "$(realpath -e "$src")" | grep -Fxq usb; then
      die "Destination is not a distinct USB disk; Longhorn iSCSI is not a backup"
    fi
    ;;
  nfs|nfs4|cifs|smb3)
    [[ "$src" != /dev/* ]] || die "Invalid remote mount"
    ;;
  *) die "Unsafe filesystem: use external ext4/xfs/btrfs or approved NFS/CIFS" ;;
esac
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
out="$(mktemp -d -p "$dest" "dell-rebuild-$stamp-XXXXXXXX")" || die "Cannot create backup directory"
chmod 700 "$out"
{
  echo "Dell-only backup evidence; no cleanup was performed"
  echo "host: $(hostname -s)"
  echo "mount_source: $src"
  echo "created_utc: $stamp"
  echo "IMPORTANT: LONGHORN PVC CONTENTS ARE NOT INCLUDED"
  echo "IMPORTANT: LOCAL FILE BACKUP DOES NOT AUTHORIZE CLUSTER RESET"
} > "$out/README.txt"
{
  hostname
  findmnt -no SOURCE,TARGET,FSTYPE /
  lsblk -o NAME,MODEL,SIZE,TYPE,FSTYPE,TRAN,RM
} > "$out/disks.txt"
kubectl --context kubernetes-admin@kubernetes get nodes -o wide > "$out/nodes.txt" || die "Node inventory failed"
kubectl --context kubernetes-admin@kubernetes get ns > "$out/namespaces.txt" || die "Namespace inventory failed"
kubectl --context kubernetes-admin@kubernetes get pvc -A -o wide > "$out/pvc.txt" || die "PVC inventory failed"
kubectl --context kubernetes-admin@kubernetes get pv -o wide > "$out/pv.txt" || die "PV inventory failed"
kubectl --context kubernetes-admin@kubernetes get ingress -A > "$out/ingress.txt" || die "Ingress inventory failed"
helm --kube-context kubernetes-admin@kubernetes list -A > "$out/helm.txt" || die "Helm inventory failed"
set --
for name in rajdeep private-5g-input 5g-kubernetes; do
  if [[ -e "$HOME/$name" ]]; then
    set -- "$@" "$name"
  fi
done
[[ "$#" -gt 0 ]] || die "No local research directories found"
tar -C "$HOME" -czf "$out/local-research.tar.gz" -- "$@" || die "Local archive failed"
tar -tzf "$out/local-research.tar.gz" >/dev/null || die "Archive is unreadable"
(
  cd "$out"
  sha256sum -- *.txt *.tar.gz > SHA256SUMS
  sha256sum -c SHA256SUMS >/dev/null
) || die "Backup checksum validation failed"
sync "$out"
printf 'LOCAL FILE BACKUP VERIFIED: %s\n' "$out"
printf 'BLOCKER: Longhorn PVC contents are NOT backed up.\n'
printf 'NO CLUSTER RESET OR FORMAT IS AUTHORIZED BY THIS RESULT.\n'
