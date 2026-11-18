"""Tests the no-compilation response-file expansion admission."""
import hashlib, json, tempfile, subprocess, unittest
from pathlib import Path
from unittest.mock import patch
from build_policy import Blocked
from open5gs_rsp_review import TARGETS
from open5gs_rsp_expansion import review_expanded_compdb, EXPECTED_OBJECTS


def fixture():
    objects = {t: [f'{t}.p/file{i}.c.o' for i in range(EXPECTED_OBJECTS[t])] for t in TARGETS}
    rule = ('rule c_LINKER_RSP\n command = cc @$out.rsp\n rspfile = $out.rsp\n'
            ' rspfile_content = $ARGS -o $out $in $LINK_ARGS\n description = Linking $out\n')
    edges = {t: f'build {t}: c_LINKER_RSP ' + ' '.join(objs) + '\n'
             + ' LINK_ARGS = -shared -fPIC -Wl,--as-needed\n' for t, objs in objects.items()}
    report = {'build_ninja_sha256':hashlib.sha256((rule + '\n'+''.join(edges.values())).encode()).hexdigest(), 'targets':{}}
    for t,edge in edges.items():
        report['targets'][t]={'rule_name':'c_LINKER_RSP','rule_text':rule,'build_edge_text':edge,
                              'rule_sha256':hashlib.sha256(rule.encode()).hexdigest(),
                              'build_edge_sha256':hashlib.sha256(edge.encode()).hexdigest()}
    entries=[{'output':t,'command':'cc -o '+t+' '+' '.join(objects[t])+' -shared -fPIC -Wl,--as-needed'} for t in TARGETS]
    return json.dumps(entries),report

class ExpansionTests(unittest.TestCase):
    def test_fixture_passes_without_authorizing_build(self):
        text,report=fixture();result=review_expanded_compdb(text,report)
        self.assertEqual(set(result['targets']),set(TARGETS))
        self.assertFalse(result['native_compilation_authorized'])
        self.assertEqual([x['object_count'] for x in result['targets'].values()],[1062,1350])
    def test_nested_rsp_denied(self):
        text,report=fixture();rows=json.loads(text);rows[0]['command']+=' @evil.rsp'
        with self.assertRaisesRegex(Blocked,'UNREVIEWED_FLAG_OR_NESTED_RSP'):review_expanded_compdb(json.dumps(rows),report)
    def test_plugin_denied(self):
        text,report=fixture();rows=json.loads(text);rows[0]['command']+=' -fplugin=/work/evil.so'
        with self.assertRaisesRegex(Blocked,'UNREVIEWED_FLAG'):review_expanded_compdb(json.dumps(rows),report)
    def test_custom_linker_script_denied(self):
        text,report=fixture();rows=json.loads(text);rows[0]['command']+=' -Wl,-T,/work/evil.ld'
        with self.assertRaises(Blocked):review_expanded_compdb(json.dumps(rows),report)
    def test_missing_target_denied(self):
        text,report=fixture();rows=json.loads(text);rows.pop()
        with self.assertRaisesRegex(Blocked,'INCOMPLETE'):review_expanded_compdb(json.dumps(rows),report)
    def test_duplicate_target_denied(self):
        text,report=fixture();rows=json.loads(text);rows.append(rows[0])
        with self.assertRaisesRegex(Blocked,'DUPLICATE'):review_expanded_compdb(json.dumps(rows),report)
    def test_missing_object_denied(self):
        text,report=fixture();rows=json.loads(text);rows[0]['command']=rows[0]['command'].replace('file4.c.o','missing4.c.o')
        with self.assertRaisesRegex(Blocked,'LINK_INPUTS_MISMATCH'):review_expanded_compdb(json.dumps(rows),report)
    def test_rule_modified_denied(self):
        text,report=fixture();report['targets'][next(iter(TARGETS))]['rule_text']+=' edit'
        with self.assertRaisesRegex(Blocked,'RULE_HASH'):review_expanded_compdb(text,report)
    def test_sha_missing_denied(self):
        text,report=fixture();report.pop('build_ninja_sha256')
        with self.assertRaisesRegex(Blocked,'MANIFEST_HASH'):review_expanded_compdb(text,report)
    def test_output_path_denied(self):
        text,report=fixture();rows=json.loads(text);rows[0]['command']=rows[0]['command'].replace('cc -o ', 'cc -o /tmp/')
        with self.assertRaisesRegex(Blocked,'OUTPUT_DRIFT'):review_expanded_compdb(json.dumps(rows),report)
    def test_bounded_tool_output(self):
        text,report=fixture()
        with patch('open5gs_rsp_expansion.MAX_COMPDB_BYTES',16):
            with self.assertRaises(Blocked):review_expanded_compdb(text,report)
    def test_compdb_x_no_build_smoke(self):
        # ninja -t compdb -x reads build.ninja and expands RSP without executing cc.
        with tempfile.TemporaryDirectory() as tmp:
            t=Path(tmp);(t/'build.ninja').write_text('rule c_LINKER_RSP\n command = cc @$out.rsp\n rspfile = $out.rsp\n rspfile_content = -o $out $in -shared\nbuild foo.so: c_LINKER_RSP foo.o\n')
            result=subprocess.run(['/usr/bin/ninja','-C',tmp,'-t','compdb','-x','c_LINKER_RSP'],text=True,capture_output=True,timeout=5,check=True)
            self.assertEqual(json.loads(result.stdout)[0]['command'],'cc -o foo.so foo.o -shared')
            self.assertFalse((t/'foo.so').exists())

if __name__=='__main__':unittest.main()
