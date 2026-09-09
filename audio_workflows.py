"""Bounded audio recipes and CPU program assembly; no downloaded sound assets."""
import math
from pathlib import Path
import subprocess
import basic

PRESETS = {
 'workout': {'bpm':130,'prompt':'Energetic instrumental workout electronic dance music, steady driving kick, punchy bass, uplifting synth hooks, consistent high energy, no vocals'},
 'techno': {'bpm':138,'prompt':'Instrumental techno, four-on-the-floor kick, rolling bass, evolving analog synth sequences, crisp hi-hats, hypnotic club groove, no vocals'},
 'classical': {'bpm':90,'prompt':'Instrumental classical chamber music, expressive piano and warm strings, flowing melodic development, natural dynamics, no vocals'},
 'ambient': {'bpm':70,'prompt':'Soothing instrumental ambient music, warm sustained pads, gentle sparse piano, slow evolution, soft dynamics, no percussion, no vocals'},
}
OPERATIONS = {
 'generate_soundscape':'Make synthetic rain, ocean or wind, or loop and mix supplied sound recordings; preset, seconds 10–3600, optional sources and seed.',
 'arrange_audio':'Crossfade ordered sources into an audio set; optional seconds 10–3600 repeats the set, crossfade 0–10 seconds.',
 'prepare_broadcast':'Build a program: scene media (720p) or ocean (1080p/4k fixed image, animated water, optional tracks, otherwise synthetic surf). Ocean seconds: 16–256 in multiples of 16; default 64.'}

def number(value,low,high):
    try:n=float(value)
    except (TypeError,ValueError):raise ValueError('Expected a number')
    if not math.isfinite(n) or not low<=n<=high:raise ValueError(f'Value must be {low}–{high}')
    return n

def music_args(args):
    a=dict(args)
    preset=a.get('preset')
    if preset is not None and preset not in PRESETS:raise ValueError('Unknown music preset')
    if preset:
        a.setdefault('prompt',PRESETS[preset]['prompt']);a.setdefault('bpm',PRESETS[preset]['bpm'])
    if 'bpm' in a:a['bpm']=int(number(a['bpm'],30,250))
    if 'key' in a and a['key'] not in [n+' '+mode for n in ('C','C#','D','D#','E','F','F#','G','G#','A','A#','B') for mode in ('major','minor')]:raise ValueError('Use a key such as C major or A minor')
    return a

def validate(op,args,deferred=False):
    allowed={'preset','sources','seconds','seed'} if op=='generate_soundscape' else {'sources','seconds','crossfade'}|({'source','scene','resolution','horizon','birds'} if op=='prepare_broadcast' else set())
    if set(args)-allowed:raise ValueError('Unexpected audio parameters')
    a=dict(args)
    if op=='prepare_broadcast':
        a.setdefault('scene','media')
        if a['scene'] not in ('media','ocean'):raise ValueError('Choose media or ocean scene')
        if a['scene']=='ocean':
            a.setdefault('seconds',64);a.setdefault('resolution','1080p');a.setdefault('horizon',0.52);a.setdefault('birds',True)
            if a['resolution'] not in ('1080p','4k'):raise ValueError('Ocean resolution must be 1080p or 4k')
            n=number(a['seconds'],16,256)
            if n%16:raise ValueError('Ocean length must be a multiple of 16 seconds (16–256)')
            a['horizon']=number(a['horizon'],0.1,0.8)
            if not isinstance(a['birds'],bool):raise ValueError('Birds must be true or false')
        elif any(k in a for k in ('resolution','horizon','birds')):raise ValueError('Resolution, horizon and birds apply to ocean mode')
    def path(x):return x if deferred and isinstance(x,str) and x.startswith('job:') else basic.source_path(x)
    if 'sources' in a:
        if not isinstance(a['sources'],list) or not 1<=len(a['sources'])<=20:raise ValueError('Select 1–20 audio files')
        a['sources']=[path(x) for x in a['sources']]
    elif op!='generate_soundscape' and a.get('scene')!='ocean':raise ValueError('Audio sources are required')
    if 'source' in a:a['source']=path(a['source'])
    if op=='generate_soundscape':
        a.setdefault('preset','rain')
        if a['preset'] not in ('rain','ocean','wind'):raise ValueError('Choose rain, ocean or wind')
        a['seed']=int(number(a.get('seed',42),0,2**31-1))
    else:a['crossfade']=number(a.get('crossfade',3),0,10)
    if op!='arrange_audio' or 'seconds' in a:a['seconds']=number(a.get('seconds',60),10,3600)
    return a

def ff(*args):
    subprocess.run([basic.FFMPEG,'-v','error','-nostdin','-y','-threads','2','-filter_complex_threads','2',*map(str,args)],check=True,stdin=subprocess.DEVNULL)

def arrange(a,folder):
    clips=[];durations=[]
    for i,source in enumerate(a['sources']):
        meta=basic.probe(source)
        if not any(s['codec_type']=='audio' for s in meta['streams']):raise ValueError('Every selected track must contain audio')
        duration=float(meta['format'].get('duration',0))
        if not 0<duration<=3600:raise ValueError('Each audio track must be at most one hour')
        durations.append(duration)
        clip=folder/f'audio-{i}.wav'
        ff('-i',source,'-map','0:a:0','-vn','-ar','48000','-ac','2','-c:a','pcm_s16le',clip);clips.append(clip)
    if sum(durations)>7200:raise ValueError('Source set exceeds two hours')
    fade=a.get('crossfade',3)
    if len(clips)>1 and fade and min(durations)<=2*fade:raise ValueError('Tracks must be longer than twice the crossfade; reduce crossfade')
    inputs=[item for clip in clips for item in ('-i',clip)]
    out=folder/'set.wav'
    if len(clips)==1:out=clips[0]
    elif fade:
        filters=[];left='0:a'
        for i in range(1,len(clips)):
            right=f'm{i}';filters.append(f'[{left}][{i}:a]acrossfade=d={fade}:c1=tri:c2=tri[{right}]');left=right
        ff(*inputs,'-filter_complex',';'.join(filters),'-map',f'[{left}]',out)
    else:
        ff(*inputs,'-filter_complex',''.join(f'[{i}:a]' for i in range(len(clips)))+f'concat=n={len(clips)}:v=0:a=1[out]','-map','[out]',out)
    result=folder/'result.wav'
    duration=a.get('seconds',sum(durations)-fade*(len(clips)-1))
    if duration>3600:raise ValueError('Assembled set exceeds one hour; specify a shorter target duration')
    ff('-stream_loop','-1','-i',out,'-t',duration,'-af',f'loudnorm=I=-16:TP=-1.5:LRA=11,afade=t=in:d=0.1,afade=t=out:st={max(0,duration-0.25)}:d=0.25','-ar','48000','-ac','2',result)
    return result

def soundscape(a,folder):
    duration=a['seconds'];out=folder/'result.wav'
    if a.get('sources'):
        inputs=[v for src in a['sources'] for v in ('-stream_loop','-1','-i',src)]
        filt=''.join(f'[{i}:a]' for i in range(len(a['sources'])))+f'amix=inputs={len(a["sources"])}:normalize=1,'
        ff(*inputs,'-t',duration,'-filter_complex',filt+f'loudnorm=I=-20:TP=-2:LRA=7,afade=t=in:d=2,afade=t=out:st={duration-2}:d=2[out]','-map','[out]','-ar','48000','-ac','2',out)
    else:
        texture={'rain':'highpass=f=350,lowpass=f=10000,tremolo=f=0.7:d=0.15','ocean':'lowpass=f=2200,tremolo=f=0.1:d=0.85','wind':'lowpass=f=900,highpass=f=80,tremolo=f=0.15:d=0.65'}[a['preset']]
        ff('-f','lavfi','-i',f'anoisesrc=color=pink:amplitude=0.5:sample_rate=48000:seed={a["seed"]}', '-t',duration,'-af',texture+f',loudnorm=I=-20:TP=-2:LRA=7,afade=t=in:d=2,afade=t=out:st={duration-2}:d=2','-ar','48000','-ac','2',out)
    return out

def prepare(a,folder):
    if a.get('scene')=='ocean':
        import ocean_scene
        return ocean_scene.prepare(a,folder)
    audio=arrange(a,folder);duration=a['seconds'];out=folder/'result.mp4'
    if a.get('source'):
        meta=basic.probe(a['source']);video=[s for s in meta['streams'] if s['codec_type']=='video']
        if not video:raise ValueError('Visual source must be an image or video')
        is_image=Path(a['source']).suffix.lower() in ('.png','.jpg','.jpeg','.webp','.heic')
        inputs=['-loop','1','-i',a['source']] if is_image else ['-stream_loop','-1','-i',a['source']]
    else:inputs=['-f','lavfi','-i','color=c=0x17212b:s=1280x720:r=24']
    ff(*inputs,'-i',audio,'-t',duration,'-map','0:v:0','-map','1:a:0','-vf','scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1','-r','24','-c:v','h264_videotoolbox','-b:v','4000k','-g','48','-pix_fmt','yuv420p','-c:a','aac','-b:a','192k','-ar','48000','-ac','2','-movflags','+faststart',out)
    return out
