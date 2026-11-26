# Dell-only reinstall: backup before any reset

Existing Dell is Windows/Ubuntu dual boot; preserve EFI/MSR/NTFS/Windows Recovery partitions. HP is never a reset target.

Attach an ENCRYPTED external USB drive (ext4/xfs/btrfs), or approved remote NFS/CIFS, mount it, then run on Dell:

    bash scripts/installer/dell_rebuild_backup.sh /media/administrator/RESEARCH_BACKUP

The script refuses an unmounted destination, the Dell internal NVMe and Longhorn iSCSI storage. It archives local research worktrees and existing private inputs plus read-only node/PVC/PV/ingress/Helm metadata; checks tar readability and SHA-256.

Treat this backup as SENSITIVE: local project archives may contain credentials. Do not upload it to GitHub or to this conversation. The script NEVER exports raw Kubernetes Secrets.

IMPORTANT: The seven Longhorn PVCs (including OSM PostgreSQL, MongoDB and Kafka) are NOT backed up by this script. Use application-consistent off-machine backups and test restores for data you need to retain, or explicitly record a decision to discard old databases. Never use a raw live database filesystem copy.

No reset is authorized by the script. Confirm Windows bootability, an off-host verified backup, PVC disposition and the approved reproducible-installer commit before any cluster removal.

PR #19 is still draft; its HP/Dell profile gate does not appear in reproducible-installer until review and merge. Fresh host preflight: ./install.sh --profile dell --check-only.
