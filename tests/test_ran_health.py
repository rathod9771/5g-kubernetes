"""Precision kernel association fixtures, independent of socket/log formatting."""
import copy
import io
import json
import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ran_health', ROOT/'scripts/ran_health.py')
health = importlib.util.module_from_spec(spec)
spec.loader.exec_module(health)
HEADER = 'ASSOC SOCK STY SST ST HBKT ASSOC-ID TX_QUEUE RX_QUEUE UID INODE LPORT RPORT LADDRS <-> RADDRS\n'
# Precision: ephemeral NGAP source, CU F1 listener, DU F1 client.
CU = HEADER + ('a b 2 1 3 0 1 0 0 0 42 43252 38412 10.244.0.195 <-> 10.101.67.60\n'
               'c d 2 1 3 0 2 0 0 0 43 38472 57335 10.244.0.195 <-> 10.244.0.196\n')
DU = HEADER + 'e f 2 1 3 0 3 0 0 0 44 57335 38472 10.244.0.196 <-> 10.109.157.83\n'


def pod(role):
    return {'metadata': {'name': 'test-'+role, 'labels': {'component': role}},
            'spec': {'containers': [{'name': role, 'image': 'localhost/5g-kubernetes/srsran:25.04.0-11c9bbabb6'}]},
            'status': {'phase': 'Running', 'conditions': [{'type': 'Ready', 'status': 'True'}]}}


class SCTPHealthTests(unittest.TestCase):
    def setUp(self):
        self.pods = {'items': [pod('du'), pod('cu')]}
        self.read = lambda p, role: CU if role == 'cu' else DU

    def test_exact_precision_ngap_and_both_f1_associations(self):
        self.assertTrue(health.established(CU, 38412, remote_only=True))
        self.assertTrue(health.established(CU, 38472))
        self.assertTrue(health.established(DU, 38472))
        self.assertEqual(health.health(self.pods, self.read)[0], 'READY')

    def test_state_is_st_not_sst_or_sty(self):
        table = CU.replace('2 1 3 ', '3 3 2 ')
        self.assertFalse(health.established(table, 38412, remote_only=True))

    def test_ngap_requires_remote_port(self):
        table = CU.replace('43252 38412', '38412 43252')
        self.assertFalse(health.established(table, 38412, remote_only=True))

    def test_ports_in_address_or_inode_columns_are_not_evidence(self):
        self.assertFalse(health.established(CU.replace('43252 38412', '43252 9000').replace('42 ', '38412 '),38412))

    def test_header_driven_layout_and_malformed_rows(self):
        self.assertTrue(health.established('ST RPORT LPORT\n3 38412 43252\n',38412,True))
        for table in ['', HEADER, HEADER+'short row\n', 'STY SST LPORT RPORT\n3 3 1 38412\n']:
            self.assertFalse(health.established(table,38412,True))

    def test_running_but_not_ready_is_degraded_without_exec(self):
        self.pods['items'][0]['status']['conditions'][0]['status']='False'
        read=mock.Mock()
        self.assertEqual(health.health(self.pods,read)[0],'DEGRADED');read.assert_not_called()

    def test_missing_du_or_nonrunning_cu_fails(self):
        self.assertEqual(health.health({'items':[pod('cu')]},self.read)[0],'DEGRADED')
        self.pods['items'][1]['status']['phase']='Pending'
        self.assertEqual(health.health(self.pods,self.read)[0],'DOWN')

    def test_ngap_and_f1_required_with_established_state(self):
        for cu,du in [(HEADER,DU),(CU,HEADER),(CU.replace('38472','9000'),DU),
                      (CU,DU.replace('2 1 3 ','2 1 2 '))]:
            self.assertEqual(health.health(self.pods,lambda p,r:cu if r=='cu' else du)[0],'DEGRADED')

    def test_exec_failures_fail_closed_without_stderr_leak(self):
        for error in [OSError('private'),subprocess.CalledProcessError(1,[],stderr='private'),
                      subprocess.TimeoutExpired([],15)]:
            result,evidence=health.health(self.pods,mock.Mock(side_effect=error))
            self.assertEqual(result,'DEGRADED');self.assertNotIn('private',evidence)

    def test_cli_reads_exact_kernel_source_in_each_ran_container(self):
        def run(args, **kwargs):
            self.assertEqual(args[-3:], ['--','cat','/proc/net/sctp/assocs'])
            role=args[args.index('-c')+1]
            self.assertEqual(kwargs,dict(capture_output=True,text=True,timeout=15,check=True))
            return subprocess.CompletedProcess(args,0,CU if role=='cu' else DU)
        output=io.StringIO()
        with mock.patch.object(health.sys,'argv',['ran_health.py','--namespace','test-ns','--kubeconfig','/tmp/config']), \
             mock.patch.object(health.sys,'stdin',io.StringIO(json.dumps(self.pods))), \
             mock.patch.object(health.sys,'stdout',output), \
             mock.patch.object(health.subprocess,'run',side_effect=run) as execute:
            health.main()
        self.assertTrue(output.getvalue().startswith('READY\n'))
        self.assertEqual(execute.call_count,2)

    def test_oai_and_non_srsran_use_existing_path(self):
        pods=copy.deepcopy(self.pods)
        for p in pods['items']:p['spec']['containers'][0]['image']='oaisoftwarealliance/oai-gnb:2026.w13'
        read=mock.Mock()
        self.assertEqual(health.health(pods,read)[0],'NOT_APPLICABLE');read.assert_not_called()
        text=(ROOT/'scripts/validate.sh').read_text()
        self.assertIn('NOT_APPLICABLE)',text);self.assertIn('-- ss -a',text)
        self.assertIn('scripts/validate.sh" --kv',(ROOT/'status.sh').read_text())


if __name__=='__main__':unittest.main()
