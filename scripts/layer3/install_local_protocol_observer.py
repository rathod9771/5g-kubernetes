#!/usr/bin/env python3
"""Reproducible, user-only installer for the non-H-CRAN Layer 3 read-only observer.

Default --check changes nothing. --install copies exactly one Python script to
~/.local/bin and writes a SHA256 receipt. No pip, sudo, Docker, or kubectl writes.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile

SOURCE = Path(__file__).resolve().parent / "local_protocol_observer.py"
NAME = "layer3-protocol-observer"
RECEIPT = NAME + ".install.json"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for buf in iter(lambda: stream.read(65536), b""):
            h.update(buf)
    return h.hexdigest()


def check_source():
    if sys.version_info < (3, 10):
        raise RuntimeError("Python 3.10+ required")
    if SOURCE.is_symlink() or not SOURCE.is_file():
        raise RuntimeError("source script missing or a symlink")
    source_bytes = SOURCE.read_bytes()
    if not source_bytes.startswith(b"#!/usr/bin/env python3\n"):
        raise RuntimeError("unexpected source file header")
    compile(source_bytes, str(SOURCE), "exec")
    return sha256(SOURCE)


def ensure_dir(path):
    # User-specific install only. Do not follow an attacker-controlled symlink.
    if path.is_symlink():
        raise RuntimeError("install directory must not be a symlink")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise RuntimeError("invalid installation directory")
    if path.stat().st_uid != os.getuid():
        raise RuntimeError("installation directory must belong to the current user")


def write_atomic(path, content, mode):
    if path.is_symlink():
        raise RuntimeError("refusing symlink destination")
    fd, temp_name = tempfile.mkstemp(prefix=".layer3-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temp_name, mode)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    group = p.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="Default: verify source and report status")
    group.add_argument("--install", action="store_true", help="Install owner-only command and hash receipt")
    group.add_argument("--uninstall", action="store_true", help="Remove only matching installed command")
    p.add_argument("--prefix", default=str(Path.home() / ".local" / "bin"),
                   help="User-owned install directory (default: ~/.local/bin)")
    args = p.parse_args(argv)
    target_dir = Path(args.prefix).expanduser().absolute()
    target = target_dir / NAME
    receipt = target_dir / RECEIPT

    if os.geteuid() == 0:
        raise RuntimeError("do not use sudo/root; installer is current-user only")
    if args.uninstall:
        if not receipt.is_file() or receipt.is_symlink():
            raise RuntimeError("no trusted installation receipt, refusing uninstall")
        data = json.loads(receipt.read_text())
        if data.get("source_sha256") != sha256(target):
            raise RuntimeError("installed file changed; refusing to delete")
        if target.is_symlink():
            raise RuntimeError("installed command is a symlink")
        target.unlink()
        receipt.unlink()
        print("UNINSTALLED user-only Layer 3 observer; Kubernetes unaffected")
        return 0

    expected = check_source()
    status = {
        "schema": 1,
        "source_sha256": expected,
        "destination": str(target),
        "mode": "user-only-read-only-install",
        "remote_download": False,
        "kubernetes_mutations": 0,
        "install_executed": bool(args.install),
    }
    if not args.install:
        status["existing_install_matches"] = (target.is_file() and not target.is_symlink()
                                               and sha256(target) == expected)
        print(json.dumps(status, indent=2))
        return 0
    ensure_dir(target_dir)
    if target.is_symlink() or receipt.is_symlink():
        raise RuntimeError("refusing symlink installation destination")
    if target.exists() and (not target.is_file() or target.stat().st_uid != os.getuid()):
        raise RuntimeError("refusing to overwrite unowned or nonregular destination")
    if receipt.exists() and (not receipt.is_file() or receipt.stat().st_uid != os.getuid()):
        raise RuntimeError("refusing to overwrite unowned or nonregular receipt")
    write_atomic(target, SOURCE.read_bytes(), 0o700)
    if sha256(target) != expected:
        raise RuntimeError("installed source hash mismatch")
    write_atomic(receipt, (json.dumps(status, indent=2) + "\n").encode(), 0o600)
    print("INSTALLED " + str(target))
    print("SHA256 " + expected)
    print("Run --help for read-only inventory/observation. No cluster operation executed.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print("INSTALL_BLOCKED: " + str(exc), file=sys.stderr)
        sys.exit(2)
