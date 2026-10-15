"""Assert final packaged Helm output, including native libconfig grammar."""
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import osm_packages as p
from scenario_registry import load_registry,select_scenarios

class IntegerRenderingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.directory=Path(cls.temp.name)
        cls.scenarios=select_scenarios(load_registry(),['fran-oai','hcran-oai','cran-oai','vcran-oai','cloudran-oai'])
        cls.configs={};cls.rendered={}
        for scenario in cls.scenarios:
            files=p.expected_artifacts(ROOT,scenario)
            for chart in scenario['charts']:
                if chart.get('role','ran')!='ran':continue
                prefix=scenario['knf_package']+'/helm-chart-v3s/'+chart['kdu']+'/'
                destination=cls.directory/scenario['key']/chart['kdu']
                for name,data in files.items():
                    if name.startswith(prefix):
                        file=destination/name[len(prefix):];file.parent.mkdir(parents=True,exist_ok=True);file.write_bytes(data)
                docs=p.render(destination,chart['release_name'])
                cls.rendered[(scenario['key'],chart['kdu'])]=docs
                cls.configs[(scenario['key'],chart['kdu'])]=next(d for d in docs if d['kind']=='ConfigMap')['data']['gnb.conf']
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def test_fog_exact_decimal_integer_ids_in_packaged_charts(self):
        for role in ['cu','du']:
            text=self.configs[('fran-oai',role)]
            self.assertIn('gNB_ID = 3586;',text)
            self.assertIn('nr_cellid = 58753025L;',text)
        self.assertIn('gNB_DU_ID = 100;',self.configs[('fran-oai','du')])
    def test_hcran_unique_decimal_du_cell_ids_and_shared_cu(self):
        for role,du_id,cell_id in [('macro',100,58736641),('small',101,58736642)]:
            text=self.configs[('hcran-oai',role)]
            self.assertIn('gNB_ID = 3585;',text)
            self.assertIn(f'gNB_DU_ID = {du_id};',text)
            self.assertIn(f'nr_cellid = {cell_id}L;',text)
        self.assertIn('gNB_ID = 3585;',self.configs[('hcran-oai','cu')])
    def test_maximum_36bit_cell_identifier_and_float_input(self):
        directory=self.directory/'fran-oai'/'du'
        override=self.directory/'maximum.json'
        override.write_text(json.dumps({'radio':{'gnbId':3586.0,'duId':100.0,'cellId':68719476735.0}}))
        docs=p.render(directory,'fran-oai-du',[override])
        config=next(d for d in docs if d['kind']=='ConfigMap')['data']['gnb.conf']
        self.assertIn('gNB_ID = 3586;',config)
        self.assertIn('gNB_DU_ID = 100;',config)
        self.assertIn('nr_cellid = 68719476735L;',config)

    def test_no_hex_float_or_exponent_identifiers(self):
        for text in self.configs.values():
            for field in ['gNB_ID','gNB_DU_ID','nr_cellid']:
                for value in re.findall(r'\b'+field+r'\s*=\s*([^;]+);',text):
                    self.assertRegex(value,r'^(?:[0-9]+L?|0x[0-9a-fA-F]+)$')
                    self.assertNotIn('0x0x',value);self.assertNotRegex(value,r'[pP][+-]|[0-9][eE][+-]')
    def test_existing_default_identifiers_unchanged(self):
        for key in ['cran-oai','vcran-oai','cloudran-oai']:
            for role in ['cu','du']:
                text=self.configs[(key,role)]
                self.assertIn('gNB_ID = 0xe00;',text);self.assertIn('nr_cellid = 12345678L;',text)
    def test_native_libconfig_reads_complete_rendered_config(self):
        if not shutil.which('cc') or subprocess.run(['pkg-config','--exists','libconfig']).returncode:
            self.skipTest('Native libconfig headers/compiler unavailable; no installation performed')
        source=self.directory/'parse.c';binary=self.directory/'parse-config'
        source.write_text('#include <libconfig.h>\nint main(int n,char **v){config_t c;config_init(&c);int ok=config_read_file(&c,v[1]);config_destroy(&c);return ok?0:1;}\n')
        flags=shlex.split(subprocess.check_output(['pkg-config','--cflags','--libs','libconfig'],text=True))
        subprocess.run(['cc',str(source),'-o',str(binary),*flags],check=True,capture_output=True,timeout=30)
        for key,text in self.configs.items():
            file=self.directory/('config-'+key[0]+'-'+key[1]+'.conf');file.write_text(text)
            with self.subTest(key=key):
                self.assertEqual(subprocess.run([str(binary),str(file)],timeout=5).returncode,0)
        file.write_text('gNB_ID = 0x0x1.c04p+11;\nnr_cellid = 5.8753025e+07L;\n')
        self.assertNotEqual(subprocess.run([str(binary),str(file)],timeout=5).returncode,0)
