"""Bounded Blender virtual-set adapter, admitted through engine's GPU lane."""
import json, math, subprocess, os, shutil, hashlib
from pathlib import Path
import basic
import scene_catalog

DESCRIPTION='Render scene ocean/forest/rain_window/abstract/space/music_visualizer/studio with common camera, palette, intensity and optional audio; renderer godot or blender (legacy default); seconds 1–30, resolution 720p/1080p/4k export, wave_height, cloud_speed, start_time and seed. Offline rendering, not seamless live streaming.'

def validate(args,deferred=False):
    allowed={'scene','palette','camera','intensity','time_of_day','audio','renderer','seconds','resolution','wave_height','cloud_speed','start_time','seed'}
    if set(args)-allowed:raise ValueError('Unknown scene parameters')
    a={'scene':'ocean','palette':'aqua','camera':'fixed','intensity':1.,'time_of_day':12.,'renderer':('godot' if args.get('scene','ocean')!='ocean' else 'blender'),'seconds':4,'resolution':'720p','wave_height':1.4,'cloud_speed':1.5,'start_time':0,'seed':42,**args}
    if a['scene'] not in scene_catalog.PRESETS:raise ValueError('Unknown scene preset')
    if a['palette'] not in scene_catalog.PALETTES or a['camera'] not in scene_catalog.CAMERAS:raise ValueError('Unknown palette or camera')
    if a['renderer'] not in scene_catalog.PRESETS[a['scene']]['renderers']:raise ValueError('This preset requires Godot')
    if a['renderer']=='blender' and any(k in args and args[k]!=v for k,v in [('camera','fixed'),('palette','aqua'),('intensity',1.),('time_of_day',12.)]):raise ValueError('These controls require Godot')
    if a['scene']=='music_visualizer' and not a.get('audio'):raise ValueError('Music-reactive scenes require an audio source')
    if 'audio' in a and not (deferred and str(a['audio']).startswith('job:')):a['audio']=basic.source_path(a['audio'])
    if a['renderer'] not in ('godot','blender'):raise ValueError('Renderer must be godot or blender')
    if a['resolution'] not in ('720p','1080p','4k'):raise ValueError('Scene resolution must be 720p, 1080p or 4k')
    for k,low,high in [('intensity',0,2),('time_of_day',0,24),('seconds',1,30),('wave_height',.1,3),('cloud_speed',0,5),('start_time',0,1000000000),('seed',0,2147483647)]:
        if isinstance(a[k],bool):raise ValueError(f'{k} must be a number')
        try:v=float(a[k])
        except (ValueError,TypeError):raise ValueError(f'{k} must be a number')
        if not math.isfinite(v) or not low<=v<=high:raise ValueError(f'{k} must be {low}–{high}')
        if k in ('seconds','seed') and v!=int(v):raise ValueError(f'{k} must be an integer')
        a[k]=int(v) if k in ('seconds','seed') else v
    return a

def render(args,folder):
    a=validate(args); folder=Path(folder)
    blender=Path('/Applications/Blender.app/Contents/MacOS/Blender')
    if a['renderer']=='blender' and not blender.is_file():raise RuntimeError('Blender is not installed on Studio')
    w,h={'720p':(1280,720),'1080p':(1920,1080),'4k':(3840,2160)}[a['resolution']]
    audio,envelope=prepare_audio(a,folder)
    request=folder/'scene-request.json'
    request.write_text(json.dumps({'args':{**a,'width':w,'height':h,'envelope':envelope,'colors':scene_catalog.PALETTES[a['palette']]},'folder':str(folder),'ffmpeg':basic.FFMPEG}))
    # Fixed trusted script; prompts cannot become executable Python or file paths.
    if a['renderer']=='blender':
        subprocess.run([str(blender),'-b','--python',str(Path(__file__).parent/'scenes/ocean_blender.py'),'--',str(request)],check=True)
    else:
        godot=Path('/opt/hollywood/tools/godot-4.7.2/Godot.app/Contents/MacOS/Godot')
        if not godot.is_file():raise RuntimeError('Godot 4.7.2 is not installed')
        source_project=Path(__file__).parent/'scenes/godot_sky'
        project=folder/'godot-project'; project.mkdir(exist_ok=True)
        for source in source_project.iterdir():
            if source.name not in ('project.godot','.godot'):
                dest=project/source.name
                if not dest.exists():dest.symlink_to(source.resolve())
        settings=(source_project/'project.godot').read_text().replace('window/stretch/mode="canvas_items"','window/stretch/mode="viewport"')
        settings=settings.replace('[display]',f'[display]\nwindow/size/viewport_width={w}\nwindow/size/viewport_height={h}\nwindow/size/window_width_override=960\nwindow/size/window_height_override=540')
        (project/'project.godot').write_text(settings)
        subprocess.run([str(godot),'--headless','--path',str(project),'--editor','--import'],check=True)
        movie=folder/'scene.avi'
        subprocess.run([str(godot),'--path',str(project),'--script','studio_stage.gd','--rendering-driver','metal','--resolution','960x540','--write-movie',str(movie),'--fixed-fps','24','--quit-after',str(a['seconds']*24+5)],env=dict(os.environ,STUDIO_SCENE_REQUEST=str(request)),check=True)
        subprocess.run([basic.FFMPEG,'-hide_banner','-loglevel','error','-nostdin','-y','-i',str(movie),'-an','-c:v','h264_videotoolbox','-bf','0','-b:v',('24M' if a['resolution']=='4k' else '8M'),'-pix_fmt','yuv420p',str(folder/'picture.mp4')],check=True)
        movie.unlink()

    video=next(x for x in basic.probe(str(folder/'picture.mp4'))['streams'] if x['codec_type']=='video')
    if (video['width'],video['height'])!=(w,h) or int(video.get('nb_frames',0))!=a['seconds']*24:
        raise RuntimeError('Scene renderer did not produce the requested resolution and frame count')
    out=folder/'result.mp4'; partial=folder/'result.partial.mp4'
    subprocess.run([basic.FFMPEG,'-hide_banner','-loglevel','error','-nostdin','-y','-i',str(folder/'picture.mp4'),'-i',str(audio),'-c:v','copy','-c:a','aac','-b:a','192k','-shortest','-movflags','+faststart',str(partial)],check=True)
    partial.replace(out)
    for path in (folder/'picture.mp4',audio,folder/'frame.png'):path.unlink(missing_ok=True)
    recipe={'engine':a['renderer'],'scene':a['scene'],'version':scene_catalog.VERSION,'source_sha256':hashlib.sha256((Path(__file__).parent/('scenes/godot_sky/studio_stage.gd' if a['renderer']=='godot' else 'scenes/ocean_blender.py')).read_bytes()).hexdigest(),'parameters':a,'audio':a.get('audio','synthetic surf' if a['scene']=='ocean' else 'silence'),'loop_seamless':False,'source':'procedural geometry; no image upscale','render':json.loads((folder/'render.json').read_text())}
    (folder/'scene.json').write_text(json.dumps(recipe,indent=2))
    cleanup(folder)
    return out

def cleanup(folder):
    folder=Path(folder)
    for name in ('scene.avi','picture.mp4','frame.png','scene-surf.wav','result.partial.mp4'):
        (folder/name).unlink(missing_ok=True)
    shutil.rmtree(folder/'godot-project',ignore_errors=True)


def prepare_audio(a,folder):
    import numpy as np
    from ocean_scene import surf,write_wav
    audio=Path(folder)/'scene-surf.wav'
    if a.get('audio'):
        duration=float(basic.probe(a['audio'])['format'].get('duration',0))
        if duration<=0:raise ValueError('Audio must have a finite duration')
        subprocess.run([basic.FFMPEG,'-hide_banner','-loglevel','error','-nostdin','-y','-stream_loop','-1','-ss',str(a['start_time']%duration),'-i',a['audio'],'-t',str(a['seconds']),'-vn','-ar','48000','-ac','2',str(audio)],check=True)
        raw=subprocess.check_output([basic.FFMPEG,'-v','error','-i',str(audio),'-ar','12000','-ac','1','-f','f32le','-'])
        x=np.frombuffer(raw,dtype='<f4'); envelopes=[]
        for i in range(a['seconds']*24):
            block=x[i*500:(i+1)*500]
            if len(block)<500:block=np.pad(block,(0,500-len(block)))
            spectrum=np.abs(np.fft.rfft(block*np.hanning(500)))/250
            envelopes.append([float(np.sqrt(np.mean(block**2))),*[float(np.sqrt(np.mean(spectrum[lo:hi]**2))) for lo,hi in [(1,9),(9,84),(84,251)]]])
        values=np.array(envelopes)
        # Fixed gain preserves silence and comparable response across chunk boundaries.
        values=np.clip(values*np.array([4.,15.,30.,45.]),0,1)
        return audio,values.tolist()
    data=surf(a['seconds'],a['seed']) if a['scene']=='ocean' else np.zeros((a['seconds']*48000,2),np.float32)
    write_wav(audio,data)
    return audio,[]
