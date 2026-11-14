"""Validate executed native cases, never treating a model/parser check as a native pass."""
import json,re,sys,xml.etree.ElementTree as ET
from pathlib import Path

def verify(xml,cases):
    root=ET.fromstring(xml)
    assert int(root.get('tests','0'))==1
    assert all(int(root.get(k,'0'))==0 for k in ('failures','errors','skipped','disabled'))
    found=root.findall('testcase'); assert len(found)==1
    tc=found[0];assert tc.get('name')=='l3_ngap_snapshot_freshness' and tc.get('status')=='run'
    assert all(tc.find(k) is None for k in ('failure','error','skipped'))
    output=tc.findtext('system-out') or ''
    assert re.findall(r'^RUN ([a-z_]+)$',output,re.M)==cases
    assert re.findall(r'^PASS ([a-z_]+)$',output,re.M)==cases
    return {'passed':len(cases),'failed':0,'skipped':0,'native_cases':cases}

if __name__=='__main__':
    folder=Path(sys.argv[1]);cases=json.loads((Path(__file__).parent/'case-inventory.json').read_text())['native_cases']
    result=verify((folder/'freshness.xml').read_text(),cases)
    for p in folder.iterdir():
        if p.is_file() and (p.name=='native.log' or p.name.startswith(('asan.','ubsan.','tsan.'))):
            assert not re.search(r'ERROR: (?:AddressSanitizer|LeakSanitizer)|WARNING: ThreadSanitizer|runtime error:|FATAL: ThreadSanitizer',p.read_text(errors='replace'))
    (folder/'case-counts.json').write_text(json.dumps(result,indent=2)+'\n');print(result)
