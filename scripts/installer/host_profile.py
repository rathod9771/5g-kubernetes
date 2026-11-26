#!/usr/bin/env python3
"""Read-only admission gate for fresh HP or Dell research-lab installations.

No Kubernetes/OSM contact, credentials, cleanup, or deployment authorization.
"""
import argparse
import json
import os
from pathlib import Path
import platform
import re
import shutil
import sys

PROFILES = ('hp', 'dell')
MIN_CPUS = 8
MIN_RAM_GIB = 16
MIN_DISK_GIB = 100
MARKERS = (
    'etc/kubernetes/admin.conf',
    'var/lib/etcd/member',
    'var/lib/kubelet/config.yaml',
    'etc/rancher/k3s/k3s.yaml',
)


def _os_release(path):
    result = {}
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        if '=' in line and not line.startswith('#'):
            key, value = line.split('=', 1)
            result[key] = value.strip().strip('"\'')
    return result


def _mem_gib(path):
    match = re.search(r'^MemTotal:\s*(\d+)\s+kB$', Path(path).read_text(), re.M)
    if not match:
        raise ValueError('invalid memory inventory')
    return int(match.group(1)) / (1024 * 1024)


def check(profile, *, root=Path('/'), home=None, euid=None, arch=None,
          cpus=None, vendor=None, disk_free_gib=None,
          os_release=None, mem_gib=None):
    """Injection points permit offline tests without system modifications."""
    if profile not in PROFILES:
        raise ValueError('unknown host profile')
    root = Path(root)
    home = Path(home) if home is not None else Path.home()
    euid = os.geteuid() if euid is None else euid
    arch = platform.machine() if arch is None else arch
    cpus = os.cpu_count() if cpus is None else cpus
    if vendor is None:
        try:
            vendor = (root / 'sys/class/dmi/id/sys_vendor').read_text().strip()
        except OSError:
            vendor = ''
    if os_release is None:
        os_release = _os_release(root / 'etc/os-release')
    if mem_gib is None:
        mem_gib = _mem_gib(root / 'proc/meminfo')
    if disk_free_gib is None:
        disk_free_gib = shutil.disk_usage(home).free / (1024 ** 3)
    blockers = []
    if euid == 0:
        blockers.append('RUN_AS_NON_ROOT_USER')
    if os_release.get('ID') != 'ubuntu' or os_release.get('VERSION_ID') != '24.04':
        blockers.append('UBUNTU_24_04_REQUIRED')
    if arch not in ('x86_64', 'amd64'):
        blockers.append('AMD64_REQUIRED')
    if not isinstance(cpus, int) or cpus < MIN_CPUS:
        blockers.append('CPU_CAPACITY_INSUFFICIENT')
    if mem_gib < MIN_RAM_GIB:
        blockers.append('MEMORY_CAPACITY_INSUFFICIENT')
    if disk_free_gib < MIN_DISK_GIB:
        blockers.append('DISK_CAPACITY_INSUFFICIENT')
    if vendor:
        v = vendor.casefold()
        if profile == 'dell' and not v.startswith('dell'):
            blockers.append('HARDWARE_PROFILE_MISMATCH')
        if profile == 'hp' and not (v.startswith('hp') or v.startswith('hewlett')):
            blockers.append('HARDWARE_PROFILE_MISMATCH')
    for marker in MARKERS:
        if (root / marker).exists() or (root / marker).is_symlink():
            blockers.append('EXISTING_CLUSTER_STATE')
            break
    if (home / '.kube/config').exists() or (home / '.kube/config').is_symlink():
        blockers.append('EXISTING_CLUSTER_STATE')
    return {
        'schema': '5g-kubernetes/installer-host-admission',
        'schema_version': 1,
        'profile': profile,
        'status': 'CANDIDATE' if not blockers else 'BLOCKED',
        'blockers': sorted(set(blockers)),
        'fresh_install_permitted_by_local_checks': not blockers,
        'cluster_installation_performed': False,
        'deployment_authorized': False,
        'mutations_performed': False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', required=True, choices=PROFILES)
    args = parser.parse_args(argv)
    try:
        result = check(args.profile)
    except (ValueError, OSError, TypeError, UnicodeError):
        result = {
            'schema': '5g-kubernetes/installer-host-admission',
            'profile': args.profile, 'status': 'BLOCKED',
            'blockers': ['LOCAL_INSPECTION_UNAVAILABLE'],
            'fresh_install_permitted_by_local_checks': False,
            'cluster_installation_performed': False,
            'deployment_authorized': False, 'mutations_performed': False,
        }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result['status'] == 'CANDIDATE' else 2


if __name__ == '__main__':
    sys.exit(main())
