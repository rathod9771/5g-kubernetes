"""Offline schema/privacy checks; never create or use a subscriber context."""
import asyncio,importlib.util,json,os,pathlib,sys,tempfile,unittest
from unittest import mock
ROOT=pathlib.Path(__file__).resolve().parents[1];PKG=ROOT/'ci/experimental/open5gs-udm-integration'
sys.path.insert(0,str(PKG))
import sbi_fixture as f
import run_integration as run
import wheels
sys.path.insert(0,str(ROOT/'scripts'))
import release_impact
from scenario_registry import load_registry

class IntegrationPreparationTests(unittest.TestCase):
    def test_valid_profile(self):
        p=f.profile('A',7777);self.assertEqual(p['nfServices'][0]['ipEndPoints'][0]['port'],7777)
        self.assertEqual(p['nfServices'][0]['versions'][0]['apiVersionInUri'],'v1')
    def test_incomplete_and_mismatched_profiles(self):
        self.assertNotIn('nfServices',f.profile('B',7777))
        self.assertEqual(f.profile('C',7777)['nfServices'][0]['versions'][0]['apiVersionInUri'],'v9')
        self.assertEqual(f.profile('D',80)['nfServices'][0]['ipEndPoints'][0]['port'],80)
    def test_refresh_retains_nf_and_service_identity(self):
        a,b=f.profile('E',7777),f.profile('E',8888)
        self.assertEqual(a['nfInstanceId'],b['nfInstanceId']);self.assertEqual(a['nfServices'][0]['serviceInstanceId'],b['nfServices'][0]['serviceInstanceId'])
    def test_udr_error_cannot_generate_vectors(self):
        op,status,body,headers=f.dispatch(7777,'GET','/nudr-dr/v1/subscription-data/PRIVATE-SENTINEL/authentication-data/authentication-subscription',b'',{'case':'A','port':7777})
        self.assertEqual((op,status),('authentication-subscription',404));self.assertNotIn('PRIVATE-SENTINEL',json.dumps(body))
        self.assertNotIn('authenticationVector',json.dumps(body));self.assertEqual(headers['content-type'],'application/problem+json')
    def test_discovery_fixture_does_not_echo_query(self):
        op,status,body,h=f.dispatch(9000,'GET','/nnrf-disc/v1/nf-instances?not-retained=PRIVATE-SENTINEL',b'',{'case':'B','port':7777})
        self.assertEqual((op,status),('discovery',200));self.assertNotIn('PRIVATE-SENTINEL',json.dumps(body))
    def test_event_output_is_allowlisted(self):
        with tempfile.TemporaryDirectory() as t,mock.patch.object(f,'OUTPUT',pathlib.Path(t)/'events.json'),mock.patch.object(f,'EVENTS',[]),mock.patch.object(f,'ERRORS',{}):
            f.record('authentication-subscription',7777,'GET',404,'172.29.250.3',run.time.monotonic())
            v=json.loads(f.OUTPUT.read_text());self.assertTrue(v['complete'])
            for bad in ('body','uri','path','headers','key','identifier'):self.assertNotIn(bad,v['events'][0])
    def test_private_curl_does_not_persist_sensitive_metadata(self):
        data={'url_effective':'SECRET-SENTINEL','http_code':404,'http_version':'2','remote_ip':'172.29.250.3','remote_port':7777,'time_total':.01}
        p=mock.Mock(returncode=0,stdout=json.dumps(data).encode(),stderr=b'')
        with mock.patch.object(run.subprocess,'run',return_value=p):
            out=run.request('internal',pathlib.Path('/private'),pathlib.Path('trial.conf'))
        self.assertNotIn('SECRET-SENTINEL',json.dumps(out));self.assertEqual(out['http_code'],404)
    def test_curl_failure_does_not_echo_stderr(self):
        p=mock.Mock(returncode=28,stdout=b'',stderr=b'SECRET-SENTINEL')
        with mock.patch.object(run.subprocess,'run',return_value=p):out=run.request('internal',pathlib.Path('/private'),pathlib.Path('trial.conf'))
        self.assertEqual(out['curl_exit'],28);self.assertNotIn('SECRET-SENTINEL',json.dumps(out))
    def test_expired_host_guard(self):
        with mock.patch.dict(os.environ,{},clear=True),mock.patch.object(run,'docker') as d:
            with self.assertRaises(RuntimeError):run.main(pathlib.Path('/unused'))
            d.assert_not_called()
    def test_h2_fixture_roundtrip_and_redaction_without_socket(self):
        with tempfile.TemporaryDirectory() as t:
            root=pathlib.Path(t);vendor=root/'vendor';wheels.unpack(PKG,vendor)
            sys.path.insert(0,str(vendor))
            try:
                import h2.config,h2.connection,h2.events
                client=h2.connection.H2Connection(config=h2.config.H2Configuration(client_side=True))
                client.initiate_connection()
                client.send_headers(1,[(':method','GET'),(':scheme','http'),(':authority','fixture'),
                    (':path','/nudr-dr/v1/subscription-data/PRIVATE-SENTINEL/authentication-data/authentication-subscription')],end_stream=True)
                class Reader:
                    def __init__(self):self.data=client.data_to_send()
                    async def read(self,n):v=self.data;self.data=b'';return v
                class Writer:
                    def __init__(self):self.data=bytearray()
                    def write(self,v):self.data.extend(v)
                    async def drain(self):pass
                    def close(self):pass
                    async def wait_closed(self):pass
                    def get_extra_info(self,k):return ('172.29.250.2',7777) if k=='sockname' else ('172.29.250.3',1234)
                state=root/'state.json';state.write_text('{"case":"A","port":7777}')
                writer=Writer()
                with mock.patch.object(f,'OUTPUT',root/'events.json'),mock.patch.object(f,'STATE',state),mock.patch.object(f,'EVENTS',[]),mock.patch.object(f,'ERRORS',{}):
                    asyncio.run(f.handle(Reader(),writer))
                    stored=f.OUTPUT.read_text();self.assertNotIn('PRIVATE-SENTINEL',stored)
                    self.assertTrue(json.loads(stored)['complete'])
                response=client.receive_data(bytes(writer.data))
                self.assertTrue(any(isinstance(e,h2.events.ResponseReceived) and (b':status',b'404') in e.headers for e in response))
            finally:sys.path.remove(str(vendor))

    def test_pinned_wheels_unpack_without_installation(self):
        with tempfile.TemporaryDirectory() as t:
            p=pathlib.Path(t);wheels.unpack(PKG,p)
            self.assertTrue((p/'h2/connection.py').is_file())
    def test_checksum_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            p=pathlib.Path(t);(p/'inputs').mkdir();(p/'inputs/bad.whl').write_bytes(b'bad')
            (p/'fixture-dependencies.json').write_text(json.dumps([{'filename':'bad.whl','sha256':'0'*64}]))
            with self.assertRaises(ValueError):wheels.unpack(p,p/'out')
    def test_incomplete_fixture_evidence_rejected(self):
        with mock.patch.object(run,'docker',return_value=b'{"events":[],"complete":false}'):
            with self.assertRaises(RuntimeError):run.events('fixture')
    def test_one_request_per_case_and_explicit_refresh(self):
        s=(PKG/'run_integration.py').read_text();self.assertIn("result['subscriber_attempts']=2",s)
        self.assertIn("'NF_REGISTERED'",s);self.assertNotIn('NF_PROFILE_CHANGED',s)
        self.assertIn("result['initial_process']['pid']==result['final_process']['pid']",s)
    def test_workflow_does_not_upload_private_paths(self):
        s=(ROOT/'.github/workflows/experimental-open5gs-udm-integration.yml').read_text()
        upload=s[s.index('name: Export only'):]
        self.assertNotIn('/dev/shm',upload);self.assertNotIn('evidence/**',upload)
        self.assertIn('stage8-cleanup.txt',upload);self.assertNotIn('Runtime.Dockerfile',s)
        self.assertNotIn('self-hosted',s);self.assertNotIn('kubectl',s)
    def test_exact_release_impact_scope(self):
        r=load_registry(ROOT)
        for p in PKG.rglob('*'):
            if p.is_file() and '__pycache__' not in str(p):self.assertEqual(release_impact.classify(str(p.relative_to(ROOT)),r),('non-package',set(),False))
        self.assertEqual(release_impact.classify('ci/experimental/open5gs-udm-integration-other/x.py',r)[0],'unknown')
