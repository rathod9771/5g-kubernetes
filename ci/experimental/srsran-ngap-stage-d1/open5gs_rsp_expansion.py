"""Bounded, read-only audit of Ninja's *expanded* RSP tool output.

Input MUST be produced by `ninja -C <build> -t compdb -x c_LINKER_RSP`,
which parses Ninja metadata without compiling or linking. The command is
executed separately, inside the already isolated worker.
This never authorizes native compilation.
"""
import hashlib
import json
import re
import shlex
from pathlib import PurePosixPath

from build_policy import Blocked
from open5gs_rsp_review import TARGETS

MAX_COMPDB_BYTES = 12 * 1024 * 1024
MAX_COMPDB_RECORDS = 1024
MAX_LINK_BYTES = 256 * 1024
MAX_TOKENS = 4096
EXPECTED_OBJECTS = {
    'lib/asn1c/ngap/libogsasn1c-ngap.so.2.7.5': 1062,
    'lib/sbi/openapi/libogssbi-openapi.so.2.7.5': 1350,
}
HEX = re.compile(r'^[0-9a-f]{64}$')
BAD_FLAGS = ('-fplugin', '-wrapper', '-specs', '--specs', '--plugin',
             '-Xlinker', '-Wl,@', '-Wl,-T', '-Wl,--script',
             '-Wl,-z,execstack', '--sysroot', '-B', '-Wl,--dynamic-linker')


def _sha(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _relative_object(token):
    path = PurePosixPath(token)
    return (not path.is_absolute() and '..' not in path.parts
            and token.endswith(('.c.o', '.cc.o', '.cpp.o', '.o')))


def _edge_objects(edge, target):
    header = edge.splitlines()[0]
    prefix = 'build ' + target + ': c_LINKER_RSP '
    if not header.startswith(prefix):
        raise Blocked('RSP_EXPANSION_EDGE_DRIFT')
    rhs = header[len(prefix):].split(' | ', 1)[0]
    objects = rhs.split()
    if len(objects) != EXPECTED_OBJECTS[target] or any(not _relative_object(x) for x in objects):
        raise Blocked('RSP_EXPANSION_OBJECT_SET')
    if len(objects) != len(set(objects)):
        raise Blocked('RSP_EXPANSION_DUPLICATE_OBJECT')
    return objects


def review_expanded_compdb(compdb_text, graph_report):
    """Review exact tool output and correlate to pinned Ninja build edges."""
    if not isinstance(compdb_text, str) or not isinstance(graph_report, dict):
        raise Blocked('RSP_EXPANSION_INVALID_INPUT')
    if len(compdb_text.encode('utf-8')) > MAX_COMPDB_BYTES:
        raise Blocked('RSP_EXPANSION_TOOL_OUTPUT_OVERSIZED')
    if not (isinstance(graph_report.get('build_ninja_sha256'), str)
            and HEX.fullmatch(graph_report['build_ninja_sha256'])):
        raise Blocked('RSP_EXPANSION_MANIFEST_HASH_MISSING')
    try:
        entries = json.loads(compdb_text)
    except (ValueError, TypeError) as e:
        raise Blocked('RSP_EXPANSION_TOOL_OUTPUT_INVALID_JSON') from e
    if not isinstance(entries, list) or len(entries) > MAX_COMPDB_RECORDS:
        raise Blocked('RSP_EXPANSION_TOOL_OUTPUT_COUNT')
    results = {}
    selected = set(TARGETS)
    for row in entries:
        if not isinstance(row, dict) or not isinstance(row.get('output'), str):
            raise Blocked('RSP_EXPANSION_MALFORMED_ENTRY')
        target = row['output']
        if target not in selected:
            continue
        if target in results:
            raise Blocked('RSP_EXPANSION_DUPLICATE_TARGET')
        command = row.get('command')
        if not isinstance(command, str) or len(command.encode('utf-8')) > MAX_LINK_BYTES:
            raise Blocked('RSP_EXPANSION_COMMAND_BUDGET')
        if any(x in command for x in ('\x00', '\n', '\r', '`', ';', '|', '<', '>')):
            raise Blocked('RSP_EXPANSION_COMMAND_METACHAR')
        try:
            argv = shlex.split(command, posix=True)
        except ValueError as e:
            raise Blocked('RSP_EXPANSION_SHELL_QUOTING') from e
        if len(argv) > MAX_TOKENS or not argv or argv[0] != 'cc':
            raise Blocked('RSP_EXPANSION_COMPILER_DRIFT')
        if any(a.startswith('@') or any(a.startswith(b) for b in BAD_FLAGS) for a in argv[1:]):
            raise Blocked('RSP_EXPANSION_UNREVIEWED_FLAG_OR_NESTED_RSP')
        if argv.count('-o') != 1 or argv[argv.index('-o')+1] != target:
            raise Blocked('RSP_EXPANSION_OUTPUT_DRIFT')
        if argv.count('-shared') != 1 or argv.count('-fPIC') != 1:
            raise Blocked('RSP_EXPANSION_SHARED_LINK_FLAGS')
        graph_target = graph_report.get('targets', {}).get(target)
        if not isinstance(graph_target, dict) or graph_target.get('rule_name') != 'c_LINKER_RSP':
            raise Blocked('RSP_EXPANSION_GRAPH_TARGET_MISMATCH')
        if _sha(graph_target['rule_text']) != graph_target.get('rule_sha256'):
            raise Blocked('RSP_EXPANSION_RULE_HASH')
        if _sha(graph_target['build_edge_text']) != graph_target.get('build_edge_sha256'):
            raise Blocked('RSP_EXPANSION_EDGE_HASH')
        if 'rspfile_content = $ARGS -o $out $in $LINK_ARGS' not in graph_target['rule_text']:
            raise Blocked('RSP_EXPANSION_RULE_TEMPLATE_DRIFT')
        expected = _edge_objects(graph_target['build_edge_text'], target)
        actual = [x for x in argv if x.endswith('.o')]
        if actual != expected:
            raise Blocked('RSP_EXPANSION_LINK_INPUTS_MISMATCH')
        results[target] = {
            'argv_sha256': _sha(json.dumps(argv, ensure_ascii=True, separators=(',', ':'))),
            'command_sha256': _sha(command),
            'command_bytes': len(command.encode('utf-8')),
            'argv_count': len(argv),
            'object_count': len(actual),
            'resolved_command': command,
            'classification': 'INDEPENDENT_REVIEW_REQUIRED',
        }
    if set(results) != selected:
        raise Blocked('RSP_EXPANSION_TARGET_SET_INCOMPLETE')
    return {
        'classification': 'INDEPENDENT_REVIEW_REQUIRED',
        'native_compilation_authorized': False,
        'source': 'ninja -t compdb -x c_LINKER_RSP (read-only)',
        'build_ninja_sha256': graph_report['build_ninja_sha256'],
        'tool_stdout_sha256': _sha(compdb_text),
        'targets': results,
    }
