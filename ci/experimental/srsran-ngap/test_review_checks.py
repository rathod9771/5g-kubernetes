"""Offline parser tests only; these do NOT execute any C++ assertions."""
import unittest
from verify_cu_cp_tests import verify_listing, verify_results

LISTING = "Running main() from gtest_main.cc\ncu_cp_connectivity_test.\n  example_a\ncu_cp_setup_test.\n  example_b\n"
XML = '''<testsuites tests="2" failures="0" errors="0"><testsuite>
<testcase classname="cu_cp_connectivity_test" status="run" result="completed"/>
<testcase classname="cu_cp_setup_test" status="run" result="completed"/>
</testsuite></testsuites>'''

class NativeEvidenceChecks(unittest.TestCase):
    def test_listing_both_suites(self):
        self.assertEqual(sum(verify_listing(LISTING).values()), 2)
    def test_listing_empty(self):
        with self.assertRaises(ValueError): verify_listing("")
    def test_listing_one_suite(self):
        with self.assertRaises(ValueError): verify_listing("cu_cp_connectivity_test.\n  example\n")
    def test_listing_unexpected_suite(self):
        with self.assertRaises(ValueError): verify_listing("unrelated.\n  example\n")
    def test_results_both_suites(self):
        self.assertEqual(sum(verify_results(XML).values()), 2)
    def test_results_zero(self):
        with self.assertRaises(ValueError): verify_results('<testsuites tests="0"/>')
    def test_results_skipped(self):
        with self.assertRaises(ValueError): verify_results(XML.replace('result="completed"', 'result="skipped"', 1))
    def test_results_skip_element(self):
        with self.assertRaises(ValueError): verify_results(XML.replace('/>', '><skipped/></testcase>', 1))
    def test_results_missing_suite(self):
        with self.assertRaises(ValueError): verify_results(XML.replace('cu_cp_setup_test', 'unrelated'))
    def test_results_failure(self):
        with self.assertRaises(ValueError): verify_results(XML.replace('failures="0"', 'failures="1"'))
    def test_results_inconsistent_count(self):
        with self.assertRaises(ValueError): verify_results(XML.replace('tests="2"', 'tests="3"'))
    def test_results_malformed(self):
        with self.assertRaises(Exception): verify_results('<incomplete')

if __name__ == "__main__": unittest.main()
