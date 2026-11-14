/* DOM text only: incident payloads never enter HTML templates. Tokens stay in request memory. */
(function(){
  function element(tag,text){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;}
  function section(body,title){const n=element('section');n.style.cssText='padding:12px;border-bottom:1px solid #334155';n.appendChild(element('h3',title));body.appendChild(n);return n;}
  function line(parent,text){parent.appendChild(element('p',text));}
  async function command(payload){
    const token=window.prompt('Operator bearer token (used for this request only)');if(!token)return;
    const r=await fetch('/api/assurance/operator',{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+token},body:JSON.stringify(payload)});
    const d=await r.json();if(!r.ok)throw new Error(d.error||'Operator request denied');await window.refreshLayer3Panel();
  }
  function button(parent,label,payload){const b=element('button',label);b.className='btn';b.onclick=()=>command(payload).catch(e=>window.alert(e.message));parent.appendChild(b);}
  window.renderAssurance=function(body,d){
    body.innerHTML="";const telemetry=d.telemetry||{};
    let s=section(body,'Service health');line(s,'Platform confidence: '+d.monitoring_confidence+' · Monitoring pipeline: '+(d.monitoring_status||'UNAVAILABLE')+' · Active scenario: '+(telemetry.active_scenario||'UNAVAILABLE'));
    for(const role of ['core','cu','du','fog','ran_protocol','ue_registration','pdu_sessions','user_plane']){
      const h=telemetry.services?.[role];line(s,role.toUpperCase()+': '+(h?.status||'UNAVAILABLE')+' ['+(d.monitoring_confidence==='STALE'?'STALE':h?.provenance||'UNAVAILABLE')+']');
    }
    s=section(body,'Active alarms');
    for(const severity of ['CRITICAL','MAJOR','MINOR','WARNING','INFO'])line(s,severity+': '+d.alarm.filter(a=>a.severity===severity&&a.state!=='CLEARED').length);
    line(s,'Acknowledged: '+d.alarm.filter(a=>a.acknowledged).length+' · Cleared: '+d.alarm.filter(a=>a.state==='CLEARED').length);
    if(!d.alarm.length)line(s,'No collected alarms. Network health is unknown without fresh evidence.');
    for(const a of d.alarm.slice(-50)){const row=element('div');line(row,a.severity+' · '+a.alarm_type+' · '+a.resource+' · '+a.state+' ['+(a.stale?'STALE':a.provenance)+']');line(row,'Cause hypothesis: '+a.probable_cause+' · '+a.service_impact+(a.inhibited_by?' · inhibited by '+a.inhibited_by:''));if(a.state!=='CLEARED'&&d.approval_enabled)button(row,'Acknowledge',{command:'acknowledge',key:a.key});s.appendChild(row);}
    s=section(body,'Incident center');
    if(!d.incident.length)line(s,'No incidents recorded.');
    for(const i of d.incident.slice(-30)){line(s,i.incident_id+' · '+i.state+' · '+i.severity+' ['+i.provenance+']');line(s,i.root_cause_hypothesis+' · Resources: '+i.resource_ids.join(', '));line(s,i.timeline.map(t=>t.state+' '+new Date(t.at*1000).toLocaleString()).join(' → '));}
    s=section(body,'Automation control');line(s,d.governance.mode+' · LIVE_AUTO disabled · Executor '+d.production_executor+' · Emergency stop: '+d.governance.emergency_stop);
    line(s,'Lifecycle lock: '+(d.lifecycle_lock?.provenance==='LIVE'?(d.lifecycle_lock.locked?'HELD':'No current holder observed'):'UNAVAILABLE')+' · No live actions registered.');
    for(const m of d.maintenance||[])line(s,'Maintenance: '+m.service+' · '+new Date(m.start*1000).toLocaleString()+' to '+new Date(m.end*1000).toLocaleString());
    if(d.approval_enabled){for(const mode of ['OBSERVE_ONLY','SIMULATION','APPROVAL_REQUIRED'])button(s,mode,{command:'control',mode});button(s,'Emergency stop',{command:'control',emergency_stop:true});button(s,'Clear emergency stop (execution disabled)',{command:'control',emergency_stop:false});const maintenance=element('button','Maintenance window');maintenance.onclick=()=>{const service=window.prompt('Exact service/NS identity');if(!service)return;const start=Date.now()/1000;command({command:'maintenance',service,start,end:start+1800}).catch(e=>window.alert(e.message));};s.appendChild(maintenance);}
    s=section(body,'Remediation history and pending approvals');
    for(const a of d.action.slice(-30)){line(s,a.action+' · Class '+a.class+' · '+a.status+' · '+a.execution_status+' ['+a.provenance+']');line(s,'Verify: '+a.verification.join(', ')+' · Cooldown: '+a.cooldown+'s · '+a.rollback);if(a.status==='PROPOSED'&&a.class==='C'&&d.approval_enabled){button(s,'Record approval (execution disabled)',{command:'decision',action_id:a.action_id,decision:'APPROVED'});button(s,'Cancel proposal',{command:'decision',action_id:a.action_id,decision:'CANCELLED'});}}
    s=section(body,'Service KPIs');const pressure=telemetry.cpu_pressure;line(s,'CPU PSI some (5m): '+(pressure?.provenance==='LIVE'&&d.monitoring_confidence!=='STALE'?pressure.value.map(v=>(v.value*100).toFixed(2)+'%').join(', ')+' [LIVE]':d.monitoring_confidence==='STALE'?'STALE':pressure?.provenance||'UNAVAILABLE'));line(s,'F1-C / NGAP transport, protocol setup, UE registration, PDU sessions and measured user plane are separate evidence states.');line(s,'RAN latency / Fog latency: UNAVAILABLE until validated measurements exist.');
    s=section(body,'Platform health');for(const t of telemetry.prometheus_targets?.value||[])if(t.health!=='up')line(s,'Scrape unavailable: '+t.labels.job+' · '+t.failure_class+' ['+(d.monitoring_confidence==='STALE'?'STALE':'LIVE')+']');for(const key of ['kubernetes','prometheus','alertmanager','rancher'])line(s,key+': '+(d.monitoring_confidence==='STALE'?'STALE':telemetry[key]?.provenance||'UNAVAILABLE'));for(const [key,value] of Object.entries(telemetry.osm||{}))line(s,'OSM '+key+': '+(value.value?.nsState||'UNAVAILABLE')+' ['+(d.monitoring_confidence==='STALE'?'STALE':value.provenance)+']');
    s=section(body,'Emergency protection');line(s,'Protected NS: '+d.protected_ns.join(', '));line(s,'3GPP emergency procedures: '+d.emergency_3gpp);line(s,'Unknown protection state blocks disruptive changes. Synthetic policy events represent offline safeguards.');
    s=section(body,'Offline simulation');line(s,'16 sandbox scenarios. Synthetic results never enter this production journal.');
    if(d.approval_enabled){const select=element('select');for(const name of d.simulations){const o=element('option',name);o.value=name;select.appendChild(o);}s.appendChild(select);const b=element('button','Run sandbox scenario');b.onclick=async()=>{const token=window.prompt('Operator token');if(!token)return;try{const r=await fetch('/api/assurance/simulate',{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+token},body:JSON.stringify({scenario:select.value})});const result=await r.json();if(!r.ok)throw new Error(result.error);window.alert('SIMULATED: '+result.scenario+' · '+result.incident_state+' · production calls: '+result.production_calls);}catch(e){window.alert(e.message);}};s.appendChild(b);}
    s=section(body,'Operational audit');for(const e of d.audit.slice(0,20))line(s,new Date(e.at*1000).toLocaleString()+' · '+e.kind+' · '+e.object_id);
  };
})();
