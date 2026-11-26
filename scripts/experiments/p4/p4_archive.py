"""Archive and verify P4 experiment evidence (local files only; never touches OSM or Kubernetes).

create  copies the given source directories into a new archive directory, writes SHA256SUMS
        and MANIFEST.json, re-verifies every copy against its source and makes the archive
        read-only. It refuses an existing archive directory, symbolic links, special files
        and files that look like credentials or key material, before copying anything.
verify  recomputes SHA256SUMS of an archive and re-checks every evidence summary.json
        (evidence_sha256) inside it; any missing, extra, changed or unreadable file fails.

Every problem is reported and makes the command exit with status 1; nothing is skipped.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import time

SUMS, MANIFEST, FILES, INCOMPLETE = 'SHA256SUMS', 'MANIFEST.json', 'files', 'INCOMPLETE'
SENSITIVE_NAME = re.compile(r'(password|passwd|secret-key|token|credential|kubeconfig|id_rsa|id_ed25519|'
                            r'\.key$|\.pem$|\.crt$|\.p12$|\.pfx$|osm-ca)', re.IGNORECASE)
SENSITIVE_CONTENT = [
    (re.compile(rb'-----BEGIN [A-Z ]*(PRIVATE KEY|CERTIFICATE)-----'), 'PEM key or certificate'),
    (re.compile(rb'client-(key|certificate)-data'), 'kubeconfig credential'),
    (re.compile(rb'"(password|passwd|token|bearer|access_token)"\s*:', re.IGNORECASE), 'credential field'),
]


class ArchiveError(RuntimeError):
    pass


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def secret_data(data):
    """Kubernetes Secret objects with data/stringData in a JSON document (fixtures must strip it)."""
    try:
        value = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        return False
    found = []

    def walk(node):
        if isinstance(node, dict):
            if node.get('kind') == 'Secret' and (node.get('data') or node.get('stringData')):
                found.append(node.get('metadata', {}).get('name'))
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)
    walk(value)
    return bool(found)


def scan(sources):
    """[(relative path, source path)] of every file, or ArchiveError listing every problem."""
    problems, files, names = [], [], set()
    for source in sources:
        source = Path(source)
        if source.is_symlink() or not source.is_dir():
            problems.append(str(source) + ': not a directory (or a symbolic link)')
            continue
        if source.name in names:
            problems.append(str(source) + ': two sources share the name ' + source.name)
        names.add(source.name)
        for root, directories, entries in os.walk(source, followlinks=False):
            for name in sorted(directories + entries):
                path = Path(root) / name
                info = path.lstat()
                relative = Path(source.name) / path.relative_to(source)
                if stat.S_ISLNK(info.st_mode):
                    problems.append(str(relative) + ': symbolic link')
                elif stat.S_ISDIR(info.st_mode):
                    continue
                elif not stat.S_ISREG(info.st_mode):
                    problems.append(str(relative) + ': not a regular file')
                elif SENSITIVE_NAME.search(name):
                    problems.append(str(relative) + ': file name suggests credentials or key material')
                else:
                    try:
                        data = path.read_bytes()
                    except OSError as error:
                        problems.append(str(relative) + ': unreadable (' + error.strerror + ')')
                        continue
                    for pattern, label in SENSITIVE_CONTENT:
                        if pattern.search(data):
                            problems.append(str(relative) + ': contains ' + label)
                    if name.endswith('.json') and secret_data(data):
                        problems.append(str(relative) + ': contains Kubernetes Secret data')
                    files.append((relative, path))
    if problems:
        raise ArchiveError('; '.join(problems))
    return sorted(files)


def summary_problems(root):
    """Re-check every evidence summary.json under root against the files it lists."""
    problems = []
    for summary in sorted(Path(root).rglob('summary.json')):
        try:
            listed = json.loads(summary.read_text()).get('evidence_sha256') or {}
        except (OSError, ValueError) as error:
            problems.append(str(summary.relative_to(root)) + ': unreadable summary (' + str(error)[:100] + ')')
            continue
        for name, digest in sorted(listed.items()):
            path = summary.parent / name
            if not path.is_file():
                problems.append(str(path.relative_to(root)) + ': listed in summary.json but missing')
            elif sha256(path) != digest:
                problems.append(str(path.relative_to(root)) + ': differs from summary.json')
    return problems


def create(sources, output, clock=time):
    output = Path(output)
    files = scan(sources)                       # refuses before anything is written
    if not files:
        raise ArchiveError('No files to archive')
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        output.mkdir()
    except FileExistsError:
        raise ArchiveError('Archive directory already exists: ' + str(output)) from None
    (output / INCOMPLETE).write_text('archive creation did not finish\n')
    lines, problems = [], []
    for relative, source in files:
        target = output / FILES / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        digest = sha256(target)
        if digest != sha256(source):
            problems.append(str(relative) + ': changed while being copied')
        lines.append(digest + '  ' + str(Path(FILES) / relative))
    problems += summary_problems(output / FILES)
    (output / SUMS).write_text('\n'.join(lines) + '\n')
    manifest = {'created': clock.time(), 'sources': [str(Path(s).resolve()) for s in sources], 'files': len(files),
                'sha256sums': sha256(output / SUMS), 'problems': problems}
    (output / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    if problems:
        raise ArchiveError('Archive written to ' + str(output) + ' but not verified: ' + '; '.join(problems))
    (output / INCOMPLETE).unlink()
    for path in sorted(output.rglob('*'), reverse=True):
        path.chmod(path.stat().st_mode & ~0o222)
    output.chmod(output.stat().st_mode & ~0o222)
    return manifest


def verify(archive):
    archive = Path(archive)
    problems = []
    if (archive / INCOMPLETE).exists():
        problems.append('archive creation did not finish')
    try:
        listed = {}
        for line in (archive / SUMS).read_text().splitlines():
            digest, _, name = line.partition('  ')
            if not re.fullmatch(r'[0-9a-f]{64}', digest) or not name or name in listed:
                problems.append('malformed SHA256SUMS line: ' + line[:100])
                continue
            listed[name] = digest
    except OSError as error:
        raise ArchiveError(SUMS + ' unreadable: ' + str(error)) from None
    present = {str(p.relative_to(archive)) for p in (archive / FILES).rglob('*') if p.is_file() or p.is_symlink()}
    for name in sorted(set(listed) - present):
        problems.append(name + ': missing')
    for name in sorted(present - set(listed)):
        problems.append(name + ': not listed in ' + SUMS)
    for name in sorted(set(listed) & present):
        path = archive / name
        if path.is_symlink() or not path.is_file():
            problems.append(name + ': not a regular file')
        elif sha256(path) != listed[name]:
            problems.append(name + ': checksum mismatch')
    problems += summary_problems(archive / FILES)
    try:
        manifest = json.loads((archive / MANIFEST).read_text())
        if manifest.get('sha256sums') != sha256(archive / SUMS):
            problems.append(MANIFEST + ': does not match ' + SUMS)
    except (OSError, ValueError) as error:
        problems.append(MANIFEST + ' unreadable: ' + str(error)[:100])
    if problems:
        raise ArchiveError('; '.join(problems))
    return {'files': len(listed), 'verified': True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest='command', required=True)
    make = sub.add_parser('create')
    make.add_argument('--source', action='append', required=True, type=Path, help='directory to archive (repeat)')
    make.add_argument('--output', required=True, type=Path, help='new archive directory (must not exist)')
    check = sub.add_parser('verify')
    check.add_argument('archive', type=Path)
    args = parser.parse_args(argv)
    try:
        result = create(args.source, args.output) if args.command == 'create' else verify(args.archive)
    except ArchiveError as error:
        print(json.dumps({'ok': False, 'error': str(error)}, indent=2))
        return 1
    print(json.dumps(dict(result, ok=True), indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
