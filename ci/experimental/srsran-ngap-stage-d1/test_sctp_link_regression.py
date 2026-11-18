"""Offline srsRAN SCTP gateway linker correction regression (NO native execution)."""
import json, unittest
from pathlib import Path
P=Path(__file__).resolve().parent
UPSTREAM_PIN='11c9bbabb69873752500d676f55e0034f6caa5c5'
GATEWAY_CMAKE_SHA='13774a3e9893d9767ae23d594e595fd9336363f07d860c6f9589f91d9cf736e2'
SYMBOL='srsran::create_sctp_network_client'

def reviewed_driver_dependencies(cmake_text):
    matches=[line for line in cmake_text.splitlines()
             if line.startswith('target_link_libraries(l3_ngap_protocol_runtime PRIVATE ')]
    if len(matches)!=1 or not matches[0].endswith(')'):
        raise ValueError('MISSING_OR_AMBIGUOUS_DRIVER_LINK_EDGE')
    libs=matches[0].removeprefix('target_link_libraries(l3_ngap_protocol_runtime PRIVATE ').removesuffix(')').split()
    if libs.count('srsran_gateway')!=1:
        raise ValueError('SCTP_GATEWAY_LIB_MISSING_OR_DUPLICATE')
    if libs.index('srsran_gateway')!=libs.index('srsran_ngap')+1:
        raise ValueError('SCTP_GATEWAY_ORDER')
    if 'srsran_pcap' not in libs or libs.index('srsran_gateway')>libs.index('srsran_pcap'):
        raise ValueError('GATEWAY_ORDER_DRIFT')
    return libs

class SctpLinkCorrection(unittest.TestCase):
    def test_link_target_after_ngap_archive(self):
        libs=reviewed_driver_dependencies((P/'CMakeLists.txt').read_text())
        self.assertIn('srsran_cu_cp',libs)
        self.assertIn('srsran_support',libs)
    def test_upstream_target_pinned_and_unchanged(self):
        policy=json.loads((P/'dependency-command-policy.json').read_text())
        self.assertEqual(policy['sources']['srsran']['revision'],UPSTREAM_PIN)
        self.assertEqual(policy['sources']['srsran']['build_control_hashes']['lib/gateways/CMakeLists.txt'],GATEWAY_CMAKE_SHA)
    def test_missing_gateway_rejected(self):
        cmake=(P/'CMakeLists.txt').read_text().replace('srsran_gateway','')
        with self.assertRaisesRegex(ValueError,'SCTP_GATEWAY'):reviewed_driver_dependencies(cmake)
    def test_duplicate_gateway_rejected(self):
        cmake=(P/'CMakeLists.txt').read_text().replace('srsran_gateway','srsran_gateway srsran_gateway')
        with self.assertRaisesRegex(ValueError,'SCTP_GATEWAY'):reviewed_driver_dependencies(cmake)
    def test_reordered_gateway_rejected(self):
        cmake=(P/'CMakeLists.txt').read_text().replace('srsran_ngap srsran_gateway','srsran_gateway srsran_ngap')
        with self.assertRaisesRegex(ValueError,'SCTP_GATEWAY_ORDER'):reviewed_driver_dependencies(cmake)
    def test_no_prohibited_protocol_runtime_target_registered(self):
        cmake=(P/'CMakeLists.txt').read_text()
        self.assertNotIn('add_test(',cmake)
        self.assertNotIn('enable_testing(',cmake)
        self.assertNotIn('execute_process(',cmake)
        self.assertEqual(cmake.count('add_executable(l3_ngap_protocol_runtime'),1)
    def test_compilation_remains_separately_qualified(self):
        worker=(P/'build_worker.py').read_text()
        self.assertIn('admit_reviewed_open5gs_graph(graph,review,expanded_report)',worker)
        self.assertIn("rec.phase('srsran_compilation'",worker)
        self.assertNotIn("['ctest'",worker)

if __name__=='__main__':unittest.main()
