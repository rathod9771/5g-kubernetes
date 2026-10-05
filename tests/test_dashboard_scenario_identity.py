"""Exercise actual browser functions and backend catalog resolution for six RANs."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest import mock
import yaml
import test_pending_adoption as adoption
backend,ROOT=adoption.backend,adoption.ROOT
from scenario_registry import load_registry,dashboard_scenarios

KEYS=['cran-srsran','cran-oai','vcran-srsran','vcran-oai','cloudran-srsran','cloudran-oai']


class DashboardScenarioTests(unittest.TestCase):
    setUp=adoption.PendingAdoptionTests.setUp
    write=adoption.PendingAdoptionTests.write

    def test_all_six_catalog_pods_runtime_events_and_logs(self):
        registry=load_registry();entries={s['key']:s for s in registry['scenarios']}
        pods=[]
        for key in KEYS:
            scenario=entries[key];label,value=scenario['selector'].split('=',1)
            pods.extend({'metadata':{'name':name+'-hash-pod','labels':{label:value}},'status':{'phase':'Running'}}
                        for name in scenario['pods'])
        with mock.patch.object(backend,'_namespace',return_value='test'), \
             mock.patch.object(backend,'_kubectl_json',return_value={'items':pods}), \
             mock.patch.object(backend,'_live_processes',return_value=[]), \
             mock.patch.object(backend,'_prom_query',return_value=None), \
             mock.patch.object(backend,'_client_events',side_effect=lambda key:{'key':key}) as events, \
             mock.patch.object(backend,'_log_text_for_pod',return_value='technical logs'):
            for key in KEYS:
                expected=[name+'-hash-pod' for name in entries[key]['pods']]
                self.assertEqual([p['metadata']['name'] for p in backend._scenario_pods(key)],expected)
                self.assertEqual([p['metadata']['name'] for p in backend._runtime_targets(key)],expected)
                response=self.client.get('/api/runtime/'+key)
                self.assertEqual(response.status_code,200);self.assertEqual(response.json['key'],key)
                self.assertEqual([p['name'] for p in response.json['pods']],expected)
                self.assertEqual(self.client.get('/api/events/'+key).json['key'],key)
                events.assert_called_with(key)
                response=self.client.get('/api/logs/'+key)
                self.assertEqual(response.status_code,200);self.assertEqual(response.json['pods'],expected)

    def test_architecture_keys_fail_clearly_without_guessing_or_queries(self):
        with mock.patch.object(backend,'_runtime_targets') as runtime, mock.patch.object(backend,'_client_events') as events:
            for architecture in ['cran','vcran','cloudran']:
                for endpoint in ['runtime','events']:
                    response=self.client.get('/api/'+endpoint+'/'+architecture)
                    self.assertEqual(response.status_code,400);self.assertIn('canonical scenario key',response.json['error'])
            runtime.assert_not_called();events.assert_not_called()

    def test_successful_deploy_records_its_instance_scenario_and_operation_for_all_six(self):
        registry=load_registry();entries={s['key']:s for s in registry['scenarios']};scenarios=dashboard_scenarios(registry)
        with mock.patch.object(backend.osm_client,'wait_for_op',return_value='COMPLETED'):
            for key in KEYS:
                state={'active':'none','context':self.context,'osm':{'core_instance_id':'core','active_operation_id':'old-op'}}
                self.write(state);self.instantiate.return_value=('instance-'+key,'operation-'+key)
                with backend.app.test_request_context():
                    response=backend._deploy_verified(key,registry,entries,scenarios[key],entries[key],self.context,{'ns':'verified-nsd'})
                self.assertEqual(response.status_code,200)
                saved=yaml.safe_load(self.path.read_bytes())
                self.assertEqual(saved['active'],key);self.assertEqual(saved['context'],self.context)
                self.assertEqual(saved['osm'],{'core_instance_id':'core','active_instance_id':'instance-'+key,
                                             'active_scenario':key,'active_operation_id':'operation-'+key})
        self.terminate.assert_not_called();self.delete.assert_not_called()

    def test_browser_canonical_tabs_restoration_polling_and_javascript_syntax(self):
        if not shutil.which('node'):self.skipTest('Node required for browser-function execution')
        html=(ROOT/'ran-selector/index.html').read_text()
        import re
        scripts='\n'.join(re.findall(r'<script[^>]*>(.*?)</script>',html,re.S))
        subprocess.run(['node','--check'],input=scripts,text=True,capture_output=True,check=True)
        def section(start,end):return html[html.index(start):html.index(end,html.index(start))]
        functions='\n'.join([section('function tabsHTML(', 'function renderProcess('),
                             section('async function fetchClientEvents(', 'async function deployRAN('),
                             section('function showDeployedPanel(', 'function showCorePanel('),
                             section('function showComboDeployedPanel(', '</script>'),
                             section('const COMBO_DISPLAY =', 'window.addEventListener("load", restoreDeployedState);')])
        payload={'source':functions,'catalog':self.client.get('/api/scenarios').json,'keys':KEYS}
        harness=r'''
const vm=require('vm'),assert=require('assert');const input=JSON.parse(require('fs').readFileSync(0,'utf8'));
(async()=>{
 for(const key of input.keys){
  let urls=[],timers=[];
  const elements={};const element=id=>elements[id]||(elements[id]={innerHTML:'',querySelectorAll:()=>[],parentElement:{querySelectorAll:()=>[]}});
  const sandbox={window:{},document:{getElementById:element,querySelectorAll:()=>[]},lastDeployedCombo:'different-scenario',
   wfHTML:()=>'',updateRANHealth:()=>{},renderClientEvents:()=>'',renderProcess:()=>'',renderStatus:()=>'',console,
   setInterval:f=>{timers.push(f);return timers.length;},clearInterval:()=>{},
   fetch:async url=>{urls.push(url);return {json:async()=>url==='/api/scenarios'?input.catalog:url==='/api/verify-clean'?{active_combos:[key]}:{logs:''}};}};
  sandbox.Workspace={panels:new Map(),closePanel(id){this.panels.delete(id)},focusPanel(){},setRefresh(){},request:async(id,url)=>{const r=await sandbox.fetch(url);return r.json();}};
  sandbox.panelCanvas=(id)=>{sandbox.Workspace.panels.set(id,{timers:new Set()});return element('main')};
  sandbox.panelTimer=(id,timer)=>timer;sandbox.liveOwner=()=>'';sandbox.panelForTarget=()=>"ran";sandbox.openRANView=(view,pod,key)=>{if(view==='events')sandbox.fetchClientEvents(key,'ranpbody');else if(view==='logs')sandbox.fetchLogs(pod,'ranlterm');else sandbox.fetchRuntime(key,'ranruntimebody',view);};sandbox.refreshLiveView=()=>{};sandbox.panelError=()=>{};
  vm.createContext(sandbox);vm.runInContext(input.source,sandbox);
  for(const restore of [true,false]){
   urls=[];timers=[];
   if(restore)await sandbox.restoreDeployedState();else sandbox.showComboDeployedPanel(key,'display','sub','oai');
   const tabHTML=element('main').innerHTML;
   const matches=[...tabHTML.matchAll(/doTab\(this,'([^']+)','([^']+)','([^']+)','ran'\)/g)];
   assert.equal(matches.length,4);
   for(const match of matches){assert.equal(match[2],key);assert.equal(match[3],key);
    sandbox.doTab({classList:{add(){}}},match[1],match[2],match[3],'ran');
   }
   for(const timer of timers)timer();
   await new Promise(resolve=>setImmediate(resolve));
   assert(urls.includes('/api/events/'+key));assert(urls.includes('/api/runtime/'+key));assert(urls.includes('/api/logs/'+key+'?lines=100'));
   for(const url of urls)assert(!/^\/api\/(events|runtime)\/(cran|vcran|cloudran)$/.test(url));
  }
 }
})().catch(error=>{console.error(error);process.exit(1)});
'''
        # Regexes are JavaScript source, not Python regular expressions.
        harness=harness.replace('\\\\','\\')
        result=subprocess.run(['node','-e',harness],input=json.dumps(payload),text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)
