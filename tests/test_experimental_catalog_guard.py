"""Read-only catalog guards; never PUT/POST a package in tests."""
import sys
from pathlib import Path
import unittest
from unittest import mock
import yaml
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'tests')]
from osm_onboard import require_unused
from osm_catalog import CatalogError
from scenario_registry import load_registry,select_scenarios
from test_experimental_recovery import uid

class CatalogGuardTests(unittest.TestCase):
    def setUp(self):
        self.scenario=select_scenarios(load_registry(),['fran-oai'])[0]
        self.knf,self.nsd=uid(1),uid(2)
        self.catalog=mock.Mock();self.catalog.lookup.side_effect=lambda kind,name:self.knf if kind=='knf' else self.nsd
        self.instances=[{'_id':uid(20),'nsd-id':uid(9)}]
        self.descriptors=[{'_id':uid(9),'vnfd-id':['open5gs_knf']}]
        self.catalog.request.side_effect=lambda method,path:yaml.safe_dump(self.instances if path=='/nslcm/v1/ns_instances' else self.descriptors).encode()
        self.state={'active':'none','osm':{}}
    def invoke(self):return require_unused(self.catalog,self.scenario,self.knf,self.nsd,self.state)
    def tearDown(self):self.catalog.upload.assert_not_called()
    def test_unused_exact_pair_passes_without_upload(self):self.invoke()
    def test_active_pending_recovery_state_rejected(self):
        for field in ['active_instance_id','pending_instance','experimental_recovery']:
            self.state['osm']={field:'synthetic'}
            with self.assertRaises(CatalogError):self.invoke()
        self.state={'active':'fran-oai','osm':{}}
        with self.assertRaises(CatalogError):self.invoke()
    def test_uuid_mismatch_rejected(self):
        self.catalog.lookup.side_effect=lambda kind,name:uid(99)
        with self.assertRaisesRegex(CatalogError,'UUID'):self.invoke()
    def test_any_reference_to_target_nsd_rejected(self):
        self.instances.append({'_id':uid(30),'nsd-id':self.nsd,'nsState':'NOT_INSTANTIATED'})
        with self.assertRaisesRegex(CatalogError,'references.*NSD'):self.invoke()
    def test_other_nsd_using_same_knf_rejected(self):
        self.descriptors[0]['vnfd-id']=['fran_oai_knf']
        with self.assertRaisesRegex(CatalogError,'references.*KNF'):self.invoke()
    def test_unprovable_reference_fails_closed(self):
        self.descriptors[0].pop('vnfd-id')
        with self.assertRaisesRegex(CatalogError,'Cannot prove'):self.invoke()
    def test_nonexperimental_rejected(self):
        self.scenario=select_scenarios(load_registry(),['cran-oai'])[0]
        with self.assertRaisesRegex(CatalogError,'experimental'):self.invoke()
