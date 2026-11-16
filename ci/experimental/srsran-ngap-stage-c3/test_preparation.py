"""Parser, reconstruction and policy checks only: NO native compilation or execution."""
import importlib.util,json,shlex,sys,unittest
from pathlib import Path
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('c3_isolation',HERE/'verify_isolation.py');isolation=importlib.util.module_from_spec(spec);spec.loader.exec_module(isolation)
spec=importlib.util.spec_from_file_location('c3_results',HERE/'verify_results.py');results=importlib.util.module_from_spec(spec);spec.loader.exec_module(results)
class Preparation(unittest.TestCase):
    def fixture(self):
        own='l3-stage-c3-tests/CMakeFiles/l3_ngap_snapshot_freshness.dir/freshness_component.cpp.o'
        entries=[{'directory':'/build','file':'/repo/ci/experimental/srsran-ngap-stage-c3/freshness_component.cpp','command':'c++ -DSRSRAN_NGAP_SNAPSHOT_TEST_CLOCK=1 -I/overlay -I/source -fsanitize=address -o '+own+' -c /repo/ci/experimental/srsran-ngap-stage-c3/freshness_component.cpp'},
                 {'directory':'/build','file':'/source/lib/support/worker.cpp','command':'c++ -fsanitize=address -o worker.o -c /source/lib/support/worker.cpp'}]
        link='c++ -fsanitize=address '+own+' -o '+isolation.BINARY+' lib/support/libsrsran_support.a'
        return entries,'\n'.join([e['command'] for e in entries]+['ar qc lib/support/libsrsran_support.a worker.o',link])
    def check(self,entries,link):return isolation.verify_database(entries,link,Path('/overlay'),'address',Path('/source'))
    def test_private_macro_acceptance(self):
        e,l=self.fixture();self.assertEqual(self.check(e,l)['support_objects'],['worker.o'])
    def test_production_macro_leakage(self):
        e,l=self.fixture();e[1]['command']+=' -DSRSRAN_NGAP_SNAPSHOT_TEST_CLOCK=1'
        with self.assertRaises(AssertionError):self.check(e,l)
    def test_production_overlay_leakage(self):
        e,l=self.fixture();e[1]['command']+=' -I/overlay'
        with self.assertRaises(AssertionError):self.check(e,l)
    def test_missing_macro(self):
        e,l=self.fixture();e[0]['command']=e[0]['command'].replace('-DSRSRAN_NGAP_SNAPSHOT_TEST_CLOCK=1','')
        with self.assertRaises(AssertionError):self.check(e,l)
    def test_wrong_include_order(self):
        e,l=self.fixture();e[0]['command']=e[0]['command'].replace('-I/overlay -I/source','-I/source -I/overlay')
        with self.assertRaises(AssertionError):self.check(e,l)
    def test_multiple_clock_translation_units(self):
        e,l=self.fixture();e.append(e[0])
        with self.assertRaises(AssertionError):self.check(e,l)
    def test_cu_cp_link_rejected(self):
        e,l=self.fixture()
        with self.assertRaisesRegex(AssertionError,'Forbidden protocol implementation linkage'):self.check(e,l+' lib/cu_cp/libsrsran_cu_cp.a')
    def test_uninstrumented_freshness_rejected(self):
        e,l=self.fixture();e[0]['command']=e[0]['command'].replace('-fsanitize=address','')
        with self.assertRaises(ValueError):self.check(e,l)
    def test_uninstrumented_link_rejected(self):
        e,l=self.fixture()
        with self.assertRaises(ValueError):self.check(e,'\n'.join(line.replace('-fsanitize=address','') if ' -c ' not in line else line for line in l.splitlines()))
    def test_dependencies_are_isolated(self):
        text='own.o: #deps 1, deps mtime 1\n    /overlay/lib/cu_cp/cu_cp_controller/ngap_application_snapshot.h\n\nworker.o: #deps 1, deps mtime 1\n    /source/include/support.h\n'
        isolation.verify_deps(text,'own.o',Path('/overlay/lib/cu_cp/cu_cp_controller/ngap_application_snapshot.h'),['worker.o'])
        with self.assertRaises(AssertionError):isolation.verify_deps(text.replace('/source/include/support.h','/source/lib/cu_cp/cu_cp_controller/ngap_application_snapshot.h'),'own.o',Path('/overlay/lib/cu_cp/cu_cp_controller/ngap_application_snapshot.h'),['worker.o'])
    def test_missing_dependency_evidence_rejected(self):
        with self.assertRaises(AssertionError):isolation.verify_deps('','own.o',Path('/overlay/header.h'),['worker.o'])
    def test_symbols_exclude_clock_from_normal_targets(self):
        isolation.verify_symbols('00000001 T l3_ngap_snapshot_test_clock_now()',{'cu_cp':'T normal()'})
        with self.assertRaises(AssertionError):isolation.verify_symbols('00000001 T l3_ngap_snapshot_test_clock_now()',{'cu_cp':'U l3_ngap_snapshot_test_clock_now()'})
    def xml(self,output):return '<testsuite tests="1"><testcase name="l3_ngap_snapshot_freshness" status="run"><system-out>'+output+'</system-out></testcase></testsuite>'
    def test_native_output_parser(self):self.assertEqual(results.verify(self.xml('RUN alpha\nPASS alpha\n'),['alpha'])['passed'],1)
    def test_missing_case_rejected(self):
        with self.assertRaises(AssertionError):results.verify(self.xml('RUN alpha\n'),['alpha'])
    def test_duplicate_case_rejected(self):
        with self.assertRaises(AssertionError):results.verify(self.xml('RUN alpha\nPASS alpha\nRUN alpha\nPASS alpha\n'),['alpha'])
    def test_skip_rejected(self):
        with self.assertRaises(AssertionError):results.verify('<testsuite tests="1" skipped="1"/>',['alpha'])
    def test_boundary_inventory_matches_native_calls(self):
        cpp=(HERE/'freshness_component.cpp').read_text();inv=json.loads((HERE/'case-inventory.json').read_text())
        import re
        found=re.findall(r'\{"([a-z_]+)",([a-z_]+)\}',cpp)
        self.assertEqual([x for x,y in found],inv['native_cases']);self.assertTrue(all(x==y for x,y in found));self.assertEqual(len(found),15)
        for row in inv['boundaries']:
            call=('publication_boundary' if row['kind']=='publication' else 'inspection_boundary')
            self.assertIn(call+'('+row['literal']+','+str(row['valid']).lower()+');',cpp)
        self.assertNotIn('sleep',cpp)
    def test_default_branch_chrono_is_unchanged(self):
        patch=(HERE/'source-clock-seam.PROPOSED.patch').read_text()
        self.assertNotRegex(patch,r'(?m)^-[^-]')
        self.assertIn('+#else',patch);self.assertIn('+#endif',patch)
    def test_unbuilt_versioning_excluded(self):
        e,l=self.fixture()
        for name in ('build_info','version'):
            e.append({'directory':'/build','file':'/source/lib/support/versioning/'+name+'.cpp',
                      'command':'c++ -fsanitize=address -o lib/support/versioning/'+name+'.cpp.o -c /source/lib/support/versioning/'+name+'.cpp'})
        meta=self.check(e,l)
        self.assertEqual(meta['support_objects'],['worker.o'])
        self.assertEqual(meta['configured_support_objects'],3)
        self.assertEqual(len(meta['excluded_configured_support_objects']),2)
        self.assertEqual(meta['object_evidence_scope'],'transitive_build_graph_not_archive_member_extraction')
    def test_unbuilt_versioning_macro_still_rejected(self):
        e,l=self.fixture()
        e.append({'directory':'/build','file':'/source/lib/support/versioning/version.cpp',
                  'command':'c++ -DSRSRAN_NGAP_SNAPSHOT_TEST_CLOCK=1 -o unused.o -c /source/lib/support/versioning/version.cpp'})
        with self.assertRaises(AssertionError):self.check(e,l)
    def test_required_built_support_selected(self):
        e,l=self.fixture();self.assertEqual(self.check(e,l)['support_objects'],['worker.o'])
    def test_object_paths_normalized(self):
        e,l=self.fixture();e[1]['command']=e[1]['command'].replace('-o worker.o','-o /build/obj/../worker.o')
        l=l.replace('-o worker.o','-o ./worker.o')
        self.assertEqual(self.check(e,l)['support_objects'],['worker.o'])
    def test_deps_absolute_relative_aliases(self):
        text='/build/./own.o: #deps 1, deps mtime 1\n    /overlay/lib/cu_cp/cu_cp_controller/ngap_application_snapshot.h\n\n./worker.o: #deps 1, deps mtime 1\n    /source/support.h\n'
        isolation.verify_deps(text,'./own.o',Path('/overlay/lib/cu_cp/cu_cp_controller/ngap_application_snapshot.h'),['/build/x/../worker.o'],Path('/build'))
    def test_selected_dependency_missing_fails(self):
        text='own.o: #deps 1, deps mtime 1\n    /overlay/lib/cu_cp/cu_cp_controller/ngap_application_snapshot.h\n\nworker.o: deps not found\n'
        with self.assertRaises(AssertionError):isolation.verify_deps(text,'own.o',Path('/overlay/lib/cu_cp/cu_cp_controller/ngap_application_snapshot.h'),['worker.o'])
    def test_empty_selection_fails(self):
        e,l=self.fixture();e=e[:1]
        l='\n'.join([e[0]['command'],l.splitlines()[-1].replace(' lib/support/libsrsran_support.a','')])
        with self.assertRaisesRegex(AssertionError,'Empty or unbounded support'):self.check(e,l)
    def test_duplicate_build_object_fails(self):
        e,l=self.fixture()
        with self.assertRaises(AssertionError):self.check(e,l+'\n'+e[1]['command'])
    def test_missing_link_evidence_fails(self):
        e,l=self.fixture()
        with self.assertRaises(AssertionError):self.check(e,'\n'.join(l.splitlines()[:-1]))
    def test_missing_transitive_compile_command_fails(self):
        e,l=self.fixture()
        with self.assertRaises(AssertionError):self.check(e,'\n'.join(line for line in l.splitlines() if ' -c /source/lib/support/worker.cpp' not in line))
    def test_missing_compile_database_entry_fails(self):
        e,l=self.fixture()
        with self.assertRaises(AssertionError):self.check(e[:1],l)
    def test_missing_freshness_command_fails(self):
        e,l=self.fixture()
        with self.assertRaises(AssertionError):self.check(e,'\n'.join(l.splitlines()[1:]))
    def test_malformed_shell_command_fails(self):
        e,l=self.fixture()
        with self.assertRaises(ValueError):self.check(e,l+'\nc++ "unterminated')
    def test_malformed_compile_output_fails(self):
        e,l=self.fixture()
        with self.assertRaises(AssertionError):self.check(e,l+'\nc++ -c /source/unknown.cpp')
    def test_response_file_evidence_fails(self):
        e,l=self.fixture()
        with self.assertRaises(AssertionError):self.check(e,l+'\nc++ @hidden-flags.rsp')
    def test_empty_commands_fail(self):
        e,l=self.fixture()
        with self.assertRaises(AssertionError):self.check(e,'')
    def test_aliased_overlay_leakage_fails(self):
        e,l=self.fixture();e[1]['command']+=' -I/tmp/../overlay'
        with self.assertRaises(AssertionError):self.check(e,l)
    def test_wrong_freshness_dependency_header_fails(self):
        text='own.o: #deps 1, deps mtime 1\n    /source/lib/cu_cp/cu_cp_controller/ngap_application_snapshot.h\n\nworker.o: #deps 1, deps mtime 1\n    /source/support.h\n'
        with self.assertRaises(AssertionError):isolation.verify_deps(text,'own.o',Path('/overlay/lib/cu_cp/cu_cp_controller/ngap_application_snapshot.h'),['worker.o'])
    def test_build_command_macro_leakage_fails(self):
        e,l=self.fixture();l=l.replace('-o worker.o','-DSRSRAN_NGAP_SNAPSHOT_TEST_CLOCK=1 -o worker.o')
        with self.assertRaises(AssertionError):self.check(e,l)
    def test_uninstrumented_built_support_fails(self):
        e,l=self.fixture();l='\n'.join(line.replace('-fsanitize=address','') if ' -c /source/lib/support/worker.cpp' in line else line for line in l.splitlines())
        with self.assertRaises(ValueError):self.check(e,l)

    def test_missing_archive_command_fails(self):
        e,l=self.fixture()
        with self.assertRaises(AssertionError):self.check(e,'\n'.join(line for line in l.splitlines() if not line.startswith('ar ')))
    def test_unsupported_command_fails(self):
        e,l=self.fixture()
        with self.assertRaises(AssertionError):self.check(e,l+'\nnonsense incomplete-evidence')
    def test_missing_archive_graph_fails(self):
        e,l=self.fixture()
        with self.assertRaises(AssertionError):self.check(e,l+' lib/support/unreviewed.a')

    def test_support_selection_is_bounded(self):
        e,l=self.fixture();entries=[e[0]];objects=[]
        for i in range(513):
            name='worker'+str(i)
            entry=dict(e[1]);entry['file']=entry['file'].replace('worker',name)
            entry['command']=entry['command'].replace('worker',name)
            entries.append(entry);objects.append(name+'.o')
        commands='\n'.join([entry['command'] for entry in entries]+['ar qc lib/support/libsrsran_support.a '+' '.join(objects),l.splitlines()[-1]])
        with self.assertRaisesRegex(AssertionError,'Empty or unbounded support'):self.check(entries,commands)

if __name__=='__main__':unittest.main()
