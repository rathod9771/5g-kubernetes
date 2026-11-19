"""Counterfactual only: replay actual D2B finalizer against signed FAILED-run ZIP.
Never compile, execute network functions, alter original artifact or claim a real PASS.
"""
import hashlib,json,os,shutil,stat,tempfile,zipfile
from pathlib import Path
from build_policy import Blocked,PHASES
from build_supervisor import select_native_phase_receipts,verify_retained_phase_outputs,ACQUISITION_DIAGNOSTICS
from finalize_build_evidence import validate_native,validate
import workflow_evidence

EXPECTED_ARCHIVE='982f06e48d3b5d9d5b95bf87cbb86f4a35d15ef435d2f53d863ac012deb1afa8'
EXPECTED_REASON='MISSING_TOOLCHAIN_HELPER_RECEIPT:acquisition-toolchain-database,acquisition-toolchain-operation,acquisition-toolchain-paths,acquisition-toolchain-versions'

def digest(data):return hashlib.sha256(data).hexdigest()
def obj(p):return json.loads(Path(p).read_bytes())
def require(ok,why):
    if not ok:raise RuntimeError('D2B_OFFLINE_REPLAY:'+why)

def unpack_verified(source,root):
    require(digest(source.read_bytes())==EXPECTED_ARCHIVE,'UNEXPECTED_ZIP')
    with zipfile.ZipFile(source) as z:
        entries=z.infolist()
        require(len(entries)==642 and len({i.filename for i in entries})==642,'ZIP_ENTRIES')
        require(sum(i.file_size for i in entries)<256*1024**2,'ZIP_SIZE_BUDGET')
        for i in entries:
            p=Path(i.filename)
            require(not p.is_absolute() and '..' not in p.parts and len(i.filename)<320,'ZIP_PATH')
            require(i.file_size<64*1024**2 and not stat.S_ISLNK(i.external_attr>>16),'ZIP_MEMBER')
            to=root/p
            if i.is_dir():
                to.mkdir(parents=True,exist_ok=True,mode=0o700)
            else:
                to.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                with z.open(i) as inf,to.open('xb') as outf:shutil.copyfileobj(inf,outf)
                to.chmod(0o600)
    manifest=obj(root/'upload-manifest.json')
    require(manifest['classification']=='PASS' and manifest['errors']==[],'SOURCE_MANIFEST_STATUS')
    files={p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}
    require(files==set(manifest['sha256'])|{'upload-manifest.json'},'UPLOAD_SET')
    require(len(manifest['sha256'])==641,'UPLOAD_COUNT')
    for name,h in manifest['sha256'].items():
        require(digest((root/name).read_bytes())==h,'UPLOAD_HASH:'+name)
    final=obj(root/'supervisor-final.json')
    require(len(final['evidence_sha256'])==608,'SUPERVISOR_COUNT')
    for name,h in final['evidence_sha256'].items():
        require(digest((root/name).read_bytes())==h,'SUPERVISOR_HASH:'+name)
    require(final['status']=='BLOCKED' and final['reason']==EXPECTED_REASON,'ORIGINAL_FAILED')
    return final

def replay_live_boundary(root):
    with tempfile.TemporaryDirectory(prefix='d2b-pre-collection-') as path:
        t=Path(path);worker=t/'worker';worker.mkdir();sources=t/'sources';sources.mkdir()
        full=root/'worker'
        for phase in PHASES:
            shutil.copyfile(full/(phase+'.receipt.json'),worker/(phase+'.receipt.json'))
        shutil.copyfile(full/'toolchain-verification.receipt.json',worker/'toolchain-verification.receipt.json')
        receipt=obj(worker/'dependency_acquisition.receipt.json')
        for name in receipt['outputs']:
            require(not name.startswith('/'),'ACQUISITION_RELATIVE_ONLY')
            if name in ACQUISITION_DIAGNOSTICS:
                shutil.copyfile(full/('acquisition-'+name),sources/name)
            else:
                shutil.copyfile(full/name,worker/name)
        require(len(list(worker.glob('*.receipt.json')))==11,'LIVE_11_RECEIPTS')
        require(set(select_native_phase_receipts(worker))==set(PHASES),'LIVE_PHASE_SELECTION')
        count=verify_retained_phase_outputs({'dependency_acquisition':receipt},t/'mount',worker,sources)
        require(count==30,'LIVE_30_SOURCE_OUTPUTS')
        target=sources/'toolchain-progress.json';original=target.read_bytes()
        target.write_bytes(original+b'TAMPER')
        try:verify_retained_phase_outputs({'dependency_acquisition':receipt},t/'mount',worker,sources)
        except Blocked as error:require('ARTIFACT_CORRUPTION' in str(error),'CORRUPTION_CODE')
        else:raise RuntimeError('D2B_OFFLINE_REPLAY:TAMPER_ACCEPTED')
        return count

def replay_finalizer_counterfactual(root,original):
    try:validate_native(root)
    except Blocked as error:require(str(error)=='SUPERVISOR_NOT_PASS','ORIGINAL_REJECT_REASON')
    else:raise RuntimeError('D2B_OFFLINE_REPLAY:ORIGINAL_FAILED_RUN_ACCEPTED')
    final=root/'supervisor-final.json';original_status=final.read_bytes()
    ledger=workflow_evidence.P/'SHA256SUMS';original_ledger=ledger.read_bytes()
    hypothetical=dict(original)
    hypothetical['status']='PASS_BUILD_ONLY_ZERO_NATIVE_TESTS'
    hypothetical.pop('reason',None)
    try:
        final.write_text(json.dumps(hypothetical,indent=2)+'\n')
        ledger.write_bytes((root/'inputs/SHA256SUMS').read_bytes())
        native=validate_native(root)
        require(native['classification']=='PASS_BUILD_ONLY','NATIVE_VALIDATION')
        complete=validate(root)
        require(complete['classification']=='PASS_BUILD_ONLY' and complete['upload_verified'] is False,
                'FULL_FINALIZER_VALIDATION')
        signed_original=obj(root/'upload-manifest.json')['sha256']['supervisor-final.json']
        require(digest(final.read_bytes())!=signed_original,'HYPOTHETICAL_MUST_BREAK_ORIGINAL_SIGNATURE')
        return 'COUNTERFACTUAL_ACCEPTANCE_ONLY'
    finally:
        final.write_bytes(original_status)
        ledger.write_bytes(original_ledger)
        require(digest(final.read_bytes())==digest(original_status),'RESTORE_ORIGINAL')

def main():
    require(os.environ.get('GITHUB_ACTIONS')=='true','HOSTED_OFFLINE_REPLAY_ONLY')
    with tempfile.TemporaryDirectory(prefix='d2b-retained-offline-') as d:
        root=Path(d)/'evidence';root.mkdir()
        original=unpack_verified(Path(os.environ['D2B_RETAINED_ZIP']),root)
        count=replay_live_boundary(root)
        outcome=replay_finalizer_counterfactual(root,original)
        require(obj(root/'supervisor-final.json')['status']=='BLOCKED','ORIGINAL_STATUS_RESTORED')
        report={'classification':outcome,'original_run':44470794446,
                'original_run_status':'FAILED','native_qualified':False,
                'production_qualified':False,'native_compilation_invoked':False,
                'original_evidence_modified':False,'retained_zip_sha256':EXPECTED_ARCHIVE,
                'upload_hashes':641,'supervisor_bindings':608,
                'live_receipts':11,'live_acquisition_outputs':count}
        print(json.dumps(report,indent=2,sort_keys=True))
        Path(os.environ['D2B_REPLAY_REPORT']).write_text(json.dumps(report,indent=2)+'\n')

if __name__=='__main__':main()
