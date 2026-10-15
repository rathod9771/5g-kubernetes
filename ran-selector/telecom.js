'use strict';
(() => {
  const views = {'External Workloads':'workloads','Capability Registry':'capabilities','CI/CD Releases':'releases',
    'Interoperability Matrix':'interoperability','Failure Analysis':'failure-analysis','Qualification Reports':'qualification-reports','Pipeline Jobs':'pipeline-jobs','Publication Requests':'publication-requests','Registry Publications':'registry-publications','Initial Deployments':'deployments','Instance Updates':'instance-update-requests'};
  let selected='workloads', token='', busy=false;
  const output=document.getElementById('result');
  async function request(path, payload) {
    if (busy) return;
    busy=true;
    const controller=new AbortController(), timeout=setTimeout(()=>controller.abort(),15000);
    try {
      if (!token) throw new Error('Scoped authentication is required');
      const options={headers:{Authorization:'Bearer '+token},signal:controller.signal,cache:'no-store'};
      if(payload!==undefined){ options.method='POST'; options.headers['Content-Type']='application/json';options.body=JSON.stringify(payload); }
      const response=await fetch('/api/telecom/'+path,options), value=await response.json();
      output.textContent=JSON.stringify(value,null,2);
      if(!response.ok) output.textContent='Operation rejected ('+response.status+')\n'+output.textContent;
    } catch(error) { output.textContent=error.name==='AbortError'?'Request timed out; do not repeat uncertain operations automatically.':error.message; }
    finally { clearTimeout(timeout);busy=false; }
  }
  for (const [label,path] of Object.entries(views)) {
    const button=document.createElement('button');button.textContent=label;
    button.onclick=()=>{selected=path;document.getElementById('title').textContent=label;request(path);};
    document.getElementById('views').appendChild(button);
  }
  document.getElementById('connect').onclick=()=>{ token=document.getElementById('credential').value;document.getElementById('credential').value='';request(selected); };
  document.getElementById('forget').onclick=()=>{ token='';document.getElementById('credential').value='';output.textContent='Credentials and results cleared.'; };
  document.getElementById('submit').onclick=()=>{try{request(document.getElementById('operation').value,JSON.parse(document.getElementById('payload').value));}catch(_){output.textContent='Invalid JSON';}};
  window.addEventListener('pagehide',()=>{token='';output.textContent='';});
})();
