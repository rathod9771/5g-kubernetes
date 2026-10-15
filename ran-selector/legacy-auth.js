'use strict';
(() => {
  let token='';
  const original=window.fetch.bind(window);
  window.fetch=(input,options={})=>{
    const url=new URL(typeof input==='string'?input:input.url,window.location.href);
    if(token && url.origin===window.location.origin && url.pathname.startsWith('/api/')) {
      const headers=new Headers(options.headers || (typeof input==='string'?undefined:input.headers));
      if(!headers.has('Authorization'))headers.set('Authorization','Bearer '+token);
      options={...options,headers};
    }
    return original(input,options);
  };
  document.getElementById('legacy-connect').onclick=()=>{
    token=document.getElementById('legacy-credential').value;
    document.getElementById('legacy-credential').value='';
    document.getElementById('legacy-auth-state').textContent=token?' Token held in page memory; lifecycle switching remains disabled.':' Token cleared.';
  };
  window.addEventListener('pagehide',()=>{token='';});
})();
