'use strict';
let broadcastProject=null,broadcastState=null;
async function refreshBroadcast(){
 const target=project;
 try{
  const b=await api('/api/broadcast/'+target);if(target!==project)return;
  broadcastState=b;
  if(broadcastProject!==target){broadcastProject=target;$('broadcastEndpoint').value='';$('broadcastPrivate').checked=!b.external;for(const [id,key] of [['broadcastFrom','start_at'],['broadcastUntil','stop_at']]){const d=b[key]?new Date(b[key]*1000):null;$(id).value=d?new Date(d-d.getTimezoneOffset()*60000).toISOString().slice(0,16):'';}}
  const select=$('broadcastAsset'),old=[...select.selectedOptions].map(o=>o.value);
  const programs=state.assets.filter(a=>a.kind==='video'&&state.jobs.some(j=>j.id===a.job&&j.operation==='prepare_broadcast'&&j.status==='succeeded'));
  select.innerHTML='<option value="">Choose a finished program</option>'+programs.map(a=>`<option value="${a.id}">${esc(a.name)}</option>`).join('');
  const chosen=old.some(id=>programs.some(a=>a.id===id))?old:b.asset_ids||[];for(const option of select.options)option.selected=chosen.includes(option.value);
  const active=b.desired==='running';
  $('broadcastState').textContent=b.status+(b.external?' · External destination':' · Private preview');
  $('broadcastStart').textContent=b.external?'Start channel broadcast':'Start private preview';
  $('broadcastStart').disabled=active||b.status==='unconfigured';$('broadcastStop').disabled=!active;
  for(const el of $('broadcastForm').elements)el.disabled=active||b.status==='recovering';
  $('broadcastPreview').hidden=b.status!=='running';$('broadcastPreview').href=b.preview_url||'#';
  $('broadcastProgram').hidden=!b.program_url;$('broadcastProgram').href=b.program_url||'#';
  if(b.error)$('broadcastNotice').textContent=b.error;
 }catch(e){$('broadcastState').textContent='Connection unavailable';$('broadcastNotice').textContent=e.message;}
}
$('broadcastForm').onsubmit=async e=>{
 e.preventDefault();const button=e.submitter;button.disabled=true;
 const selected=[...$('broadcastAsset').selectedOptions].map(o=>o.value).filter(Boolean);const data={asset_id:selected[0],asset_ids:selected,start_at:$('broadcastFrom').value?new Date($('broadcastFrom').value).getTime()/1000:null,stop_at:$('broadcastUntil').value?new Date($('broadcastUntil').value).getTime()/1000:null};
 if($('broadcastPrivate').checked)data.endpoint='';else if($('broadcastEndpoint').value.trim())data.endpoint=$('broadcastEndpoint').value.trim();
 try{await post('/api/broadcast/'+project+'/configure',data);$('broadcastEndpoint').value='';$('broadcastNotice').textContent='Saved. Start when ready.';await refreshBroadcast();}catch(e){$('broadcastNotice').textContent=e.message;}finally{button.disabled=false;}
};
for(const [id,action] of [['broadcastStart','start'],['broadcastStop','stop']])$(id).onclick=async()=>{
 $(id).disabled=true;try{await post('/api/broadcast/'+project+'/'+action);$('broadcastNotice').textContent=action==='start'?'Starting saved program…':'Stopping…';await refreshBroadcast();}catch(e){$('broadcastNotice').textContent=e.message;$(id).disabled=false;}
};
refreshBroadcast();
$('broadcastEndpoint').oninput=()=>{if($('broadcastEndpoint').value.trim())$('broadcastPrivate').checked=false;};
