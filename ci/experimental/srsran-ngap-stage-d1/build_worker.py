"""Only entered by the separately approved hosted isolated supervisor; never native execution."""
import json,os,shutil,subprocess,sys,tarfile,time,stat
from pathlib import Path
from build_policy import Blocked,LIMITS,VERSIONS,PHASES,digest,environment,resource_admission,check_dependency_tree,audit_graph,unity_generated
from open5gs_graph_gate import audit_open5gs_graph
from open5gs_rsp_review import collect_rsp_review
from open5gs_rsp_expansion import review_expanded_compdb
from build_receipts import Recorder,atomic
from toolchain_lock import run_bounded as verify_toolchain,verify_acquisition

def run_output(argv,env):return subprocess.check_output(argv,env=env,text=True,timeout=30)
def main():
    if sys.argv[1:]!=['--isolated-build-only'] or os.getuid()==0:raise Blocked('UNPRIVILEGED_SUPERVISED_ENTRY_ONLY')
    p=Path(__file__).resolve().parent;w=Path('/work');out=w/'evidence';out.mkdir(mode=0o700)
    spec=json.loads((w/'spec.json').read_text());sources=Path(spec['acquired']);home=w/'home';home.mkdir();tmp=w/'tmp';tmp.mkdir();env=environment(os.environ,home,tmp)
    cg=Path('/sys/fs/cgroup')/next(x.split(':',2)[2].lstrip('/') for x in Path('/proc/self/cgroup').read_text().splitlines() if x.startswith('0::'))
    def cgtext(n):return (cg/n).read_text().strip()
    caps_by_name={line.split(':',1)[0]:line.split(':',1)[1].strip() for line in Path('/proc/self/status').read_text().splitlines() if line.startswith(('CapInh:','CapPrm:','CapEff:','CapBnd:','CapAmb:'))}
    link=json.loads(run_output(['/usr/sbin/ip','-j','link','show'],env));routes=json.loads(run_output(['/usr/sbin/ip','-j','route','show','table','all'],env));routes6=json.loads(run_output(['/usr/sbin/ip','-j','-6','route','show','table','all'],env))
    # Mounts outside bounded scratch and in-memory /dev must be read-only.
    writable=[]
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        fields=line.split();target=fields[4]
        if 'rw' in fields[5].split(','):writable.append(target)
    fs=os.statvfs(w);capacity=fs.f_blocks*fs.f_frsize
    status=Path('/proc/self/status').read_text();caps=next(x.split()[1] for x in status.splitlines() if x.startswith('CapEff:'))
    ipc_clear=all(not any(Path(d).iterdir()) for d in ('/run','/tmp','/var','/home','/root'))
    for directory in (sources,Path(spec['repository'])):
        for base,dirs,files in os.walk(directory,followlinks=False):
            if any(stat.S_ISSOCK(os.lstat(Path(base)/name).st_mode) for name in files):ipc_clear=False
    e={'host_ipc_isolated':ipc_clear,'cgroup_v2':(cg/'cgroup.controllers').exists(),'network_isolated':os.stat('/proc/self/ns/net').st_ino!=spec['host_netns_inode'] and bool(link) and all(x['ifname']=='lo' for x in link) and all(x.get('dev')=='lo' for x in routes+routes6),'root_readonly':all(t=='/work' or t=='/dev' or t.startswith('/dev/') for t in writable),'scratch_capacity_verified':capacity<=LIMITS['disk_bytes'] and capacity>=LIMITS['disk_bytes']*.9 and spec['disk_image_bytes']==LIMITS['disk_bytes'],'unprivileged':os.getuid()==spec['uid'],'no_capabilities':len(caps_by_name)==5 and all(int(v,16)==0 for v in caps_by_name.values()) and 'NoNewPrivs:\t1' in status,'namespace_private':os.stat('/proc/self/ns/mnt').st_ino!=spec['host_mount_inode'],'memory.max':int(cgtext('memory.max')),'memory.swap.max':int(cgtext('memory.swap.max')),'cpu.max':cgtext('cpu.max'),'pids.max':int(cgtext('pids.max')),'disk_bytes':spec['disk_image_bytes'],'filesystem_capacity_bytes':capacity,'accounting':[x for x in ('memory.peak','pids.peak','cpu.stat','memory.events','pids.events') if (cg/x).is_file()]+['disk_used_peak'],'capability_readback':caps_by_name,'writable_mounts':writable,'netns_inode':os.stat('/proc/self/ns/net').st_ino}
    atomic(out/'enforcement.json',e);resource_admission(e)
    rec=Recorder(out,env);policy=json.loads((p/'dependency-command-policy.json').read_text());manifest=json.loads((p/'source-manifest.json').read_text());repo=Path(spec['repository'])
    shutil.copy2(sources/'dependency_acquisition.receipt.json',out/'dependency_acquisition.receipt.json')
    for logfile in sources.glob('dependency_acquisition*.log'):shutil.copy2(logfile,out/logfile.name)
    shutil.copy2(sources/'source-archives.json',out/'source-archives.json')
    def source_verify():
        verify_acquisition(sources)
        shutil.copy2(sources/'toolchain-lock.json',out/'toolchain-lock.json')
        verify_toolchain('--verify',out,env,w,w/'platform-packages.txt')
        for name,d in policy['sources'].items():
            archive=sources/(name+'.tar')
            if digest(archive)!=d['archive_sha256']:raise Blocked('SOURCE_ARCHIVE_HASH')
            dest=w/name;dest.mkdir()
            with tarfile.open(archive) as tar:
                for member in tar.getmembers():
                    target=Path(os.path.normpath(str(dest/member.name)))
                    if not target.is_relative_to(dest) or member.isdev() or member.isfifo() or member.islnk():raise Blocked('UNSAFE_SOURCE_ARCHIVE')
                    if member.issym() and not Path(os.path.normpath(str(target.parent/member.linkname))).is_relative_to(dest):raise Blocked('UNSAFE_SYMLINK')
                tar.extractall(dest)
        check_dependency_tree(w,policy)
        for name,h in manifest['preserved_repository_inputs'].items():
            if digest(repo/name)!=h:raise Blocked('PRESERVED_INPUT_DRIFT')
        atomic(out/'verified-source-archives.json',{k:{'revision':v['revision'],'sha256':v['archive_sha256']} for k,v in policy['sources'].items()})
    rec.phase('source_verification',[],w,source_verify,outputs=[out/'verified-source-archives.json',out/'toolchain-lock.json',out/'toolchain-verification.receipt.json',out/'toolchain-verification.log',out/'toolchain-progress.json'])
    s=w/'srsran';ogs=w/'open5gs';sb=w/'srs-build';ob=w/'ogs-build'
    for name in ('freeDiameter','libtins','prometheus-client-c','usrsctp'):shutil.move(str(w/name),ogs/'subprojects'/name)
    stage=repo/'ci/experimental/srsran-ngap/stage-a-complete.patch'
    def stage_check():
        if digest(stage)!=manifest['original_instrumentation_patch_sha256']:raise Blocked('PATCH_HASH')
        for name,h in manifest['stage_a_files'].items():
            if digest(s/name)!=h:raise Blocked('STAGE_A_DRIFT')
    def activation_check():
        stage_check()
        for name,h in manifest['activation_source_files'].items():
            if digest(s/name)!=h['proposed']:raise Blocked('ACTIVATION_DRIFT')
        with (s/'CMakeLists.txt').open('a') as f:f.write('\nadd_subdirectory("'+str(p)+'" l3-d1-driver)\n')
        atomic(out/'applied-patches.json',{'original':digest(stage),'activation':digest(p/'activation.patch')})
    # Both patches are explicit; phase failure leaves the scratch tree unused.
    rec.phase('patch_application',[['/usr/bin/git','apply',str(stage)],['/usr/bin/git','apply',str(p/'activation.patch')]],s,activation_check,outputs=[out/'applied-patches.json'])
    versions={}
    for tool in VERSIONS:versions[tool]=run_output(['/usr/bin/'+tool,'--version'],env)
    atomic(out/'toolchain.json',versions)
    (out/'os-release.txt').write_text(Path('/etc/os-release').read_text());shutil.copy2(w/'platform-packages.txt',out/'packages.txt')
    rec.phase('cmake_configuration',[['/usr/bin/cmake','-G','Ninja','-S',str(s),'-B',str(sb),'-DCMAKE_BUILD_TYPE=RelWithDebInfo','-DCMAKE_EXPORT_COMPILE_COMMANDS=ON','-DENABLE_UHD=OFF','-DENABLE_ZEROMQ=OFF','-DENABLE_MKL=OFF','-DENABLE_ARMPL=OFF','-DENABLE_PLUGINS=OFF','-DENABLE_BACKWARD=OFF','-DBUILD_TESTING=ON','-DCMAKE_GTEST_DISCOVER_TESTS_DISCOVERY_MODE=PRE_TEST','-DFETCHCONTENT_FULLY_DISCONNECTED=ON','-DFETCHCONTENT_UPDATES_DISCONNECTED=ON']],w,outputs=[sb/'compile_commands.json',sb/'CMakeCache.txt'],timeout=180)
    rec.phase('meson_configuration',[['/usr/bin/meson','setup',str(ob),str(ogs),'--buildtype=debugoptimized','--wrap-mode=nodownload']],w,lambda:unity_generated(ob),outputs=[ob/'compile_commands.json',ob/'meson-logs/meson-log.txt'],timeout=180)
    db=json.loads((sb/'compile_commands.json').read_text())
    if any('SRSRAN_NGAP_SNAPSHOT_TEST_CLOCK' in json.dumps(x) or 'stage-c3-' in json.dumps(x) for x in db):raise Blocked('CLOCK_LEAKAGE')
    st=['srsran_cu_cp','srscucp','cu_cp_test','l3_ngap_protocol_runtime'];ot=['src/amf/open5gs-amfd','src/nrf/open5gs-nrfd']
    for build,targets,name in [(sb,st,'srsran'),(ob,ot,'open5gs')]:
        graph=run_output(['/usr/bin/ninja','-C',str(build),'-t','commands',*targets],env)
        (out/(name+'-commands.txt')).write_text(graph)
        if name=='srsran':
            template=(s/'cmake/modules/build_info.cmake.in').read_text().replace('@PROJECT_SOURCE_DIR@',str(s)).replace('@CMAKE_BINARY_DIR@',str(sb))
            if (sb/'build_info.cmake').read_text()!=template:raise Blocked('UNREVIEWED_GENERATED_VERSION_HELPER')
        if name=='open5gs':
            audit_open5gs_graph(graph)
            review=collect_rsp_review(ob)
            atomic(out/'open5gs-rsp-review.json',review)
            expanded=run_output(['/usr/bin/ninja','-C',str(ob),'-t','compdb','-x','c_LINKER_RSP'],env)
            if digest(ob/'build.ninja')!=review['build_ninja_sha256']:raise Blocked('OPEN5GS_RSP_MANIFEST_CHANGED')
            atomic(out/'open5gs-rsp-expanded.json',review_expanded_compdb(expanded,review))
            raise Blocked('OPEN5GS_RSP_INDEPENDENT_REVIEW_REQUIRED')
        else:
            audit_graph(graph)
    rec.phase('srsran_compilation',[['/usr/bin/ninja','-C',str(sb),'-j','2','-v',*st]],w,outputs=[sb/'l3-d1-driver/l3_ngap_protocol_runtime',sb/'apps/cu_cp/srscucp',sb/'tests/unittests/cu_cp/cu_cp_test',sb/'lib/cu_cp/libsrsran_cu_cp.a'],timeout=4200)
    rec.phase('open5gs_compilation',[['/usr/bin/ninja','-C',str(ob),'-j','2','-v',*ot]],w,outputs=[ob/'src/amf/open5gs-amfd',ob/'src/nrf/open5gs-nrfd'],timeout=4200)
    bins=[sb/'l3-d1-driver/l3_ngap_protocol_runtime',sb/'apps/cu_cp/srscucp',sb/'tests/unittests/cu_cp/cu_cp_test',ob/'src/amf/open5gs-amfd',ob/'src/nrf/open5gs-nrfd']
    def linkage():
        for b in bins:
            if not b.is_file() or b.read_bytes()[:4]!=b'\x7fELF':raise Blocked('MISSING_ELF')
        entries=[x for x in db if Path(x['file']).name in ('runtime_driver.cpp','snapshot_observer.cpp','cu_cp_impl.cpp','cu_cp_controller.cpp','amf_connection_manager.cpp')]
        if len(entries)!=5:raise Blocked('MISSING_DEPENDENCY_OBJECT')
        import shlex
        for x in entries:
            args=x.get('arguments') or shlex.split(x['command']);obj=args[args.index('-o')+1]
            data=run_output(['/usr/bin/ninja','-C',str(sb),'-t','deps',obj],env)
            if 'deps not found' in data or str(s/'lib/cu_cp/cu_cp_controller/ngap_application_snapshot.h') not in data or 'stage-c3-' in data:raise Blocked('INVALID_HEADER_DEPENDENCY')
            (out/(Path(x['file']).name+'.deps.txt')).write_text(data)
    rec.phase('linkage_verification',[],w,linkage,outputs=[out/(name+'.deps.txt') for name in ('runtime_driver.cpp','snapshot_observer.cpp','cu_cp_impl.cpp','cu_cp_controller.cpp','amf_connection_manager.cpp')])
    inventory={};inspection_outputs=[out/'binary-inventory.json']
    def inspect():
        libs=[x for x in ob.rglob('*.so*') if x.is_file() and not x.is_symlink()]+list(sb.rglob('*.a'))+list(ob.rglob('*.a'))
        if len(libs)>512:raise Blocked('LIBRARY_BUDGET')
        for index,b in enumerate(bins+libs):
            base=str(index)+'-'+b.name
            re=run_output(['/usr/bin/readelf','-d','-h',str(b)],env);od=run_output(['/usr/bin/objdump','-p',str(b)],env);sy=run_output(['/usr/bin/nm','-C',str(b)],env)
            if 'l3_ngap_snapshot_test_clock_now' in sy:raise Blocked('CLOCK_SYMBOL_LEAKAGE')
            artifacts={}
            for ext,text in [('readelf',re),('objdump',od),('symbols',sy)]:
                artifact=out/(base+'.'+ext);artifact.write_text(text)
                artifacts[ext]={'path':artifact.name,'sha256':digest(artifact)}
                inspection_outputs.append(artifact)
            inventory[str(b.relative_to(w))]={'sha256':digest(b),'readelf_sha256':digest(out/(base+'.readelf')),'objdump_sha256':digest(out/(base+'.objdump')),'symbols_sha256':digest(out/(base+'.symbols')),'inspection_artifacts':artifacts}
        atomic(out/'binary-inventory.json',inventory)
    rec.phase('binary_inspection',[],w,inspect,outputs=inspection_outputs)
    def final():
        stage_check()
        for name,h in manifest['preserved_repository_inputs'].items():
            if digest(repo/name)!=h:raise Blocked('PRESERVED_INPUT_DRIFT')
        for src,dst in [(sb/'compile_commands.json','srs-compile-commands.json'),(sb/'CMakeCache.txt','CMakeCache.txt'),(ob/'compile_commands.json','ogs-compile-commands.json'),(ob/'meson-logs/meson-log.txt','meson-log.txt')]:shutil.copy2(src,out/dst)
        atomic(out/'worker-completion.json',{'status':'BUILD_ONLY_COMPLETE_PENDING_EXTERNAL_VERIFICATION','native_tests_executed':0,'protocol_runtime_executed':False})
    rec.phase('final_evidence_verification',[],w,final,outputs=[out/'worker-completion.json',out/'srs-compile-commands.json',out/'ogs-compile-commands.json'])
if __name__=='__main__':
    try:main()
    except BaseException as error:
        status='BLOCKED' if isinstance(error,Blocked) else getattr(error,'classification','INCOMPLETE')
        destination=Path('/work/evidence')
        if destination.is_dir():atomic(destination/'worker-status.json',{'status':status,'reason':str(error) if isinstance(error,Blocked) else type(error).__name__})
        raise SystemExit(78 if status=='BLOCKED' else 1)
