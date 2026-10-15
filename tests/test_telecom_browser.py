"""Execute actual new dashboard JavaScript with mocked browser/network objects."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[1]

class TelecomBrowserTests(unittest.TestCase):
    def node(self,name,harness):
        if not shutil.which('node'):self.skipTest('Node required')
        result=subprocess.run(['node','-e',harness],input=(ROOT/'ran-selector'/name).read_text(),text=True,capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_six_views_auth_submission_and_forget(self):
        self.node('telecom.js',r'''
const vm=require('vm'),assert=require('assert'),source=require('fs').readFileSync(0,'utf8');
(async()=>{
 const elements={},requests=[],events={};
 const get=id=>elements[id]||(elements[id]={value:'',textContent:'',children:[],appendChild(v){this.children.push(v);}});
 const sandbox={document:{getElementById:get,createElement:()=>({})},window:{addEventListener:(k,v)=>events[k]=v},
   AbortController,JSON,setTimeout,clearTimeout,
   fetch:async(path,opts)=>{requests.push({path,opts});return {ok:true,status:200,json:async()=>({items:[{name:'<img onerror=evil>'}]})};}};
 vm.runInNewContext(source,sandbox);
 assert.equal(get('views').children.length,11);
 get('credential').value='offline-browser-only-'+ 'x'.repeat(40);
 await get('connect').onclick();await new Promise(setImmediate);
 assert.equal(requests.length,1);assert(requests[0].opts.headers.Authorization.startsWith('Bearer '));
 assert.equal(get('credential').value,'');assert(get('result').textContent.includes('<img onerror=evil>'));
 assert(!get('result').innerHTML);
 get('views').children[2].onclick();await new Promise(setImmediate);
 assert.equal(requests[1].path,'/api/telecom/releases');
 get('operation').value='workloads';get('payload').value='{"event_id":"test"}';
 get('submit').onclick();await new Promise(setImmediate);assert.equal(requests[2].opts.method,'POST');
 get('forget').onclick();get('connect').onclick();await new Promise(setImmediate);
 assert.equal(requests.length,3,'cleared token must prevent requests');
 events.pagehide();
})().catch(e=>{console.error(e);process.exit(1);});
''')

    def test_legacy_token_is_not_forwarded_cross_origin(self):
        self.node('legacy-auth.js',r'''
const vm=require('vm'),assert=require('assert'),source=require('fs').readFileSync(0,'utf8');
const elements={},requests=[];const get=id=>elements[id]||(elements[id]={value:'',textContent:''});
const window={location:{href:'https://dashboard.example/',origin:'https://dashboard.example'},addEventListener(){},
 fetch:(input,opts)=>{requests.push(opts);return Promise.resolve({});}};
vm.runInNewContext(source,{window,document:{getElementById:get},URL,Headers});
get('legacy-credential').value='offline-browser-only-'+ 'x'.repeat(40);get('legacy-connect').onclick();
window.fetch('/api/pods');window.fetch('https://other.example/api/pods');
assert(requests[0].headers.get('Authorization').startsWith('Bearer '));assert(!requests[1].headers);
get('legacy-credential').value='';get('legacy-connect').onclick();window.fetch('/api/pods');assert(!requests[2].headers);
''')
