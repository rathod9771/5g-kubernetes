"""Fail-closed admission for the *exact* pinned Open5GS AMF/NRF Ninja graph.

The srsRAN graph continues through the existing, stricter audit_graph().
The fingerprint is from GitHub Actions run 44255524672, with source and
reviewed build-dependency pins unchanged. Graph changes require fresh review.
The two named Ninja linker response files are represented by their command
references, NOT their expanded contents; these must be reviewed separately
before any additional native build is authorized.
"""
import hashlib
import re
import shlex
from collections import Counter

from build_policy import Blocked

OPEN5GS_GRAPH_SHA256 = '9f086ee1c67d2716a69b5ccf45379231897c29a059f2c51d2d3bc5d257e8fd6c'
OPEN5GS_GRAPH_BYTES = 3873485
OPEN5GS_GRAPH_LINES = 2671
OPEN5GS_GRAPH_TOOLS = {'cc': 2654, 'meson': 15, 'archive': 2}
OPEN5GS_RESPONSE_FILES = {
    '@lib/asn1c/ngap/libogsasn1c-ngap.so.2.7.5.rsp',
    '@lib/sbi/openapi/libogssbi-openapi.so.2.7.5.rsp',
}
_PROHIBITED = re.compile(
    r'libUnity\.|vendor/Unity|ThrowTheSwitch|promtest|parson|'
    r'gtest_discover_tests_impl|--gtest_list_tests', re.I
)
_PLUGIN_FLAGS = ('-fplugin', '-wrapper', '--plugin', '-specs', '--specs',
                 '-Xlinker', '-Wl,@', '-Wl,-T', '-Wl,--script')
_ARCHIVE_COMMANDS = {
    'amf': 'rm -f src/amf/libamf.a && gcc-ar csrDT src/amf/libamf.a ',
    'nrf': 'rm -f src/nrf/libnrf.a && gcc-ar csrDT src/nrf/libnrf.a ',
}


def audit_open5gs_graph(commands):
    """Accept only the reviewed byte-for-byte graph and expected command forms.

    This is an *offline graph preflight*, not evidence that generated .rsp file
    contents were independently audited or that compilation succeeded.
    """
    if not isinstance(commands, str):
        raise Blocked('UNREVIEWED_OPEN5GS_COMMAND_GRAPH')
    raw = commands.encode('utf-8')
    if (len(raw) != OPEN5GS_GRAPH_BYTES
            or raw.count(b'\n') != OPEN5GS_GRAPH_LINES
            or hashlib.sha256(raw).hexdigest() != OPEN5GS_GRAPH_SHA256):
        raise Blocked('UNREVIEWED_OPEN5GS_COMMAND_GRAPH')
    if _PROHIBITED.search(commands):
        raise Blocked('PROHIBITED_EXECUTION_GRAPH')

    tools = Counter()
    rsp = set()
    for line in commands.splitlines():
        if not line or any(c in line for c in ('\r', '\0', '`', ';', '|', '<', '>')):
            raise Blocked('UNREVIEWED_OPEN5GS_SHELL_OPERATION')
        if line.startswith('cc '):
            tokens = shlex.split(line)
            if tokens[0] != 'cc':
                raise Blocked('UNREVIEWED_OPEN5GS_COMPILER')
            if any(arg.startswith(_PLUGIN_FLAGS) for arg in tokens[1:]):
                raise Blocked('UNREVIEWED_OPEN5GS_COMPILER_FLAG')
            rsp.update(arg for arg in tokens[1:] if arg.startswith('@'))
            tools['cc'] += 1
        elif line.startswith('/usr/bin/meson --internal symbolextractor '):
            tokens = shlex.split(line)
            if tokens[:3] != ['/usr/bin/meson', '--internal', 'symbolextractor']:
                raise Blocked('UNREVIEWED_OPEN5GS_HELPER')
            tools['meson'] += 1
        elif line.startswith(_ARCHIVE_COMMANDS['amf']) or line.startswith(_ARCHIVE_COMMANDS['nrf']):
            prefix = next(v for v in _ARCHIVE_COMMANDS.values() if line.startswith(v))
            tokens = shlex.split(line[len(prefix):])
            if not tokens or any(not x.endswith('.o') for x in tokens):
                raise Blocked('UNREVIEWED_OPEN5GS_ARCHIVE_INPUT')
            if ' && ' in line[len(prefix):]:
                raise Blocked('UNREVIEWED_OPEN5GS_ARCHIVE_CHAIN')
            tools['archive'] += 1
        else:
            raise Blocked('UNREVIEWED_OPEN5GS_TOOL')
    if dict(tools) != OPEN5GS_GRAPH_TOOLS or rsp != OPEN5GS_RESPONSE_FILES:
        raise Blocked('UNREVIEWED_OPEN5GS_TOOL_OR_RESPONSE_FILE_SET')
    return True
