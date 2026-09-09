"""Ordered scene programs with bounded cuts/crossfades and a single final export."""
from pathlib import Path
import json,subprocess,shutil
import basic,scene_renderer,scene_catalog
DESCRIPTION='Sequence procedural scenes into one video: sequence ordered preset names (array or comma-separated), seconds per shot 2–30, crossfade 0–2, resolution 720p/1080p, optional audio, palette and seed.'
def validate(args,deferred=False):
    allowed={'sequence','seconds','crossfade','resolution','audio','palette','seed'}
    if set(args)-allowed:raise ValueError('Unknown program controls')
    a={'seconds':8,'crossfade':1,'resolution':'720p','palette':'aqua','seed':42,**args}
    sequence=a.get('sequence',[])
    if isinstance(sequence,str):sequence=[s.strip() for s in sequence.split(',')]
    if not isinstance(sequence,list) or not 1<=len(sequence)<=12 or any(s not in scene_catalog.PRESETS for s in sequence):raise ValueError('Choose 1–12 known scenes in order')
    a['sequence']=sequence
    template=scene_renderer.validate({k:v for k,v in a.items() if k not in ('sequence','crossfade')}|{'renderer':'godot','scene':sequence[0]},deferred)
    a.update({k:template[k] for k in ('seconds','resolution','palette','seed')})
    if 'audio' in template:a['audio']=template['audio']
    import math
    fade=float(a['crossfade'])
    if not math.isfinite(fade) or not 0<=fade<=2 or a['seconds']<2 or a['seconds']<=2*fade:raise ValueError('Shots must be 2–30 seconds and longer than twice the 0–2 second crossfade')
    if len(sequence)*a['seconds']>300:raise ValueError('Program exceeds five minutes')
    if 'music_visualizer' in sequence and not a.get('audio'):raise ValueError('Music visualizer requires audio')
    a['crossfade']=fade
    return a

def render(args,folder):
    a=validate(args);folder=Path(folder); clips=[];clock=0.
    for i,scene in enumerate(a['sequence']):
        sub=folder/f'shot-{i}';sub.mkdir(exist_ok=True)
        controls={k:a[k] for k in ('seconds','resolution','palette','seed','audio') if k in a}
        clips.append(scene_renderer.render({**controls,'scene':scene,'renderer':'godot','start_time':clock},sub))
        clock+=a['seconds']-a['crossfade']
    out=folder/'result.mp4';partial=folder/'program.partial.mp4'
    inputs=[x for c in clips for x in ('-i',str(c))]
    filters=[];v='0:v';audio='0:a'
    for i in range(1,len(clips)):
        if a['crossfade']:
            filters += [f'[{v}][{i}:v]xfade=transition=fade:duration={a["crossfade"]}:offset={i*(a["seconds"]-a["crossfade"])}[v{i}]',f'[{audio}][{i}:a]acrossfade=d={a["crossfade"]}:c1=tri:c2=tri[a{i}]']
        else:filters += [f'[{v}][{audio}][{i}:v][{i}:a]concat=n=2:v=1:a=1[v{i}][a{i}]']
        v=f'v{i}';audio=f'a{i}'
    command=[basic.FFMPEG,'-hide_banner','-loglevel','error','-nostdin','-y','-filter_complex_threads','2',*inputs]
    if filters:command+=['-filter_complex',';'.join(filters),'-map',f'[{v}]','-map',f'[{audio}]']
    command+=['-c:v','libx264','-threads','4','-preset','fast','-crf','20','-pix_fmt','yuv420p','-r','24','-g','48','-c:a','aac','-ar','48000','-ac','2','-movflags','+faststart',str(partial)]
    subprocess.run(command,check=True);partial.replace(out)
    info={'version':1,'recipe':a,'duration':len(clips)*a['seconds']-(len(clips)-1)*a['crossfade'],'shots':[json.loads((c.parent/'scene.json').read_text()) for c in clips]}
    (folder/'scene.json').write_text(json.dumps(info,indent=2));cleanup(folder)
    return out

def cleanup(folder):
    folder=Path(folder)
    for i in range(12):shutil.rmtree(folder/f'shot-{i}',ignore_errors=True)
    (folder/'program.partial.mp4').unlink(missing_ok=True)
