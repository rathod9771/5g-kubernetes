"""D2B admission from independently reviewed exact command evidence.

Only called after source, dependency, graph, and RSP audits pass.
Not a general executable allowlist or runtime/protocol authorization.
"""
import hashlib,json,shlex
from pathlib import Path
from build_policy import Blocked

REVIEW_RUNS=(44273531132,44274192878,44278061236,44278442399)
PINNED_OPEN5GS_GRAPH='9f086ee1c67d2716a69b5ccf45379231897c29a059f2c51d2d3bc5d257e8fd6c'
PINNED_EXPANSION_STDOUT='b2eb1cd7664e7bbbc70cdad4d642c349d9f9785abf14ce38aacc2c14627aa310'
PINNED_COMMANDS={
    'lib/asn1c/ngap/libogsasn1c-ngap.so.2.7.5':{
        'command_sha256':'784bd8af203dd0dd0b9e26c2410235a1ad6b63350e7626caf1f35d37d099a00a',
        'argv_sha256':'5553994db8c961eced570ea8c144ffd06ac0898efff9f91b3420e89a517bad47','object_count':1062},
    'lib/sbi/openapi/libogssbi-openapi.so.2.7.5':{
        'command_sha256':'82cc28eec4b65f9799c75a26470be14ee179c7d395075a7f4729671605f4516c',
        'argv_sha256':'5afc5b7b4c81171b9f070633df2f7bd6f06fe179e26284dfa7a3fac94a725830','object_count':1350}}

def _sha(data):
    return hashlib.sha256(data.encode('utf-8')).hexdigest()

def admit_reviewed_open5gs_graph(graph,review,expanded):
    """All exact pins are required; this returns review evidence, not runtime permission."""
    if not isinstance(graph,str) or not isinstance(review,dict) or not isinstance(expanded,dict):
        raise Blocked('D2B_NATIVE_INVALID_REVIEW_INPUT')
    if _sha(graph)!=PINNED_OPEN5GS_GRAPH:raise Blocked('D2B_NATIVE_GRAPH_DRIFT')
    if review.get('classification')!='REVIEW_REQUIRED' or review.get('native_compilation_authorized') is not False:
        raise Blocked('D2B_NATIVE_REVIEW_STATUS_DRIFT')
    if expanded.get('classification')!='INDEPENDENT_REVIEW_REQUIRED' or expanded.get('native_compilation_authorized') is not False:
        raise Blocked('D2B_NATIVE_EXPANSION_STATUS_DRIFT')
    if expanded.get('source')!='ninja -t compdb -x c_LINKER_RSP (read-only)':
        raise Blocked('D2B_NATIVE_UNREVIEWED_EXPANSION_SOURCE')
    if expanded.get('tool_stdout_sha256')!=PINNED_EXPANSION_STDOUT:
        raise Blocked('D2B_NATIVE_EXPANSION_TOOL_OUTPUT_DRIFT')
    if not review.get('build_ninja_sha256') or expanded.get('build_ninja_sha256')!=review['build_ninja_sha256']:
        raise Blocked('D2B_NATIVE_NINJA_MANIFEST_DRIFT')
    if set(review.get('targets',{}))!=set(PINNED_COMMANDS) or set(expanded.get('targets',{}))!=set(PINNED_COMMANDS):
        raise Blocked('D2B_NATIVE_TARGET_SET_DRIFT')
    fingerprints={}
    for target,pins in PINNED_COMMANDS.items():
        record=expanded['targets'][target]
        if not isinstance(record,dict) or record.get('classification')!='INDEPENDENT_REVIEW_REQUIRED':
            raise Blocked('D2B_NATIVE_UNREVIEWED_TARGET:'+target)
        command=record.get('resolved_command')
        if not isinstance(command,str) or len(command.encode('utf-8'))>256*1024:
            raise Blocked('D2B_NATIVE_COMMAND_BUDGET:'+target)
        if _sha(command)!=pins['command_sha256'] or record.get('command_sha256')!=pins['command_sha256']:
            raise Blocked('D2B_NATIVE_LINK_COMMAND_DRIFT:'+target)
        try:argv=shlex.split(command)
        except ValueError as e:raise Blocked('D2B_NATIVE_LINK_QUOTING:'+target) from e
        if _sha(json.dumps(argv,ensure_ascii=True,separators=(',',':')))!=pins['argv_sha256'] or record.get('argv_sha256')!=pins['argv_sha256']:
            raise Blocked('D2B_NATIVE_LINK_ARGV_DRIFT:'+target)
        if record.get('object_count')!=pins['object_count']:raise Blocked('D2B_NATIVE_OBJECT_COUNT_DRIFT:'+target)
        fingerprints[target]=pins['command_sha256']
    return {'classification':'PINNED_COMMANDS_APPROVED_FOR_ONE_BUILD_ONLY',
            'protocol_runtime_authorized':False,'native_tests_authorized':False,
            'review_evidence_run_ids':list(REVIEW_RUNS),
            'source_command_graph_sha256':PINNED_OPEN5GS_GRAPH,
            'ninja_manifest_sha256_runtime':review['build_ninja_sha256'],
            'expanded_tool_stdout_sha256':PINNED_EXPANSION_STDOUT,
            'approved_link_command_sha256':fingerprints}

def verify_native_terminal_guard(root,supervisor,usage):
    """Reject successful build evidence without an independently bound quiescent snapshot."""
    path=Path(root)/'post-worker-guard-accounting.json'
    if path.is_symlink() or not path.is_file() or path.stat().st_size>16*1024:
        raise Blocked('D2B_NATIVE_MISSING_GUARD_ACCOUNTING')
    expected=supervisor.get('evidence_sha256',{}).get('post-worker-guard-accounting.json')
    if not expected or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
        raise Blocked('D2B_NATIVE_UNBOUND_GUARD_ACCOUNTING')
    try:record=json.loads(path.read_text())
    except (UnicodeError,ValueError) as e:raise Blocked('D2B_NATIVE_MALFORMED_GUARD_ACCOUNTING') from e
    proof=record.get('proof',{})
    if record.get('classification')!='POST_WORKER_SNAPSHOT_REQUIRES_TERMINAL_CONFIRMATION':
        raise Blocked('D2B_NATIVE_GUARD_CLASSIFICATION')
    if (not isinstance(proof,dict) or proof.get('worker_exit_code')!=0
            or proof.get('mode')!='POST_WORKER_QUIESCENT_GUARD_HELD'
            or proof.get('worker_accounting_complete') is not True
            or proof.get('terminal_cgroup_empty') is not False
            or proof.get('native_compilation_authorized') is not False):
        raise Blocked('D2B_NATIVE_GUARD_PROOF_MISMATCH')
    if (not isinstance(usage,dict)
            or usage.get('accounting_mode')!='POST_WORKER_QUIESCENT_GUARD_HELD'
            or usage.get('terminal_accounting_complete') is not True
            or usage.get('guard_only_at_capture') is not True
            or usage.get('capture_boundary')!='POST_WORKER_PRE_GUARD_EXIT'):
        raise Blocked('D2B_NATIVE_CGROUP_SAMPLE_NOT_TERMINAL')
    actual=usage.get('usage',{})
    if actual.get('oom_kill')!=0 or actual.get('pids_limit_events')!=0:
        raise Blocked('D2B_NATIVE_RESOURCE_LIMIT_EVENT')
    if (supervisor.get('exit_code')!=0 or supervisor.get('systemd_result')!='success'
            or supervisor.get('signal') is not None or supervisor.get('timeout') is not False):
        raise Blocked('D2B_NATIVE_TERMINAL_EXIT_MISMATCH')
    return True
