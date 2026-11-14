"""Parser, reconstruction and policy checks only: NO native compilation or execution."""
import importlib.util,json,shlex,sys,unittest
from pathlib import Path
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('c3_isolation',HERE/'verify_isolation.py');isolation=importlib.util.module_from_spec(spec);spec.loader.exec_module(isolation)
spec=importlib.util.spec_from_file_location('c3_results',HERE/'verify_results.py');results=importlib.util.module_from_spec(spec);spec.loader.exec_module(results)
class Preparation(unittest.TestCase):
    def fixture(self):
        own='l3-stage-c3-tests/CMakeFiles/l3_ngap_snapshot_freshness.dir/freshness_component.cpp.o'
        entries=[{'file':'/repo/ci/experimental/srsran-ngap-stage-c3/freshness_component.cpp','command':'c++ -DSRSRAN_NGAP_SNAPSHOT_TEST_CLOCK=1 -I/overlay -I/source -fsanitize=address -o '+own+' -c fixture.cpp'},
                 {'file':'/source/lib/support/worker.cpp','command':'c++ -fsanitize=address -o worker.o -c worker.cpp'}]
        link='c++ -fsanitize=address '+own+' -o '+isolation.BINARY+' lib/support/libsrsran_support.a lib/srslog/libsrslog.a'
        return entries,link
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
        with self.assertRaises(AssertionError):self.check(e,l+' lib/cu_cp/libsrsran_cu_cp.a')
    def test_uninstrumented_freshness_rejected(self):
        e,l=self.fixture();e[0]['command']=e[0]['command'].replace('-fsanitize=address','')
        with self.assertRaises(ValueError):self.check(e,l)
    def test_uninstrumented_link_rejected(self):
        e,l=self.fixture()
        with self.assertRaises(ValueError):self.check(e,l.replace('-fsanitize=address',''))
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
if __name__=='__main__':unittest.main()
