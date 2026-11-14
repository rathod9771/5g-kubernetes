"""Synthetic parser/flag validation only. No native code or sanitizers are executed."""
import unittest
import xml.etree.ElementTree as ET
from verify_instrumentation import check_flags, verify_database, REQUIRED, BINARIES
from verify_results import check_components

class PreparationChecks(unittest.TestCase):
    def test_each_separate_mode(self):
        for mode in ('address','undefined','thread'): check_flags(['-fsanitize='+mode],mode)
    def test_missing_flags(self):
        with self.assertRaises(ValueError): check_flags([], 'address')
    def test_mixed_sanitizers(self):
        with self.assertRaises(ValueError): check_flags(['-fsanitize=address,thread'],'thread')
    def test_wrong_mode(self):
        with self.assertRaises(ValueError): check_flags(['-fsanitize=undefined'],'address')
    def test_disable_all_rejected(self):
        with self.assertRaises(ValueError): check_flags(['-fsanitize=address','-fno-sanitize=all'],'address')
    def test_upstream_ubsan_vptr_exception(self):
        check_flags(['-fsanitize=undefined','-fno-sanitize=vptr'],'undefined')
    def test_database_and_link_flags(self):
        entries=[{'file':'/source/'+p,'command':'c++ -fsanitize=address -c dummy.cpp'} for p in REQUIRED]
        commands='\n'.join('c++ -fsanitize=address main.o -o '+p for p in BINARIES)
        self.assertEqual(verify_database(entries,commands,'address')['mode'],'address')
    def test_missing_translation_unit(self):
        with self.assertRaises(ValueError): verify_database([], '', 'thread')
    def test_missing_link_flags(self):
        entries=[{'file':'/source/'+p,'command':'c++ -fsanitize=address -c dummy.cpp'} for p in REQUIRED]
        commands='\n'.join('c++ main.o -o '+p for p in BINARIES)
        with self.assertRaises(ValueError): verify_database(entries,commands,'address')
    def test_component_success(self):
        xml='<testsuite tests="2"><testcase name="old" status="run"><system-out>RUN alpha\nPASS alpha\n</system-out></testcase><testcase name="new" status="run"><system-out>RUN beta\nPASS beta\n</system-out></testcase></testsuite>'
        self.assertEqual(check_components(xml,{'old':['alpha'],'new':['beta']}),{'old':1,'new':1})
    def test_missing_native_assertions(self):
        xml='<testsuite tests="2"><testcase name="old" status="run"><system-out>RUN alpha\n</system-out></testcase></testsuite>'
        with self.assertRaises(ValueError): check_components(xml,{'old':['alpha'],'new':['beta']})
    def test_skipped_components(self):
        with self.assertRaises(ValueError): check_components('<testsuite tests="2" skipped="1"/>',{})

    def test_overlap_contract_and_post_owner_barrier(self):
        from pathlib import Path
        cpp=Path(__file__).with_name('concurrency_component.cpp').read_text()
        for name in ('retained_ticket_destruction','revision_invalidation'):
            body=cpp.split('void '+name+'() {',1)[1].split('\nvoid ',1)[0]
            self.assertIn('if(value.valid) {',body)
            self.assertIn('} else unknown(value);',body)
            for assertion in ('value.complete','value.process_incarnation==e.incarnation',
                              'value.state_revision==before.state_revision',
                              'value.peers[p].association_generation==before.peers[p].association_generation',
                              'value.peers[p].application_state==before.peers[p].application_state'):
                self.assertIn('CHECK('+assertion+');',body)
            after=body.split('gate_wait(completed);',1)[1]
            self.assertIn('for(unsigned i=0;i<16;++i) unknown(result->inspect(e.incarnation));',after)
            self.assertIn('owner_completed.set_value();',after)
    def test_revision_final_state_is_strict(self):
        from pathlib import Path
        cpp=Path(__file__).with_name('concurrency_component.cpp').read_text()
        body=cpp.split('void revision_invalidation() {',1)[1].split('\nvoid ',1)[0]
        self.assertIn('CHECK(current.peers[0].application_state==state::down);',body)
        self.assertIn('CHECK(current.state_revision>before.state_revision);',body)
        self.assertIn('CHECK(current.peers[0].association_generation>before.peers[0].association_generation);',body)

if __name__ == '__main__': unittest.main()
