"""Compact fixed-view ocean programs using Metal, hardware encoding and cyclic audio.

The source is a photograph/AI still; the water is a stylized animated displacement,
not a fluid simulation. Output resolution is separate from source-image detail.
"""
import fcntl
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time
import wave
import basic

HERE=Path(__file__).resolve().parent

def renderer():
    sources=[HERE/'ocean'/n for n in ('Renderer.swift','ocean.metal')]
    digest=hashlib.sha256(b''.join(p.read_bytes() for p in sources)).hexdigest()[:16]
    root=basic.STATE/'renderers'/digest;root.mkdir(parents=True,exist_ok=True)
    binary=root/'ocean-renderer'
    with open(root/'build.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if not binary.exists():
            subprocess.run(['/usr/bin/xcrun','swiftc','-O',str(sources[0]),'-o',str(binary)+'.partial'],check=True)
            shutil.copyfile(sources[1],root/'ocean.metal')
            Path(str(binary)+'.partial').replace(binary)
    return binary

def surf(seconds,seed=42):
    import numpy as np
    rate=48000;n=round(seconds*rate);rng=np.random.default_rng(seed)
    frequencies=np.fft.rfftfreq(n,1/rate)
    # Periodic filtered noise has no artificial silent lead-in or end fade.
    response=(np.maximum(frequencies,70)/200)**-0.55
    response*=1/(1+(frequencies/3500)**4)
    response*=frequencies/np.sqrt(frequencies**2+45**2)
    t=np.arange(n,dtype=np.float64)/n*2*np.pi
    channels=[]
    for offset in (0,0.17):
        noise=np.fft.irfft(np.fft.rfft(rng.standard_normal(n))*response,n=n)
        envelope=0.34+0.42*((1+np.sin(t*9+offset))/2)**2+0.18*((1+np.sin(t*13+1.3+offset))/2)**3
        noise*=envelope
        channels.append(noise)
    audio=np.stack(channels,axis=1);audio*=0.12/np.sqrt(np.mean(audio**2))
    audio*=min(1,0.78/np.max(np.abs(audio)))
    return audio.astype(np.float32)

def write_wav(path,audio):
    import numpy as np
    with wave.open(str(path),'wb') as f:
        f.setnchannels(2);f.setsampwidth(2);f.setframerate(48000)
        f.writeframes((np.clip(audio,-1,1)*32767).astype('<i2').tobytes())

def prepare(a,folder):
    import numpy as np
    import audio_workflows
    started=time.monotonic();seconds=int(a['seconds']);resolution=a['resolution']
    width,height={'1080p':(1920,1080),'4k':(3840,2160)}[resolution]
    source=a.get('source') or str(basic.STATE/'scene-assets/ocean.png')
    if not Path(source).is_file():raise ValueError('Choose an ocean image first; the default ocean background is not installed')
    if Path(source).suffix.lower() not in ('.png','.jpg','.jpeg'):raise ValueError('Ocean mode needs a PNG or JPEG still image')
    source_meta=basic.probe(source)
    if not any(s['codec_type']=='video' for s in source_meta['streams']):raise ValueError('Source is not an image')
    audio_path=folder/'ocean.wav';video_path=folder/'ocean-video.mp4';output=folder/'result.mp4'
    if a.get('sources'):
        # Overlap the tail with the beginning; preserve continuity across program repeats.
        fade=2;arranged=audio_workflows.arrange({**a,'seconds':seconds+fade},folder)
        raw=subprocess.check_output([basic.FFMPEG,'-v','error','-i',str(arranged),'-f','f32le','-ar','48000','-ac','2','-'])
        samples=np.frombuffer(raw,dtype='<f4').reshape(-1,2);n=seconds*48000;k=fade*48000
        if len(samples)<n+k:raise RuntimeError('Audio assembly returned too few samples')
        weight=np.linspace(0,1,k,dtype=np.float32)[:,None]
        audio=np.concatenate((samples[k:n],samples[n:n+k]*(1-weight)+samples[:k]*weight))
        sound_source='supplied tracks, circular crossfade'
    else:audio=surf(seconds);sound_source='periodic synthetic surf, not a field recording'
    write_wav(audio_path,audio)
    # Serialize compositor jobs, while permitting neural production and stream-copy playout.
    with open(basic.STATE/'ocean-render.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        run=subprocess.run([str(renderer()),source,str(video_path),str(width),str(height),str(seconds),str(a.get('horizon',0.52)),str(int(a.get('birds',True)))],check=True,text=True,stdout=subprocess.PIPE)
    print(run.stdout,flush=True)
    metrics=json.loads(next(line[8:] for line in run.stdout.splitlines() if line.startswith('METRICS ')))
    audio_workflows.ff('-ss','4','-i',video_path,'-i',audio_path,'-map','0:v:0','-map','1:a:0','-c:v','copy','-c:a','aac','-b:a','192k','-t',seconds,'-movflags','+faststart',output)
    info={'renderer':'Metal fixed-view water displacement and distant gulls','resolution':resolution,'source':source,'source_probe':source_meta,'source_sha256':hashlib.sha256(Path(source).read_bytes()).hexdigest(),'sound_source':sound_source,'loop_seconds':seconds,'horizon':a.get('horizon',0.52),'render':metrics,'total_seconds':round(time.monotonic()-started,3),'bytes':output.stat().st_size}
    (folder/'scene.json').write_text(json.dumps(info,indent=2))
    video_path.unlink();audio_path.unlink()
    return output
