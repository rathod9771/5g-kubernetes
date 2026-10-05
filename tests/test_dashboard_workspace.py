"""Execute real panel manager and integrations using a DOM identity model."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[1]

class WorkspaceTests(unittest.TestCase):
    def run_browser(self,body):
        if not shutil.which('node'):self.skipTest('Node required')
        html=(ROOT/'ran-selector/index.html').read_text()
        def section(start,end):return html[html.index(start):html.index(end,html.index(start))]
        source='\n'.join([section('// Persistent read-only functionality workspace.', 'const NF_LIST='),
                         section('const NF_LIST=', '// Restore Layer 2'),
                         section('function wfHTML(', 'async function deployRAN('),
                         section('function showDeployedPanel(', 'loadPods();\nsetInterval(loadPods,20000);'),
                         section('const EXTERNAL_PANELS =', 'let _extKey'),
                         section('async function loadOsmStatus(', 'async function deployCombo('),
                         section('async function refreshUEPanel(', 'async function refreshLiveBar(')])
        harness='''
const vm=require('vm'),assert=require('assert');const input=JSON.parse(require('fs').readFileSync(0,'utf8'));
const dom=require(input.dom)(), document=dom.document;let calls=[],intervals=new Map(),timeouts=new Map(),next=0;
let fail=false;
const sandbox={document,window:{},console,AbortController,DOMException,Date,Promise,
 localStorage:{setItem(){},removeItem(){}},alert(){},
 setInterval:f=>{const id=++next;intervals.set(id,f);return id;},clearInterval:id=>intervals.delete(id),
 setTimeout:f=>{const id=++next;timeouts.set(id,f);return id;},clearTimeout:id=>timeouts.delete(id),
 fetch:async(url)=>{calls.push(url);if(fail)throw Error('synthetic failure');return {ok:true,json:async()=>
 url==='/api/config'?{osm:'https://osm.invalid',rancher:'https://rancher.invalid',grafana:'https://grafana.invalid',prometheus:'https://prometheus.invalid'}:
 url==='/api/osm/status'?{pods:{total:1,ready:1},flux:[]}:
 url==='/api/ue-status'?{pod:'oai-nr-ue-cran-hash',registered:true,pdu_session:true,tun:'10.45.0.2'}:
 url==='/api/layer2-metrics'?{bler_dl:0,harq_retx_dl:0}:
 url==='/api/layer3-actions'?{actions:[]}:
 url==='/api/pods'?{pods:[]}:
 url.startsWith('/api/runtime/')?{found:true,pods:[],processes:[]}:
 url.startsWith('/api/logs/')?{logs:'registration complete',pod:'pod'}:
 {found:true,overall:'OPERATIONAL',statuses:[],events:[]}};}};
vm.createContext(sandbox);vm.runInContext(input.source+'\\nglobalThis.W=Workspace;',sandbox);
(async()=>{const W=sandbox.W;const button={classList:{add(){},remove(){}}};
'''+body+'''
})().catch(e=>{console.error(e);process.exit(1)});
'''
        result=subprocess.run(['node','-e',harness],input=json.dumps({'source':source,'dom':str(ROOT/'tests/workspace_dom.js')}),capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_persistence_controls_and_timer_cleanup(self):
        self.run_browser('''
sandbox.showDeployedPanel('vcran-srsran','vC-RAN','sub');await new Promise(setImmediate);
const ran=W.panels.get('ran'),saved=ran.body.innerHTML;const count=intervals.size;
sandbox.showCorePanel(button);await sandbox.showLayer2Panel(button);await sandbox.showLayer3Panel(button);
assert.equal(W.panels.size,4);assert.equal(ran.body.innerHTML,saved);
sandbox.showDeployedPanel('vcran-srsran','vC-RAN','sub');assert.equal(intervals.size,count+2);
W.minimizePanel('ran');assert(ran.body.hidden);assert.equal(ran.body.innerHTML,saved);
W.maximizePanel('ran');assert(W.panels.get('core').node.hidden);W.restorePanel('ran');assert(ran.minimized);assert(!W.panels.get('core').node.hidden);
W.focusPanel('ran');assert(!ran.minimized);assert.equal(W.panels.size,4);
const l2=W.panels.get('layer2');W.closePanel('ran');assert.equal(W.panels.size,3);assert(!intervals.has([...ran.timers][0]));assert.equal(W.panels.get('layer2'),l2);
for(let i=0;i<3;i++){sandbox.showDeployedPanel('cran-oai','C-RAN','sub');W.closePanel('ran');}assert.equal(intervals.size,2);
for(const p of W.panels.values()){assert.equal(p.header.querySelectorAll('.workspace-controls')[0].children.map(b=>b.textContent).join(''),'↻—□×');}
''')

    def test_external_application_refresh_is_independent(self):
        self.run_browser('''
for(const key of ['osm','rancher','grafana','prometheus'])await sandbox.showExternalPanel(button,key);
assert.equal(W.panels.size,4);
for(const key of ['osm','rancher','grafana','prometheus']){
 const p=W.panels.get(key),frame=p.body.querySelector('iframe'),src=frame.src,body=p.body;
 W.minimizePanel(key);const timers=intervals.size;calls=[];await W.refreshPanel(key);
 assert.equal(p.body,body);assert(p.minimized);assert.equal(frame.src,src);assert.equal(intervals.size,timers);
 assert.deepEqual(calls,key==='osm'?['/api/osm/status']:[]);
 const total=W.panels.size;await sandbox.showExternalPanel(button,key);assert.equal(W.panels.size,total);
}
''')

    def test_ue_layers_ran_refresh_preserves_view_and_state(self):
        self.run_browser('''
sandbox.showTopPanel(button,'ue');await sandbox.showLayer2Panel(button);await sandbox.showLayer3Panel(button);
sandbox.showDeployedPanel('cloudran-oai','Cloud-RAN','sub');await new Promise(setImmediate);
for(const [id,url] of [['ue','/api/ue-status'],['layer2','/api/layer2-metrics'],['layer3','/api/layer3-actions']]){
 const p=W.panels.get(id),before=p.body;W.maximizePanel(id);calls=[];const count=intervals.size;await W.refreshPanel(id);
 assert(calls.includes(url));assert.equal(p.body,before);assert(p.maximized);assert.equal(intervals.size,count);W.restorePanel(id);
}
assert.equal(document.getElementById('uei-reg').textContent,'REGISTERED');assert.equal(document.getElementById('uei-pdu').textContent,'ACTIVE');
const ueIntervals=intervals.size;for(let i=0;i<3;i++){sandbox.doTab({classList:{add(){}}},'logs','UE','UE','');}assert.equal(intervals.size,ueIntervals);
const btn={classList:{add(){}}};
for(const [view,url] of [['events','/api/events/cloudran-oai'],['logs','/api/logs/cloudran-oai?lines=100'],['status','/api/runtime/cloudran-oai']]){
 sandbox.doTab(btn,view,'cloudran-oai','cloudran-oai','ran');await new Promise(setImmediate);const count=intervals.size;calls=[];await W.refreshPanel('ran-'+view+'-cloudran-oai');
 assert(calls.includes(url));assert.equal(intervals.size,count);assert.equal(W.panels.get('ran').scenario,'cloudran-oai');
 assert(!calls.some(u=>/^\\/api\\/(events|runtime)\\/cloudran$/.test(u)));
}
''')

    def test_failure_is_local_and_concurrent_refresh_is_coalesced(self):
        self.run_browser('''
await sandbox.showLayer2Panel(button);await sandbox.showLayer3Panel(button);await new Promise(setImmediate);
const p=W.panels.get('layer2'),before=p.body.innerHTML,other=W.panels.get('layer3').body.innerHTML;
fail=true;await W.refreshPanel('layer2');assert(!p.error.hidden);assert.equal(p.error.textContent,'Refresh failed — retry');assert.equal(p.body.innerHTML,before);assert.equal(W.panels.get('layer3').body.innerHTML,other);
fail=false;let release;W.setRefresh('layer2',()=>new Promise(r=>release=r));const pending=W.refreshPanel('layer2');assert(p.busy);await W.refreshPanel('layer2');release();await pending;assert(!p.busy);assert(!p.refreshButton.disabled);
W.closePanel('layer2');assert(W.isPanelOpen('layer3'));
''')

    def test_request_cleanup_and_successful_switch_preserves_other_panels(self):
        self.run_browser('''
await sandbox.showLayer2Panel(button);await new Promise(setImmediate);const other=W.panels.get('layer2');
sandbox.showDeployedPanel('cran-srsran','C-RAN','sub');
W.openPanel('switch',{title:'RAN switch',refresh:async()=>{}});
sandbox.showDeployedPanel('cloudran-oai','Cloud-RAN','sub');
assert.equal(W.panels.get('layer2'),other);assert(!W.isPanelOpen('switch'));assert.equal(W.panels.get('ran').scenario,'cloudran-oai');
const originalFetch=sandbox.fetch;
sandbox.fetch=(url,options)=>new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(new DOMException('closed','AbortError'))));
const pending=W.request('layer2','/api/layer2-metrics').catch(e=>e.name);
assert.equal(other.controllers.size,1);W.closePanel('layer2');assert.equal(await pending,'AbortError');assert.equal(other.controllers.size,0);assert.equal(timeouts.size,0);
sandbox.fetch=originalFetch;
for(const id of ['core','ue','layer2','layer3','runtime','events','logs']){const body=W.openPanel(id,{title:id,refresh:async()=>{}});assert(body);assert(W.panels.get(id).refreshButton);}
assert(W.isPanelOpen('ran'));
''')
