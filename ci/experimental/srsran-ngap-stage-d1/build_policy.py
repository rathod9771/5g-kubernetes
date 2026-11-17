"""Pure fail-closed policy. Import does not configure, probe or build anything."""
import datetime,hashlib,json,math,os,re,shlex
from pathlib import Path
class Blocked(RuntimeError): pass
LIMITS={'memory.max':6*1024**3,'memory.swap.max':0,'cpu.max':'200000 100000','pids.max':256,'disk_bytes':12*1024**3,'wall_seconds':4500}
PHASES=('dependency_acquisition','source_verification','patch_application','cmake_configuration','meson_configuration','srsran_compilation','open5gs_compilation','linkage_verification','binary_inspection','final_evidence_verification')
VERSIONS=('cc','c++','ld','cmake','meson','ninja','pkg-config')
def digest(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def environment(inherited,home,tmp):
    # Reject unsafe input, then construct an allowlist. Never copy PATH/compiler flags/proxies.
    forbidden={'TEST','LD_PRELOAD','LD_LIBRARY_PATH','PYTHONPATH','CMAKE_TOOLCHAIN_FILE','CMAKE_PROJECT_INCLUDE','CC','CXX','CFLAGS','CXXFLAGS','LDFLAGS','CMAKE_ARGS','MESON_ARGS','BASH_ENV','ENV'}
    if forbidden.intersection(inherited): raise Blocked('UNSAFE_INHERITED_ENVIRONMENT')
    return {'PATH':'/usr/bin:/bin','HOME':str(home),'TMPDIR':str(tmp),'LC_ALL':'C.UTF-8','LANG':'C.UTF-8','TZ':'UTC','PYTHONDONTWRITEBYTECODE':'1','CMAKE_BUILD_PARALLEL_LEVEL':'2','MAKEFLAGS':'-j2'}
def resource_admission(e):
    for key in ('cgroup_v2','network_isolated','root_readonly','scratch_capacity_verified','unprivileged','no_capabilities','namespace_private','host_ipc_isolated'):
        if e.get(key) is not True: raise Blocked('MISSING_ENFORCEMENT:'+key)
    for key in ('memory.max','memory.swap.max','cpu.max','pids.max','disk_bytes'):
        if e.get(key)!=LIMITS[key]: raise Blocked('LIMIT_READBACK:'+key)
    if set(e.get('accounting',()))!={'memory.peak','pids.peak','cpu.stat','memory.events','pids.events','disk_used_peak'}: raise Blocked('MISSING_ACCOUNTING')
    return True

def check_dependency_tree(root,policy):
    for name,d in policy['sources'].items():
        directory=Path(root)/name
        for rel,h in d['build_control_hashes'].items():
            if digest(directory/rel)!=h: raise Blocked('DEPENDENCY_SOURCE_DRIFT:'+name+'/'+rel)
        actual={str(p.relative_to(directory)) for p in directory.rglob('*') if p.is_file() and '.git' not in p.parts and control(p)}
        if actual!=set(d['build_control_hashes']): raise Blocked('UNREVIEWED_BUILD_CONTROL:'+name)
    prom=(Path(root)/'prometheus-client-c/prom/CMakeLists.txt').read_text()
    main=(Path(root)/'prometheus-client-c/CMakeLists.txt').read_text()
    if 'if ($ENV{TEST})\n    include(test/CMakeLists.txt)\nendif()' not in prom or 'add_subdirectory (prom)' not in main:raise Blocked('UNITY_GUARD_CHANGED')
    if re.search(r'^\s*add_subdirectory\s*\(\s*(promtest|promhttp)',main,re.M|re.I):raise Blocked('UNEXPECTED_PROM_SUBDIRECTORY')
    if 'add_library(prom SHARED)' not in prom:raise Blocked('LIBPROM_MISSING')
    return True

def control(p):
    return p.name in {'CMakeLists.txt','meson.build','meson_options.txt','configure','configure.ac','Makefile.am','Makefile.in'} or p.suffix in {'.cmake','.in','.sh','.wrap'}

def audit_graph(commands):
    """Conservative shell command grammar; unfamiliar helper invocations block the experiment."""
    if not commands.strip() or len(commands)>16*1024**2:raise Blocked('EMPTY_OR_OVERSIZED_BUILD_GRAPH')
    allowed={'cc','c++','gcc','g++','ar','ranlib','ld','strip','objcopy','flex','bison',':'}
    for line in commands.splitlines():
        if re.search(r'[`$|;<>\n]',line):raise Blocked('UNREVIEWED_SHELL_OPERATION')
        for segment in line.split('&&'):
            if '&' in segment or '(' in segment or ')' in segment:raise Blocked('UNREVIEWED_SHELL_CONTROL')
            t=shlex.split(segment)
            if not t:raise Blocked('EMPTY_COMMAND')
            tool=Path(t[0]).name
            if tool not in {':','cd'} and t[0] not in {'/usr/bin/'+tool,'/bin/'+tool}:raise Blocked('UNTRUSTED_TOOL_PATH')
            if tool=='cd' and len(t)==2 and t[1].startswith('/work/'):continue
            if tool in allowed:
                if any(x.startswith(('-fplugin','-wrapper','--plugin','-specs','--specs')) for x in t[1:]):raise Blocked('UNREVIEWED_TOOL_PLUGIN')
                if tool=='git' and t[1:] not in (['rev-parse','--short','HEAD'],['describe','--always','--dirty']):raise Blocked('UNREVIEWED_GIT_COMMAND')
                continue
            if tool=='cmake' and len(t)>=5 and t[1:4]==['-E','rm','-f'] and all(x.endswith('.a') and '..' not in Path(x).parts for x in t[4:]):continue
            if tool=='cmake' and t[1:]==['-P','/work/srs-build/build_info.cmake']:continue
            if tool=='cmake' and len(t)>=3 and t[1:3] in (['-E','cmake_symlink_library'],['-E','copy_if_different'],['-E','touch']):continue
            # Meson uses a trusted system helper, with fixed non-protocol subcommands.
            if tool=='meson' and len(t)>2 and t[1]=='--internal' and t[2] in {'symbolextractor','depaccumulate'}:continue
            raise Blocked('UNEXPECTED_CUSTOM_COMMAND:'+tool)
    if re.search(r'libUnity\.|vendor/Unity|ThrowTheSwitch|promtest|parson|gtest_discover_tests_impl|--gtest_list_tests',commands,re.I):raise Blocked('PROHIBITED_EXECUTION_GRAPH')
    return True

def unity_generated(build):
    files=list(Path(build).rglob('CMakeCache.txt'))
    if not files:raise Blocked('MISSING_PROM_CONFIGURATION_EVIDENCE')
    alltext='\n'.join(f.read_text(errors='strict') for f in files)
    commands=(Path(build)/'build.ninja').read_text()
    if not re.search(r'\bprom\b',commands):raise Blocked('PROM_TARGET_MISSING')
    if re.search(r'Unity|promTest|promtest|parson|ThrowTheSwitch',alltext+'\n'+commands):raise Blocked('UNITY_OR_UNREVIEWED_TARGET')
    return True

def verify_usage(usage):
    for k in ('memory_peak_bytes','cpu_usage_usec','pids_peak','disk_used_peak_bytes'):
        if type(usage.get(k)) is not int or usage[k]<0:raise Blocked('MISSING_USAGE:'+k)
    for k in ('oom_kill','pids_limit_events'):
        if usage.get(k)!=0:raise Blocked('RESOURCE_LIMIT_HIT:'+k)
    return True

# Realtime and monotonic intervals must agree within 1 second. A clock step outside
# this allowance invalidates admission; timestamps/durations are never repaired.
TIMING_TOLERANCE_SECONDS=1.0
HEX=re.compile(r'^[0-9a-f]{64}$')
def timing(record):
    def timestamp(value):
        if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)',value):raise Blocked('INVALID_TIMESTAMP')
        try:return datetime.datetime.fromisoformat(value.replace('Z','+00:00'))
        except ValueError:raise Blocked('INVALID_TIMESTAMP') from None
    start=timestamp(record.get('started_at'));end=timestamp(record.get('ended_at'))
    elapsed=record.get('elapsed_seconds')
    if type(elapsed) not in (int,float) or not math.isfinite(elapsed) or elapsed<0:raise Blocked('INVALID_MONOTONIC_DURATION')
    wall=(end-start).total_seconds()
    if wall<0 or abs(wall-elapsed)>TIMING_TOLERANCE_SECONDS:raise Blocked('CLOCK_INTERVAL_INCONSISTENT')
    return start,end

def successful(record):
    if record.get('classification')!='PASS' or type(record.get('exit_code')) is not int or record['exit_code']!=0:raise Blocked('EXECUTION_NOT_PASS')
    if 'signal' not in record or record['signal'] is not None or record.get('timeout') is not False:raise Blocked('SIGNAL_TIMEOUT_OR_INCOMPLETE_EXECUTION')
    if record.get('timed_out',False) is not False or record.get('termination_signal') is not None:raise Blocked('CONTRADICTORY_EXECUTION_OUTCOME')
    return timing(record)

def verify_receipt(r,phase):
    required={'phase','started_at','ended_at','elapsed_seconds','command_sha256','exit_code','signal','timeout','validation_completed','resource_limit_status','log','log_sha256','outputs','classification','commands'}
    if not isinstance(r,dict) or not required<=r.keys() or set(r)-required-{'reason'}:raise Blocked('RECEIPT_SCHEMA')
    start,end=successful(r)
    if r['phase']!=phase or r['validation_completed'] is not True:raise Blocked('PHASE_VALIDATION_INCOMPLETE')
    if r['resource_limit_status']!=('NOT_APPLICABLE_ACQUISITION_ONLY' if phase=='dependency_acquisition' else 'VERIFIED'):raise Blocked('MISSING_RESOURCE_EVIDENCE')
    for key in ('command_sha256','log_sha256'):
        if not isinstance(r[key],str) or not HEX.fullmatch(r[key]):raise Blocked('INVALID_RECEIPT_HASH')
    if not isinstance(r['log'],str) or not r['log']:raise Blocked('MISSING_LOG')
    if not isinstance(r['outputs'],dict) or not r['outputs'] or any(not isinstance(k,str) or not k or not isinstance(v,str) or not HEX.fullmatch(v) for k,v in r['outputs'].items()):raise Blocked('MISSING_ARTIFACT_HASHES')
    if not isinstance(r['commands'],list) or len(r['commands'])>256:raise Blocked('INVALID_COMMAND_EVIDENCE')
    required_counts={'dependency_acquisition':18,'patch_application':2,'cmake_configuration':1,'meson_configuration':1,'srsran_compilation':1,'open5gs_compilation':1}
    if len(r['commands'])!=required_counts.get(phase,0):raise Blocked('MISSING_OR_UNEXPECTED_PHASE_COMMANDS')
    previous=start
    for c in r['commands']:
        if not isinstance(c,dict):raise Blocked('INVALID_COMMAND_EVIDENCE')
        cs,ce=successful(c)
        if c.get('forced') is not False or c.get('log_budget_exceeded') is not False:raise Blocked('FORCED_OR_TRUNCATED_COMMAND')
        if not isinstance(c.get('argv'),list) or not c['argv'] or any(not isinstance(v,str) for v in c['argv']):raise Blocked('MISSING_COMMAND_IDENTITY')
        if not isinstance(c.get('log'),str) or not c['log'] or not isinstance(c.get('log_sha256'),str) or not HEX.fullmatch(c['log_sha256']):raise Blocked('MISSING_COMMAND_LOG')
        if cs<previous or ce>end or c['elapsed_seconds']>r['elapsed_seconds']+TIMING_TOLERANCE_SECONDS:raise Blocked('COMMAND_OUTSIDE_PHASE')
        previous=ce
    return True

def verify_supervisor(final):
    if not isinstance(final,dict) or final.get('supervisor_completed') is not True:raise Blocked('SUPERVISOR_INCOMPLETE')
    if type(final.get('exit_code')) is not int or final['exit_code']!=0 or 'signal' not in final or final['signal'] is not None or final.get('timeout') is not False:raise Blocked('MISSING_OR_FAILED_SUPERVISOR_STATUS')
    if final.get('timed_out',False) is not False or final.get('termination_signal') is not None:raise Blocked('CONTRADICTORY_SUPERVISOR_OUTCOME')
    if final.get('systemd_result')!='success':raise Blocked('SUPERVISOR_SYSTEMD_RESULT')
    q=final.get('observed_properties',{})
    if q.get('ExecMainCode')!='1' or q.get('ExecMainStatus')!='0' or q.get('SubState') not in {'exited','dead'}:raise Blocked('SUPERVISOR_TERMINAL_OUTCOME')
    timing(final)
    return True

def verify_complete(receipts,versions,binaries,final,usage):
    if set(receipts)!=set(PHASES):raise Blocked('MISSING_PHASE_RECEIPT')
    for phase,r in receipts.items():verify_receipt(r,phase)
    if set(versions)!=set(VERSIONS) or any(not isinstance(v,str) or not v.strip() for v in versions.values()):raise Blocked('MISSING_TOOL_VERSION')
    if not binaries or any(not all(isinstance(v.get(k),str) and HEX.fullmatch(v[k]) for k in ('sha256','readelf_sha256','objdump_sha256')) for v in binaries.values()):raise Blocked('MISSING_BINARY_DEPENDENCY_INVENTORY')
    verify_supervisor(final);verify_usage(usage)
    return True


def verify_artifacts(root,inventory):
    root=Path(root).resolve()
    for relative,h in inventory.items():
        p=root/relative
        if Path(relative).is_absolute() or '..' in Path(relative).parts or p.is_symlink() or not p.is_file() or not p.resolve().is_relative_to(root) or digest(p)!=h:raise Blocked('ARTIFACT_CORRUPTION_OR_MISSING:'+relative)
    return True
