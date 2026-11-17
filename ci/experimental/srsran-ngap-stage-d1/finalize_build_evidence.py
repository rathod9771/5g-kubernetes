"""Post-supervisor CI check: missing status/phase/log/artifact evidence cannot become PASS."""
import json,sys
from pathlib import Path
from build_policy import Blocked,digest,PHASES,verify_artifacts,verify_complete,resource_admission
from build_receipts import atomic

def validate_native(root):
    root=Path(root)
    if not (root/'supervisor-final.json').is_file():raise Blocked('MISSING_FINAL_SUPERVISOR_STATUS_RUNNER_MAY_HAVE_BEEN_TERMINATED')
    final=json.loads((root/'supervisor-final.json').read_text())
    if final.get('status')!='PASS_BUILD_ONLY_ZERO_NATIVE_TESTS' or final.get('exit_code')!=0 or final.get('timeout') is not False or final.get('signal') is not None:raise Blocked('SUPERVISOR_NOT_PASS')
    if not final.get('evidence_sha256'):raise Blocked('MISSING_FINAL_ARTIFACT_MANIFEST')
    verify_artifacts(root,final['evidence_sha256'])
    receipts={}
    for phase in PHASES:
        f=root/'worker'/(phase+'.receipt.json')
        if not f.is_file():raise Blocked('MISSING_PHASE:'+phase)
        if 'worker/'+f.name not in final['evidence_sha256']:raise Blocked('PHASE_NOT_BOUND_TO_FINAL_MANIFEST')
        r=json.loads(f.read_text());receipts[phase]=r
        if r.get('classification')!='PASS' or r.get('exit_code')!=0:raise Blocked('PHASE_NOT_PASS')
        log=root/'worker'/Path(r['log']).name
        if not log.is_file() or digest(log)!=r['log_sha256']:raise Blocked('INTERRUPTED_OR_CORRUPT_LOG')
        for c in r.get('commands',[]):
            log=root/'worker'/Path(c['log']).name
            if not log.is_file() or digest(log)!=c['log_sha256']:raise Blocked('INTERRUPTED_OR_CORRUPT_COMMAND_LOG')
    for required in ('resource-usage.json','worker/toolchain.json','worker/binary-inventory.json','worker/enforcement.json'):
        if not (root/required).is_file() or required not in final['evidence_sha256']:raise Blocked('MISSING_REQUIRED_EVIDENCE:'+required)
    resource_admission(json.loads((root/'worker/enforcement.json').read_text()))
    verify_complete(receipts,json.loads((root/'worker/toolchain.json').read_text()),json.loads((root/'worker/binary-inventory.json').read_text()),final,json.loads((root/'resource-usage.json').read_text())['usage'])
    for r in receipts.values():
        for ref in [r['log'],*[c['log'] for c in r['commands']]]:
            relative='worker/'+Path(ref).name
            if relative not in final['evidence_sha256']:raise Blocked('LOG_NOT_BOUND_TO_FINAL_MANIFEST')
    return {'classification':'PASS_BUILD_ONLY','native_tests_executed':0,'runtime_executed':False}
def validate(root):
    # Native success alone is never whole-workflow success.
    from workflow_evidence import validate_aux, timing, checked, INPUT_FILES, P, inspection_manifest, validate_toolchain_diagnostics
    root=Path(root)
    result=validate_native(root)
    inspection_manifest(root)
    validate_toolchain_diagnostics(root)
    windows=validate_aux(root/'auxiliary')
    for name in INPUT_FILES:
        if digest(checked(root,'inputs/'+name))!=digest(P/name):raise Blocked('INPUT_EVIDENCE_HASH:'+name)
    preservation=json.loads(checked(root,'preservation.json').read_text())
    if preservation.get('classification')!='PASS' or preservation.get('errors')!=[]:raise Blocked('PRESERVATION_INCOMPLETE')
    first=json.loads((root/'worker/dependency_acquisition.receipt.json').read_text())
    if windows[-1][1]>timing(first)[0]:raise Blocked('WORKFLOW_PHASE_ORDER')
    # Native phase contracts remain unchanged; establish inter-phase ordering too.
    last=timing(first)[1]
    for phase in PHASES[1:]:
        r=json.loads((root/'worker'/(phase+'.receipt.json')).read_text());a,b=timing(r)
        if a<last:raise Blocked('NATIVE_PHASE_ORDER')
        last=b
    result['upload_verified']=False
    result['scope']='LOCAL_EVIDENCE_ADMISSION_ONLY_UPLOAD_REQUIRED'
    return result

def failure_classification(root,error):
    root=Path(root)
    # Do not manufacture an outcome for an interrupted command or missing receipt.
    for phase in ('wheel_acquisition','python_setup'):
        f=root/'auxiliary'/(phase+'.receipt.json')
        if f.is_file() and not f.is_symlink():
            try:
                r=json.loads(f.read_text())
                if r.get('command',{}).get('timeout') is True or r.get('command',{}).get('signal') is not None:return 'INCOMPLETE'
                if r.get('classification') in ('FAIL','BLOCKED'):return r['classification']
            except (ValueError,TypeError):return 'INCOMPLETE'
    f=root/'supervisor-final.json'
    if f.is_file() and not f.is_symlink():
        try:
            r=json.loads(f.read_text())
            if r.get('status') in ('FAIL','BLOCKED'):return r['status']
        except (ValueError,TypeError):return 'INCOMPLETE'
    text=str(error)
    if any(x in text for x in ('MISSING','INCOMPLETE','INTERRUPTED','NOT_BOUND')):return 'INCOMPLETE'
    return 'BLOCKED' if isinstance(error,Blocked) else 'INCOMPLETE'

if __name__=='__main__':
    root=Path(sys.argv[1]);root.mkdir(exist_ok=True)
    try:result=validate(root)
    except BaseException as e:result={'classification':failure_classification(root,e),'reason':str(e) if isinstance(e,Blocked) else type(e).__name__}
    atomic(root/'ci-evidence-status.json',result)
    raise SystemExit(0 if result['classification']=='PASS_BUILD_ONLY' else 1)
