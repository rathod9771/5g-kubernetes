"""Verify/convert the approved reference export; import only on explicit request.

No registry pulls or image execution. Containerd import belongs on target nodes.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile

from local_safety import exclusive_lock
from runtime_images import ROOT, policy, reference


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def stream_digest(stream):
    digest, size = hashlib.sha256(), 0
    while chunk := stream.read(4 * 1024 * 1024):
        digest.update(chunk)
        size += len(chunk)
    return 'sha256:' + digest.hexdigest(), size


def inspect_archive(path, image):
    """Authenticate the whole export and each uncompressed OCI layer; no extract."""
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('Provide a regular approved srsRAN Docker export, not a symlink')
    with path.open('rb') as stream:
        digest, size = stream_digest(stream)
    if digest != image['archive_sha256'] or size != image['archive_size']:
        raise ValueError('srsRAN export identity mismatch; refusing import')
    with tarfile.open(path, 'r:') as archive:
        members = archive.getmembers()
        names = [m.name for m in members]
        if len(names) != len(set(names)) or any(m.issym() or m.islnk() or
                Path(m.name).is_absolute() or '..' in Path(m.name).parts for m in members):
            raise ValueError('Unsafe or duplicate srsRAN archive members')
        def read(name):
            member = archive.getmember(name)
            if not member.isfile() or member.size > 4 * 1024 * 1024:
                raise ValueError('Invalid srsRAN export metadata')
            return archive.extractfile(member).read()
        entries = json.loads(read('manifest.json'))
        if len(entries) != 1:
            raise ValueError('Expected exactly one srsRAN image')
        entry = entries[0]
        config = read(entry['Config'])
        parsed = json.loads(config)
        if 'sha256:' + hashlib.sha256(config).hexdigest() != image['config_digest']:
            raise ValueError('srsRAN image configuration differs')
        if parsed.get('os') != 'linux' or parsed.get('architecture') != 'amd64':
            raise ValueError('Approved srsRAN import requires linux/amd64')
        layer_names = entry['Layers']
        diff_ids = parsed['rootfs']['diff_ids']
        if len(layer_names) != len(diff_ids) or not layer_names:
            raise ValueError('Invalid srsRAN layer inventory')
        layers = []
        for name, expected in zip(layer_names, diff_ids):
            member = archive.getmember(name)
            if not member.isfile():
                raise ValueError('Invalid srsRAN layer')
            digest, size = stream_digest(archive.extractfile(member))
            if digest != expected:
                raise ValueError('srsRAN layer digest mismatch')
            layers.append(dict(mediaType='application/vnd.oci.image.layer.v1.tar', digest=digest, size=size))
        manifest = canonical(dict(schemaVersion=2, mediaType='application/vnd.oci.image.manifest.v1+json',
                                  config=dict(mediaType='application/vnd.oci.image.config.v1+json',
                                              digest=image['config_digest'], size=len(config)), layers=layers))
        if 'sha256:' + hashlib.sha256(manifest).hexdigest() != image['digest']:
            raise ValueError('Approved srsRAN OCI manifest differs')
        return config, manifest, list(zip(layer_names, layers))


def convert(path, destination, image):
    config, manifest, layers = inspect_archive(path, image)
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise ValueError('Conversion output already exists; refusing overwrite')
    # Private temporary sibling + atomic promotion; failures expose no partial tar.
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor = dict(mediaType='application/vnd.oci.image.manifest.v1+json',
                      digest=image['digest'], size=len(manifest),
                      platform=dict(os='linux', architecture='amd64'),
                      annotations={'org.opencontainers.image.ref.name': image['repository'] + ':' + image['tag']})
    index = canonical(dict(schemaVersion=2, manifests=[descriptor]))
    with tempfile.TemporaryDirectory(prefix='.srsran-oci-', dir=destination.parent) as directory:
        staging = Path(directory) / 'image.tar'
        with tarfile.open(path, 'r:') as source, tarfile.open(staging, 'w', format=tarfile.PAX_FORMAT) as output:
            def add(name, size, stream):
                info = tarfile.TarInfo(name)
                info.size, info.mode, info.mtime = size, 0o644, 0
                output.addfile(info, stream)
            for name, content in [('oci-layout', b'{"imageLayoutVersion":"1.0.0"}'), ('index.json', index),
                                  ('blobs/sha256/' + image['config_digest'][7:], config),
                                  ('blobs/sha256/' + image['digest'][7:], manifest)]:
                add(name, len(content), io.BytesIO(content))
            for name, layer in layers:
                class CheckedReader:
                    def __init__(self, stream):
                        self.stream, self.digest = stream, hashlib.sha256()
                    def read(self, size=-1):
                        chunk = self.stream.read(size)
                        self.digest.update(chunk)
                        return chunk
                reader = CheckedReader(source.extractfile(name))
                add('blobs/sha256/' + layer['digest'][7:], layer['size'], reader)
                if 'sha256:' + reader.digest.hexdigest() != layer['digest']:
                    raise ValueError('srsRAN layer changed during conversion; no OCI archive published')
        with staging.open('rb') as stream:
            os.fsync(stream.fileno())
        # Hard-link publication is atomic and does not overwrite a racing output.
        os.link(staging, destination)
    return destination


def installed(image, run):
    result = run(['sudo', 'ctr', '--namespace', 'k8s.io', 'images', 'list'])
    for row in result.stdout.decode().splitlines():
        columns = row.split()
        if len(columns) >= 3 and columns[0] == reference(image):
            if columns[2] != image['digest']:
                raise ValueError('Containerd image name points to an unapproved manifest')
            ready = run(['sudo', 'ctr', '--namespace', 'k8s.io', 'images', 'check',
                         '--quiet', 'name==' + reference(image)])
            if reference(image) not in ready.stdout.decode().splitlines():
                return False
            # Authenticate the target descriptor bytes, not just a name or config ID.
            for digest in (image['digest'], image['config_digest']):
                blob = run(['sudo', 'ctr', '--namespace', 'k8s.io', 'content', 'get', digest]).stdout
                if 'sha256:' + hashlib.sha256(blob).hexdigest() != digest:
                    raise ValueError('Containerd content differs from approved integrity lock')
                if digest == image['digest'] and json.loads(blob).get('config', {}).get('digest') != image['config_digest']:
                    raise ValueError('Containerd manifest configuration differs from integrity lock')
            # Query the same runtime used by kubelet; resolving a ctr name is insufficient.
            cri = run(['sudo', 'crictl', '--runtime-endpoint', 'unix:///run/containerd/containerd.sock',
                       '--image-endpoint', 'unix:///run/containerd/containerd.sock',
                       'inspecti', reference(image)], check=False)
            if cri.returncode:
                raise ValueError('Approved srsRAN runtime reference is not CRI-resolvable')
            status = json.loads(cri.stdout).get('status', {})
            if status.get('id') != image['config_digest'] or reference(image) not in status.get('repoTags', []):
                raise ValueError('CRI runtime reference resolves to unapproved image content')
            return True
    return False


def import_image(path, runtime_dir, run, image=None):
    image = image or policy()['components']['srsran']
    runtime_dir = Path(runtime_dir)
    runtime_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with exclusive_lock(runtime_dir / '.srsran-image.lock'):
        if installed(image, run):
            return False
        if not path:
            raise ValueError('Approved srsRAN image absent; supply --srsran-image-archive and import on every workload node')
        with tempfile.TemporaryDirectory(prefix='srsran-import-', dir=runtime_dir) as directory:
            archive = convert(path, Path(directory) / 'image.tar', image)
            run(['sudo', 'ctr', '--namespace', 'k8s.io', 'images', 'import',
                 '--platform', 'linux/amd64', '--base-name', image['repository'], str(archive)])
        if not installed(image, run):
            raise ValueError('srsRAN import did not expose the approved runtime reference in containerd k8s.io')
        return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['verify', 'convert', 'import'])
    parser.add_argument('archive', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--runtime-dir', type=Path, default=ROOT / '.runtime')
    args = parser.parse_args()
    image = policy()['components']['srsran']
    if args.command == 'verify':
        inspect_archive(args.archive, image)
    elif args.command == 'convert':
        if args.output is None:
            parser.error('convert requires --output')
        convert(args.archive, args.output, image)
    else:
        from installer.install import Runner
        import_image(args.archive, args.runtime_dir, Runner().run, image)
    print('Approved srsRAN identity: ' + reference(image))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, tarfile.TarError):
        raise SystemExit('ERROR: approved srsRAN image validation/import failed; no image contents printed') from None
