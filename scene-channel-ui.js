'use strict';
let loadedSceneChannel=null;
async function refreshSceneChannel(){
 const target=project;
 try{
  const s=await api('/api/projects/'+target+'/scene-channel');if(target!==project)return;
  $('sceneChannelStatus').textContent=`${s.status} · ${s.buffer_seconds||0}s ready · ${s.generated||0} scenes generated · ${s.underruns||0} filler uses${s.error?' · '+s.error:''}`;
  $('sceneChannelStart').disabled=s.desired==='running'||s.status==='unconfigured';
  $('sceneChannelStop').disabled=s.desired!=='running';
  $('sceneChannelPreview').hidden=!['running','filler'].includes(s.status);$('sceneChannelPreview').href=s.preview_url;
  if(s.recipe&&loadedSceneChannel!==target+':'+s.generation){$('liveScene').value=s.recipe.scene;$('liveResolution').value=s.recipe.resolution;loadedSceneChannel=target+':'+s.generation}
  const selected=$('liveAudio').value||state.assets.find(a=>a.path===s.recipe?.audio)?.id||'',options=state.assets.filter(a=>a.kind==='audio');
  const key=options.map(a=>a.id).join();if($('liveAudio').dataset.options!==key){$('liveAudio').innerHTML='<option value="">Scene default</option>'+options.map(a=>`<option value="${a.id}">${esc(a.name)}</option>`).join('');$('liveAudio').value=selected;$('liveAudio').dataset.options=key}
 }catch(e){$('sceneChannelStatus').textContent=e.message}
}
$('sceneChannelForm').onsubmit=async e=>{e.preventDefault();e.submitter.disabled=true;try{
 const a={scene:$('liveScene').value,renderer:'godot',resolution:$('liveResolution').value,seconds:16};
 if($('liveAudio').value)a.audio=$('liveAudio').value;
 await post('/api/projects/'+project+'/scene-channel',a);await refreshSceneChannel();
}catch(e){$('sceneChannelStatus').textContent=e.message}finally{e.submitter.disabled=false}};
for(const [id,action]of [['sceneChannelStart','start'],['sceneChannelStop','stop']])$(id).onclick=async()=>{try{await post('/api/projects/'+project+'/scene-channel/'+action);await refreshSceneChannel()}catch(e){$('sceneChannelStatus').textContent=e.message}};
refreshSceneChannel();setInterval(refreshSceneChannel,2500);
