"""Bounded read-only Open5GS Ninja linker response-file evidence collector.

Collects data for independent review; does NOT authorize compiling/linking.
No subprocess, shell, network, or Ninja execution occurs here.
"""
import hashlib
import os
import re
import stat
from pathlib import Path

from build_policy import Blocked
from open5gs_graph_gate import OPEN5GS_RESPONSE_FILES

MANIFEST_LIMIT = 32 * 1024 * 1024
BLOCK_LIMIT = 256 * 1024
RSP_LIMIT = 256 * 1024
TARGETS = {
    'lib/asn1c/ngap/libogsasn1c-ngap.so.2.7.5':
        'lib/asn1c/ngap/libogsasn1c-ngap.so.2.7.5.rsp',
    'lib/sbi/openapi/libogssbi-openapi.so.2.7.5':
        'lib/sbi/openapi/libogssbi-openapi.so.2.7.5.rsp',
}


def _read_regular(root, rel, limit):
    """Use no-follow open and an fd-relative path; never follow symlinks."""
    if not rel or Path(rel).is_absolute() or any(p in ('.', '..') for p in Path(rel).parts):
        raise Blocked('OPEN5GS_RSP_UNSAFE_PATH')
    base = Path(root).resolve(strict=True)
    if not base.is_dir():
        raise Blocked('OPEN5GS_RSP_MISSING_BUILD_DIR')
    parts = Path(rel).parts
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_CLOEXEC', 0)
    # Traverse every parent with O_DIRECTORY|O_NOFOLLOW, preventing symlink traversal.
    dirs = []
    try:
        fd = os.open(base, flags | os.O_DIRECTORY)
        dirs.append(fd)
        for part in parts[:-1]:
            try:
                fd = os.open(part, flags | os.O_DIRECTORY, dir_fd=fd)
            except FileNotFoundError:
                return None
            dirs.append(fd)
        try:
            file_fd = os.open(parts[-1], flags | getattr(os, 'O_NONBLOCK', 0), dir_fd=fd)
        except FileNotFoundError:
            return None
        try:
            st = os.fstat(file_fd)
            if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_size > limit:
                raise Blocked('OPEN5GS_RSP_UNSAFE_OR_OVERSIZED_FILE')
            raw = os.read(file_fd, limit + 1)
            if len(raw) > limit or len(raw) != st.st_size:
                raise Blocked('OPEN5GS_RSP_SIZE_OR_RACE')
            if os.fstat(file_fd).st_size != st.st_size:
                raise Blocked('OPEN5GS_RSP_FILE_CHANGED')
            return raw
        finally:
            os.close(file_fd)
    except (NotADirectoryError, IsADirectoryError, OSError) as error:
        if isinstance(error, Blocked):
            raise
        raise Blocked('OPEN5GS_RSP_UNSAFE_PATH_OR_IO:' + type(error).__name__) from error
    finally:
        for directory_fd in reversed(dirs):
            os.close(directory_fd)


def _block_dictionary(text):
    """Select Ninja build/rule blocks without attempting shell evaluation."""
    # A Ninja '$' newline is a logical-line continuation, not a new top-level block.
    lines = text.splitlines(keepends=True)
    blocks = []
    current = []
    for line in lines:
        if len(line) > BLOCK_LIMIT:
            raise Blocked('OPEN5GS_RSP_OVERSIZED_MANIFEST_LINE')
        if current and line and not line[0].isspace() and not current[-1].rstrip('\r\n').endswith('$'):
            blocks.append(''.join(current))
            current = []
        current.append(line)
    if current:
        blocks.append(''.join(current))
    rules = {}
    builds = {}
    for block in blocks:
        header = block.split('\n', 1)[0]
        if header.startswith('rule '):
            name = header[5:].strip()
            if name in rules:
                raise Blocked('OPEN5GS_RSP_DUPLICATE_RULE')
            rules[name] = block
        elif header.startswith('build '):
            # Ninja's colon separates outputs from rule; our pinned output paths have no ':'
            match = re.match(r'^build (.+?):\s+([A-Za-z0-9_.-]+)(?:\s|$)', header)
            if not match:
                continue
            outputs, rule = match.groups()
            for target in TARGETS:
                if target in outputs.split():
                    if target in builds:
                        raise Blocked('OPEN5GS_RSP_DUPLICATE_TARGET')
                    builds[target] = (rule, block)
    return rules, builds


def collect_rsp_review(build_dir):
    """Only captures bounded evidence, with an explicit non-admission result."""
    manifest = _read_regular(build_dir, 'build.ninja', MANIFEST_LIMIT)
    if manifest is None:
        raise Blocked('OPEN5GS_RSP_MANIFEST_MISSING')
    if b'\x00' in manifest or b'\r' in manifest:
        raise Blocked('OPEN5GS_RSP_MANIFEST_ENCODING')
    try:
        raw_text = manifest.decode('utf-8', errors='strict')
    except UnicodeError as error:
        raise Blocked('OPEN5GS_RSP_MANIFEST_ENCODING') from error
    rules, builds = _block_dictionary(raw_text)
    if set(builds) != set(TARGETS):
        raise Blocked('OPEN5GS_RSP_TARGET_DEFINITION_MISSING')
    if {'@' + x for x in TARGETS.values()} != OPEN5GS_RESPONSE_FILES:
        raise Blocked('OPEN5GS_RSP_PIN_MISMATCH')
    report = {'classification': 'REVIEW_REQUIRED', 'native_compilation_authorized': False,
              'response_files_complete': False,
              'build_ninja_sha256': hashlib.sha256(manifest).hexdigest(),
              'build_ninja_bytes': len(manifest), 'targets': {}}
    present = 0
    for target, rsp_path in TARGETS.items():
        rule_name, edge = builds[target]
        rule = rules.get(rule_name)
        if rule is None or not re.search(r'^\s+rspfile\s*=', rule, re.M) or not re.search(r'^\s+rspfile_content\s*=', rule, re.M):
            raise Blocked('OPEN5GS_RSP_RULE_NOT_REVIEWABLE')
        if len(edge.encode()) > BLOCK_LIMIT or len(rule.encode()) > BLOCK_LIMIT:
            raise Blocked('OPEN5GS_RSP_RULE_OR_EDGE_OVERSIZED')
        raw_rsp = _read_regular(build_dir, rsp_path, RSP_LIMIT)
        row = {'rule_name': rule_name,
               'rule_sha256': hashlib.sha256(rule.encode()).hexdigest(),
               'rule_text': rule, 'build_edge_sha256': hashlib.sha256(edge.encode()).hexdigest(),
               'build_edge_text': edge, 'response_file': rsp_path}
        if raw_rsp is None:
            row['response_file_state'] = 'NOT_MATERIALIZED'
        else:
            if b'\x00' in raw_rsp or b'\r' in raw_rsp:
                raise Blocked('OPEN5GS_RSP_UNSAFE_RESPONSE_BYTES')
            try:
                rsp_text = raw_rsp.decode('utf-8', errors='strict')
            except UnicodeError as error:
                raise Blocked('OPEN5GS_RSP_UNSAFE_RESPONSE_BYTES') from error
            row.update(response_file_state='PRESENT_UNREVIEWED',
                       response_file_sha256=hashlib.sha256(raw_rsp).hexdigest(),
                       response_file_bytes=len(raw_rsp), response_file_text=rsp_text)
            present += 1
        report['targets'][target] = row
    report['response_files_complete'] = present == len(TARGETS)
    return report
