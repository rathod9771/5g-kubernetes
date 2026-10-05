"""Execute dashboard switch completion against simulated browser/API responses."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DashboardSwitchTests(unittest.TestCase):
    def test_success_failure_lost_response_and_single_flight(self):
        if not shutil.which('node'):
            self.skipTest('Node required for browser execution')
        html=(ROOT/'ran-selector/index.html').read_text()
        source=html[html.index('let switchInFlight=false;'):html.index('function showComboDeployedPanel(')]
        harness=r'''
const vm=require('vm'),assert=require('assert');
const source=JSON.parse(require('fs').readFileSync(0,'utf8')).source;
(async()=>{
for(const mode of ['success','failed','lost']){
 const key='cloudran-oai', urls=[], alerts=[], elements={}, jobs=new Map();let next=0,posts=0,reads=0;
 const element=id=>elements[id]||(elements[id]={innerHTML:'',textContent:'',style:{},dataset:{},classList:{toggle(){}}});
 const buttons=[{dataset:{arch:'cloudran'},classList:{toggle(name,value){this.selected=value;}}}];
 const sandbox={document:{getElementById:element,querySelectorAll:selector=>selector==='.arch-btn'?buttons:[]},
  selectedArch:{key:'cloudran'},selectedStack:{key:'oai'},lastDeployedCombo:'cran-srsran',currentRAN:'cran-srsran',
  Workspace:{completeSwitchRender:render=>render(),refreshPanel:async()=>{urls.push("/api/runtime/cloudran-oai");urls.push("/api/events/cloudran-oai");}},
  panelCanvas:()=>element('main'),
  COMBO_DISPLAY:{'cloudran-oai':['cloudran']},clearTop(){},wfHTML:()=>'',blank(){},alert:m=>alerts.push(m),
  AbortController,Date,Promise,console,setTimeout:(f,ms)=>{const id=++next;jobs.set(id,{f,ms});return id;},clearTimeout:id=>jobs.delete(id),
  showComboDeployedPanel:k=>{sandbox.panelKey=k;},verifyClean:async()=>{sandbox.verified=true;},
  refreshLiveBar:async()=>{},loadPods:async()=>{},fetchRuntime:async k=>urls.push('/api/runtime/'+k),
  fetchClientEvents:async k=>urls.push('/api/events/'+k),
  fetch:async(url,opts)=>{
   urls.push(url);
   if(url==='/api/deploy'){
    posts++;assert.equal(JSON.parse(opts.body).ran,key);
    if(mode==='lost')throw Error('proxy disconnected');
    // Delay completion until tests have submitted a duplicate click.
    return new Promise(resolve=>sandbox.complete=()=>resolve({ok:mode==='success',json:async()=>mode==='success'?{status:'success',ran:key}:{error:'confirmed operation failure'}}));
   }
   if(url==='/api/status'){reads++;return {ok:true,json:async()=>({active:mode==='lost'&&reads>1?key:'none'})};}
   if(url==='/api/scenarios')return {ok:true,json:async()=>({scenarios:[{key,name:'Cloud-RAN OAI'}]})};
   throw Error('unexpected URL '+url);
  }};
 vm.createContext(sandbox);vm.runInContext(source,sandbox);
 const pending=sandbox.runDeployCombo(key,'Cloud-RAN','sub');
 await sandbox.runDeployCombo(key,'Cloud-RAN','sub');assert.equal(posts,1);
 await new Promise(setImmediate);
 if(mode==='lost'){
   const poll=[...jobs.values()].find(j=>j.ms===2000);assert(poll);await poll.f();
 }else sandbox.complete();
 await pending;
 assert.equal(jobs.size,0,'completion must cancel all poll timers');
 assert(!element('clean-badge').textContent.includes('Switching'));
 assert.equal(element('combo-deploy-btn').disabled,false);
 if(mode==='failed'){
  assert(alerts[0].includes('confirmed operation failure'));assert.equal(sandbox.lastDeployedCombo,'cran-srsran');
 }else{
  assert.equal(sandbox.lastDeployedCombo,key);assert.equal(sandbox.currentRAN,key);assert.equal(sandbox.panelKey,key);
  assert.equal(element('combo-deploy-btn').textContent,'✓ Deployed');assert(buttons[0].classList.selected);
  assert(urls.includes('/api/scenarios'));assert(urls.includes('/api/runtime/'+key));assert(urls.includes('/api/events/'+key));
  assert.equal(sandbox.selectedArch.key,'cloudran');assert.equal(sandbox.selectedStack.key,'oai');assert(sandbox.verified);
 }
 assert.equal(posts,1);
}
})().catch(e=>{console.error(e);process.exit(1)});
'''
        result=subprocess.run(['node','-e',harness],input=json.dumps({'source':source}),text=True,capture_output=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_javascript_syntax_and_no_reload(self):
        html=(ROOT/'ran-selector/index.html').read_text()
        source=html[html.index('let switchInFlight=false;'):html.index('function showComboDeployedPanel(')]
        self.assertNotIn('location.reload',source)
        self.assertNotIn('setInterval',source)
        if shutil.which('node'):
            scripts='\n'.join(re.findall(r'<script[^>]*>(.*?)</script>',html,re.S))
            result=subprocess.run(['node','--check'],input=scripts,text=True,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
