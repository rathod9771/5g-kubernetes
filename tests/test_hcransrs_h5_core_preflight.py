"""H5 isolated full auth-backend preflight: deterministic safety and negative controls."""
import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch

P=Path(__file__).resolve().parents[1]/"scripts/experiments/hcransrs_h5_core_preflight.py"
spec=importlib.util.spec_from_file_location("hcransrs_h5_core_preflight",P)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class H5AuthBackendContracts(unittest.TestCase):
    def test_runner_and_explicit_synthetic_only_restrictions(self):
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaises(m.PreflightError):m.check_isolated()
        with patch.dict(os.environ,{"GITHUB_ACTIONS":"true","RUNNER_ENVIRONMENT":"github-hosted"},clear=True):
            with self.assertRaises(m.PreflightError):m.check_isolated()
        with patch.dict(os.environ,{"GITHUB_ACTIONS":"true","RUNNER_ENVIRONMENT":"github-hosted",
            "HCRANSRS_ALLOW_H5_CORE":"EXACTLY_ISOLATED_SYNTHETIC_NO_DEPLOY"},clear=True):
            m.check_isolated()
    def test_exact_nf_config_and_database_uri(self):
        self.assertEqual(set(m.SBI_ROLES),{"nrf","ausf","udm","udr"})
        for role in m.SBI_ROLES:
            c=m.config(role)
            self.assertIn(m.IP[role],c)
            self.assertNotIn("localhost:7777",c)
            self.assertNotIn("kubernetes",c)
            if role=="udr":
                self.assertIn("db_uri: mongodb://172.29.79.12/open5gs",c)
            else:
                self.assertNotIn("db_uri:",c)
            if role!="nrf":
                self.assertIn("http://172.29.79.11:7777",c)
        with self.assertRaises(m.PreflightError):
            m.config("unknown")
    def test_synthetic_credentials_unique_and_never_artifact_data(self):
        a=m.subscriber();b=m.subscriber()
        self.assertTrue(m.check_subscriber_doc(a))
        self.assertTrue(m.check_subscriber_doc(b))
        self.assertNotEqual(a["security"]["k"],b["security"]["k"])
        self.assertNotEqual(a["security"]["opc"],b["security"]["opc"])
        self.assertEqual(a["slice"][0]["sd"],"010203")
        self.assertEqual(a["imsi"],"208930100001100")
        a["security"]["k"]="short"
        with self.assertRaises(m.PreflightError):m.check_subscriber_doc(a)
    def test_no_nas_or_pdu_production_claim(self):
        self.assertIn("NAS_REGISTRATION_ACCEPT",m.NOT_RUN)
        self.assertIn("PDU_SESSION",m.NOT_RUN)
        self.assertIn("OAI_UE_START",m.NOT_RUN)
        self.assertEqual(m.NET,"hcransrs-h5-core-only")
        self.assertEqual(m.CORE,"gradiant/open5gs:2.7.6")
        self.assertEqual(m.MONGO,"mongo:6.0")

    def test_sbi_read_only_h2_prior_knowledge_and_fail_closed_url(self):
        from unittest.mock import Mock
        sample=Mock(returncode=0,stdout='{"nfInstances":[]}')
        with patch.object(m.subprocess,"run",return_value=sample) as execute:
            data=m.h2_json_get("http://172.29.79.11:7777/nnrf-disc/v1/nf-instances")
        self.assertEqual(data,{"nfInstances":[]})
        args=execute.call_args.args[0]
        self.assertIn("--http2-prior-knowledge",args)
        self.assertIn("--max-filesize",args)
        self.assertIn("--silent",args)
        self.assertNotIn("--verbose",args)
        for url in ("https://example.com/nnrf-nfm/v1/nf-instances",
                    "http://127.0.0.1:7777/nudr-dr/v1/foo"):
            with self.subTest(url=url),self.assertRaises(m.PreflightError):
                m.h2_json_get(url)
    def test_sbi_rejects_http_failure_without_echoing_sensitive_body(self):
        from unittest.mock import Mock
        sample=Mock(returncode=22,stdout='SENSITIVE',stderr='SENSITIVE')
        with patch.object(m.subprocess,"run",return_value=sample):
            with self.assertRaises(m.PreflightError) as cm:
                m.h2_json_get("http://172.29.79.15:7777/nudr-dr/v1/foo")
            self.assertNotIn("SENSITIVE",str(cm.exception))

    def test_post_auth_udm_service_must_be_discovered_for_amf(self):
        seen=[]
        def fake(url):
            seen.append(url)
            return {"nfInstances":[{"nfType":"UDM","nfServices":[
                {"serviceName":"nudm-uecm","nfServiceStatus":"REGISTERED"}]}]}
        with patch.object(m,"h2_json_get",side_effect=fake):
            self.assertTrue(m.nrf_udm_service_for_amf("nudm-uecm"))
            self.assertFalse(m.nrf_udm_service_for_amf("nudm-sdm"))
        self.assertIn("requester-nf-type=AMF",seen[0])
        self.assertIn("service-names=nudm-uecm",seen[0])
        with self.assertRaises(m.PreflightError):
            m.nrf_udm_service_for_amf("nudm-ueau")
    def test_service_preflight_rejects_nf_without_named_service(self):
        with patch.object(m,"h2_json_get",return_value={"nfInstances":[{"nfType":"UDM"}]}):
            self.assertFalse(m.nrf_udm_service_for_amf("nudm-uecm"))

    def test_nrf_discovery_uses_correct_nf_consumer(self):
        from unittest.mock import Mock
        probes=[]
        def fake(url):
            probes.append(url)
            return {"nfInstances":[{"nfType":"UDR"}]}
        with patch.object(m,"h2_json_get",side_effect=fake):
            self.assertTrue(m.nrf_discovery("udr"))
        self.assertIn("target-nf-type=UDR",probes[0])
        self.assertIn("requester-nf-type=UDM",probes[0])
        with self.assertRaises(m.PreflightError):
            m.nrf_discovery("unknown")

if __name__=="__main__":
    unittest.main()
