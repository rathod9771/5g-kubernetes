"""Provision owner-controlled OSM trust using only the target's public CA."""
import argparse
import base64
import os
from pathlib import Path
import re
import ssl
import subprocess

from local_safety import atomic_write, exclusive_lock
from runtime_config import ConfigError, load_config, private_text, read_snapshot, write_snapshot


def validate_certificate(certificate):
    try:
        pattern = rb'(?:\s*-----BEGIN CERTIFICATE-----\s+[A-Za-z0-9+/=\s]+-----END CERTIFICATE-----\s*)+'
        if not re.fullmatch(pattern, certificate):
            raise ValueError()
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(cadata=certificate.decode('ascii'))
        if not context.get_ca_certs():
            raise ValueError()
    except (ValueError, UnicodeError, ssl.SSLError):
        raise ConfigError('OSM TLS trust: missing or invalid public CA certificate material') from None


def read_target_certificate(cfg):
    try:
        result = subprocess.run(['kubectl', '--kubeconfig', cfg['KUBECONFIG_PATH'],
                                 'get', 'secret', 'osm-ca', '-n', cfg['OSM_NAMESPACE'],
                                 '-o', 'jsonpath={.data.tls\\.crt}'],
                                capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        raise ConfigError('OSM TLS trust: cannot read target osm-ca/tls.crt (command unavailable or timed out)') from None
    if result.returncode:
        raise ConfigError('OSM TLS trust: cannot read target osm-ca/tls.crt; check namespace, Secret and read permissions')
    return result.stdout


def ensure_osm_ca(cfg, *, refresh=False, read_certificate=None):
    """Reuse validated explicit trust; otherwise export the target's public CA.

    No system trust changes, lifecycle commands, key reads, or TLS bypasses.
    Explicit user-provided CA paths are never overwritten.
    """
    directory = Path(cfg['RUNTIME_DIR'])
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.is_symlink() or directory.stat().st_uid != os.getuid() or directory.stat().st_mode & 0o022:
        raise ConfigError('OSM TLS trust: runtime directory must be owner-controlled')
    destination = directory / 'osm-ca.crt'
    configured = cfg.get('OSM_CA_CERT_PATH')
    path = Path(configured) if configured else destination
    managed = path == destination
    with exclusive_lock(directory / '.osm-ca.lock'):
        if path.exists() and (not refresh or not managed):
            certificate, mode = private_text(path, required=True)
            if mode & 0o022:
                raise ConfigError('OSM TLS trust: CA file must not be group/world writable')
            validate_certificate(certificate.encode('utf-8'))
        elif not managed:
            raise ConfigError('OSM TLS trust: configured CA file is missing')
        else:
            encoded = (read_certificate or (lambda: read_target_certificate(cfg)))()
            try:
                certificate = base64.b64decode(encoded.strip(), validate=True)
            except ValueError:
                raise ConfigError('OSM TLS trust: osm-ca tls.crt is missing or invalid public certificate material') from None
            validate_certificate(certificate)
            atomic_write(destination, certificate)
        cfg['OSM_CA_CERT_PATH'] = str(path.resolve())
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--refresh', action='store_true', help='Refresh installer-owned public CA from target cluster')
    args = parser.parse_args()
    try:
        cfg = dict(load_config(network='auto'))
        path = ensure_osm_ca(cfg, refresh=args.refresh)
        # Existing services reload their snapshots per dashboard request. Keep
        # all service/installer snapshots aligned without restarting workloads.
        for name in ['dashboard-service.json', 'watcher-service.json', 'rancher-forward-service.json', 'installer-config.json']:
            snapshot = Path(cfg['RUNTIME_DIR']) / name
            if snapshot.exists():
                saved = read_snapshot(snapshot)
                saved['OSM_CA_CERT_PATH'] = str(path.resolve())
                write_snapshot(snapshot, saved)
        print('OSM TLS trust ready: ' + str(path.resolve()))
    except (ConfigError, OSError, ValueError):
        parser.exit(1, 'ERROR: OSM TLS trust preparation failed; check target osm-ca/tls.crt and owner-controlled runtime files\n')


if __name__ == '__main__':
    main()
