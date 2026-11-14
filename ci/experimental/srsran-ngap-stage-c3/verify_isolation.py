"""Fail closed on clock-macro leakage, mixed header dependencies or CU-CP linkage."""
import json,re,shlex,sys
from pathlib import Path
MACRO='SRSRAN_NGAP_SNAPSHOT_TEST_CLOCK'
HEADER='ngap_application_snapshot.h'
HOOK='l3_ngap_snapshot_test_clock_now'
BINARY='l3-stage-c3-tests/l3_ngap_snapshot_freshness'


def verify_database(entries,commands,overlay,mode,source):
    # Reuse the already validated sanitizer flag policy, not a new weaker policy.
    prior=Path(__file__).resolve().parents[1]/'srsran-ngap-stage-c'
    sys.path.insert(0,str(prior))
    from verify_instrumentation import check_flags
    matched=[]; support=[]
    for entry in entries:
        tokens=entry.get('arguments') or shlex.split(entry['command'])
        name=entry['file'].replace('\\','/')
        if name.endswith('/srsran-ngap-stage-c3/freshness_component.cpp'):
            matched.append(tokens)
            assert tokens.count('-D'+MACRO+'=1')==1
            assert '-I'+str(overlay) in tokens
            check_flags(tokens,mode)
            # Prefer the private overlay over the source tree for the one snapshot header.
            assert tokens.index('-I'+str(overlay)) < tokens.index('-I'+str(source)), 'Overlay include order is unsafe'
        else:
            assert not any(MACRO in t or str(overlay) in t for t in tokens), 'Test clock leaked into non-test compilation'
        if '/lib/support/' in name or '/lib/srslog/' in name or '/external/fmt/' in name:
            support.append(tokens[tokens.index('-o')+1])
    assert len(matched)==1, 'Exactly one test-clock translation unit required'
    own=matched[0][matched[0].index('-o')+1]
    links=[]
    for line in commands.splitlines():
        tokens=shlex.split(line)
        if '-o' in tokens and tokens[tokens.index('-o')+1]==BINARY and '-c' not in tokens:
            links.append(tokens)
    assert len(links)==1
    check_flags(links[0],mode)
    assert not any('cu_cp' in t or '/ngap/' in t or 'libsrsran_ngap' in t for t in links[0]), 'Forbidden protocol implementation linkage'
    assert 0<len(support)<=512
    return {'freshness_object':own,'support_objects':support,'sole_test_clock_translation_unit':True}


def verify_deps(text,own,overlay_header,support):
    sections={}; current=None
    for line in text.splitlines():
        if line and not line.startswith(' ') and ': #' in line:
            current=line.split(': #',1)[0]; sections[current]=[]
        elif current and line.startswith('    '): sections[current].append(line.strip())
    assert own in sections and str(overlay_header) in sections[own], 'Test overlay was not the compiled header'
    assert [p for p in sections[own] if p.endswith('/'+HEADER)]==[str(overlay_header)]
    for obj in support:
        assert obj in sections and sections[obj], 'Missing support object dependency evidence'
        assert not any(p.endswith('/'+HEADER) for p in sections[obj]), 'Linked support object contains another snapshot definition'
    return {'freshness_overlay_dependency_verified':True,'support_objects_without_snapshot_header':len(support)}


def verify_symbols(fresh,normal):
    assert re.search(r'(?m)^[0-9a-fA-F]+\s+[Tt]\s+.*\b'+HOOK+r'\(',fresh), 'Missing test-clock definition'
    for name,text in normal.items(): assert HOOK not in text, 'Test-clock hook leaked into '+name
    return {'normal_target_hook_absence_verified':sorted(normal)}

if __name__=='__main__':
    mode,build,overlay,commands,source=sys.argv[1:]; build=Path(build);overlay=Path(overlay)
    print(json.dumps(verify_database(json.loads((build/'compile_commands.json').read_text()),Path(commands).read_text(),overlay,mode,Path(source)),indent=2))
