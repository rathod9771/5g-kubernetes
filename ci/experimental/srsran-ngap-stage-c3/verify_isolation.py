"""Fail closed on clock leakage; dependency scope is the target build graph, not archive extraction."""
import json,posixpath,re,shlex,sys
from pathlib import Path
MACRO='SRSRAN_NGAP_SNAPSHOT_TEST_CLOCK'
HEADER='ngap_application_snapshot.h'
HOOK='l3_ngap_snapshot_test_clock_now'
BINARY='l3-stage-c3-tests/l3_ngap_snapshot_freshness'


def absolute(value,directory):
    assert isinstance(value,str) and value and '\\' not in value, 'Invalid Linux path evidence'
    return posixpath.normpath(value if value.startswith('/') else posixpath.join(str(directory),value))


def output_path(value,directory,build):
    path=absolute(value,directory);build=absolute(str(build),'/')
    assert path.startswith(build.rstrip('/')+'/'), 'Object/output escapes the build directory'
    return posixpath.relpath(path,build)


def option(tokens,flag):
    assert tokens.count(flag)==1, 'Missing or ambiguous '+flag
    i=tokens.index(flag);assert i+1<len(tokens) and not tokens[i+1].startswith('-'), 'Missing '+flag+' value'
    return tokens[i+1]


def includes(tokens,directory):
    paths=[];i=0
    while i<len(tokens):
        t=tokens[i]
        if t in ('-I','-isystem','-iquote','-include','-imacros'):
            assert i+1<len(tokens), 'Missing include argument';i+=1;paths.append(absolute(tokens[i],directory))
        else:
            for prefix in ('-isystem','-iquote','-include','-imacros','-I'):
                if t.startswith(prefix) and len(t)>len(prefix):paths.append(absolute(t[len(prefix):],directory));break
        i+=1
    return paths


def private_scope(tokens,directory,overlay,source,authorized):
    assert not any(t.startswith('@') for t in tokens), 'Unexpanded response-file evidence is unsupported'
    paths=includes(tokens,directory)
    if authorized:
        assert tokens.count('-D'+MACRO+'=1')==1
        assert paths.count(overlay)==1 and source in paths
        assert paths.index(overlay)<paths.index(source), 'Overlay include order is unsafe'
    else:
        assert not any(MACRO in t or overlay in t for t in tokens), 'Test clock leaked into non-test compilation'
        assert not any(p==overlay or p.startswith(overlay+'/') for p in paths), 'Private overlay alias leaked'


def verify_database(entries,commands,overlay,mode,source,build=None):
    prior=Path(__file__).resolve().parents[1]/'srsran-ngap-stage-c'
    sys.path.insert(0,str(prior))
    from verify_instrumentation import check_flags
    assert isinstance(entries,list) and 0<len(entries)<=20000
    assert isinstance(commands,str) and 0<len(commands.encode())<=16*1024**2
    fresh=[e for e in entries if e['file'].replace('\\','/').endswith('/srsran-ngap-stage-c3/freshness_component.cpp')]
    assert len(fresh)==1, 'Exactly one test-clock translation unit required'
    build=absolute(str(build) if build is not None else fresh[0]['directory'],'/')
    overlay=absolute(str(overlay),'/');source=absolute(str(source),'/')
    database={};candidates=set();own=None
    # Deliberately scan ALL entries, including unbuilt targets, for prohibited clock evidence.
    for entry in entries:
        directory=entry['directory'];assert directory.startswith('/'), 'Compile directory must be explicit and absolute'
        tokens=entry.get('arguments') or shlex.split(entry['command'])
        assert tokens and all(isinstance(t,str) for t in tokens)
        name=absolute(entry['file'],directory);authorized=entry is fresh[0]
        private_scope(tokens,directory,overlay,source,authorized)
        obj=output_path(option(tokens,'-o'),directory,build)
        assert obj.endswith('.o') and obj not in database, 'Duplicate or non-object compile output'
        assert absolute(option(tokens,'-c'),directory)==name, 'Compile database source mismatch'
        database[obj]={'file':name,'tokens':tokens,'directory':directory}
        if authorized:own=obj;check_flags(tokens,mode)
        if '/lib/support/' in name or '/lib/srslog/' in name or '/external/fmt/' in name:candidates.add(obj)
    graph={};links=[];references=set();archives=set()
    lines=commands.splitlines();assert 0<len(lines)<=8192
    for line in lines:
        assert line.strip(), 'Empty build-command evidence'
        tokens=shlex.split(line);assert tokens and 'cd' not in tokens, 'Unsupported command working-directory change'
        assert not any(t.startswith('@') for t in tokens), 'Unexpanded build response file'
        if '-c' in tokens:
            obj=output_path(option(tokens,'-o'),build,build)
            assert obj.endswith('.o') and obj not in graph, 'Duplicate or malformed build object'
            assert obj in database, 'Build object absent from compile database'
            assert absolute(option(tokens,'-c'),build)==database[obj]['file'], 'Build/database source mismatch'
            private_scope(tokens,build,overlay,source,obj==own)
            check_flags(tokens,mode)
            graph[obj]=tokens
        else:
            references.update(output_path(t,build,build) for t in tokens if t.endswith('.o'))
            if '-o' in tokens:
                assert output_path(option(tokens,'-o'),build,build)==BINARY, 'Unexpected executable link output'
                links.append(tokens)
            else:
                tools=[i for i,t in enumerate(tokens) if posixpath.basename(t) in ('ar','gcc-ar','llvm-ar')]
                assert len(tools)==1, 'Unsupported or malformed build command'
                i=tools[0];assert i+2<len(tokens) and tokens[i+2].endswith('.a'), 'Missing archive output evidence'
                archive=output_path(tokens[i+2],build,build)
                assert archive not in archives, 'Duplicate archive command';archives.add(archive)
    assert own in graph and own in references, 'Missing freshness compile/link object evidence'
    assert references==set(graph), 'Incomplete transitive object command evidence'
    assert len(links)==1, 'Missing or ambiguous freshness link evidence'
    check_flags(links[0],mode)
    assert not any('cu_cp' in t or '/ngap/' in t or 'libsrsran_ngap' in t for t in links[0]), 'Forbidden protocol implementation linkage'
    assert archives=={output_path(t,build,build) for t in links[0] if t.endswith('.a')}, 'Incomplete archive build-graph evidence'
    selected=sorted(candidates & set(graph))
    assert 0<len(selected)<=512, 'Empty or unbounded support build-graph selection'
    # Unknown object classes must not bypass header dependency verification.
    assert set(graph)==set(selected)|{own}, 'Unreviewed object class in freshness build graph'
    for obj in selected:check_flags(database[obj]['tokens'],mode)
    return {'freshness_object':own,'support_objects':selected,'sole_test_clock_translation_unit':True,
            'build_directory':build,'configured_support_objects':len(candidates),
            'excluded_configured_support_objects':sorted(candidates-set(selected)),
            'object_evidence_scope':'transitive_build_graph_not_archive_member_extraction'}


def verify_deps(text,own,overlay_header,support,build=None):
    build=absolute(str(build) if build is not None else str(Path.cwd()),'/')
    own=output_path(own,build,build);support=[output_path(o,build,build) for o in support]
    assert 0<len(support)<=512 and len(set(support))==len(support), 'Invalid support dependency selection'
    sections={};current=None
    for line in text.splitlines():
        if line and not line.startswith(' '):
            current=None
            if ': #' in line:
                current=output_path(line.split(': #',1)[0],build,build)
                assert current not in sections, 'Duplicate object dependency evidence';sections[current]=[]
        elif current and line.startswith('    '):sections[current].append(absolute(line.strip(),build))
    overlay_header=absolute(str(overlay_header),build)
    assert own in sections and overlay_header in sections[own], 'Test overlay was not the compiled header'
    assert [p for p in sections[own] if p.endswith('/'+HEADER)]==[overlay_header]
    for obj in support:
        assert obj in sections and sections[obj], 'Missing support object dependency evidence: '+obj
        assert not any(p.endswith('/'+HEADER) for p in sections[obj]), 'Build-graph support object contains another snapshot definition'
    return {'freshness_overlay_dependency_verified':True,'support_objects_without_snapshot_header':len(support),
            'object_evidence_scope':'transitive_build_graph_not_archive_member_extraction'}


def verify_symbols(fresh,normal):
    assert re.search(r'(?m)^[0-9a-fA-F]+\s+[Tt]\s+.*\b'+HOOK+r'\(',fresh), 'Missing test-clock definition'
    for name,text in normal.items():assert HOOK not in text, 'Test-clock hook leaked into '+name
    return {'normal_target_hook_absence_verified':sorted(normal)}

if __name__=='__main__':
    mode,build,overlay,commands,source=sys.argv[1:];build=Path(build);overlay=Path(overlay)
    print(json.dumps(verify_database(json.loads((build/'compile_commands.json').read_text()),Path(commands).read_text(),overlay,mode,Path(source),build),indent=2))
