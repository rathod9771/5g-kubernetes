"""Dashboard release gating and repository guide, without lifecycle calls."""
import json
from pathlib import Path
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[1]
HTML=(ROOT/'ran-selector/index.html').read_text()
GUIDE=ROOT/'docs/dashboard-and-ran-architecture-guide.md'

class DashboardGuideTests(unittest.TestCase):
    def test_disabled_clicks_and_direct_entry_cannot_deploy(self):
        selection=HTML[HTML.index('let selectedArch=null'):HTML.index('let lastDeployedCombo=null')]
        deploy=HTML[HTML.index('async function deployCombo()'):HTML.index('let switchInFlight=false;')]
        direct=HTML[HTML.index('async function runDeployCombo('):HTML.index('function showComboDeployedPanel(')]
        harness=r'''
const vm=require('vm'),assert=require('assert'),input=JSON.parse(require('fs').readFileSync(0,'utf8'));
const calls=[],messages=[],nodes={};const node=()=>({style:{},dataset:{},innerHTML:'button',classList:{add(){},remove(){}},setAttribute(){},getAttribute(){return '';}});
const stackButton=node();stackButton.getAttribute=()=>"selectStack(this,'srsran','srsRAN')";stackButton.setAttribute=(name,value)=>{stackButton[name]=value;};
const sandbox={console,document:{querySelectorAll:selector=>selector==='.stack-btn'?[stackButton]:[],getElementById:id=>nodes[id]||(nodes[id]=node())},showInfoModal:m=>messages.push(m),showConfirmModal:(n,f)=>f(),lastDeployedCombo:null,switchInFlight:false,fetch:()=>{throw Error('No disabled request allowed');}};
vm.createContext(sandbox);vm.runInContext(input.selection+input.deploy,sandbox);
sandbox.runDeployCombo=key=>calls.push(key);
(async()=>{
for(const arch of ['hcran','fran']){sandbox.selectArch(node(),arch,arch,true);assert(messages.pop().includes('not enabled'));}
for(const arch of ['cran','vcran','cloudran','oran'])for(const stack of ['srsran','oai']){
 sandbox.selectArch(node(),arch,arch,true);sandbox.selectStack(node(),stack,stack);
 await sandbox.deployCombo();
 if(arch==='oran'&&stack==='srsran'){assert.equal(calls.length,6);assert(messages.pop().includes('not enabled'));}
}
assert.equal(stackButton['aria-disabled'],'true');assert(stackButton.innerHTML.includes('Disabled / Future'));
sandbox.selectArch(node(),'cran','C-RAN',true);assert.equal(stackButton['aria-disabled'],'false');assert(!stackButton.innerHTML.includes('Disabled / Future'));
assert.deepEqual(calls,['cran-srsran','cran-oai','vcran-srsran','vcran-oai','cloudran-srsran','cloudran-oai','oran-oai']);
vm.runInContext(input.direct,sandbox);
for(const key of ['hcran-oai','hcran-srsran','fran','fran-oai','oran-srsran']){await sandbox.runDeployCombo(key,key,'');assert(messages.pop().includes('not enabled'));}
})().catch(e=>{console.error(e);process.exit(1)});
'''
        result=subprocess.run(['node','-e',harness],input=json.dumps(dict(selection=selection,deploy=deploy,direct=direct)),capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_markdown_is_safe_and_formats_guide(self):
        source=HTML[HTML.index('function renderGuideMarkdown('):HTML.index('async function refreshGuide(')]
        code="const vm=require('vm'),assert=require('assert'),i=JSON.parse(require('fs').readFileSync(0,'utf8')),s={};vm.createContext(s);vm.runInContext(i.source,s);const out=s.renderGuideMarkdown(i.guide);assert(out.includes('<table>'));assert(out.includes('<pre><code>'));assert(out.includes('<h3>H-CRAN</h3>'));assert.equal(s.renderGuideMarkdown('<script>alert(1)</script>').includes('<script>'),false);"
        r=subprocess.run(['node','-e',code],input=json.dumps(dict(source=source,guide=GUIDE.read_text())),capture_output=True,text=True,timeout=15)
        self.assertEqual(r.returncode,0,r.stderr)

    def test_documentation_route_and_controls(self):
        import test_pending_adoption as adoption
        client=adoption.backend.app.test_client()
        response=client.get('/api/docs/dashboard-and-ran-architecture-guide.md')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.data,GUIDE.read_bytes())
        response.close()
        self.assertEqual(client.get('/api/docs/../config/global.env').status_code,400)
        source=HTML[HTML.index('async function refreshGuide('):HTML.index('function docShow(i)')]
        self.assertIn("panelCanvas('docs'",source)
        self.assertIn("Workspace.refreshPanel('docs')",source)
        self.assertIn("responseType:'text'",source)
        self.assertNotIn('setInterval',source)
        self.assertNotIn('location.reload',source)

    def test_guide_scope_and_controls(self):
        text=GUIDE.read_text()
        for heading in ['C-RAN / Centralized RAN','Cloud RAN','vC-RAN','O-RAN','H-CRAN','F-RAN / Fog RAN']:
            self.assertIn('### '+heading,text)
        for control in ['5G Core','UE / PDU Session','Slice/NW','Istio','Verify Clean','Rancher','Prometheus','Layer 2','Layer 3','RAN Scenario','Documentation and window controls']:
            self.assertIn('### '+control,text)
        self.assertIn('E2 pending',text)
        self.assertIn('Disabled / Future',text)
        self.assertIn('not establish UE/PDU validation for every row',text)
