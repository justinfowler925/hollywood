"""One job per process; weights and caches are released when the process exits."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request
import basic

BASE=Path('/opt/hollywood')
def cmd(args):
    subprocess.run(args,check=True,stdin=subprocess.DEVNULL)

def ff(*args):cmd([basic.FFMPEG,'-hide_banner','-nostdin','-y','-threads','2',*map(str,args)])

def stamp(seconds):
    ms=round(seconds*1000);h,ms=divmod(ms,3600000);m,ms=divmod(ms,60000);s,ms=divmod(ms,1000)
    return f'{h:02}:{m:02}:{s:02},{ms:03}'

def transcribe(source,folder,language=None):
    import torch
    import whisper
    torch.set_num_threads(4)
    model=whisper.load_model(str(BASE/'models/whisper-cpu/base.pt'),device='cpu')
    result=model.transcribe(source,fp16=False,language=language,verbose=False,condition_on_previous_text=False)
    (folder/'transcript.txt').write_text(result['text'].strip())
    (folder/'transcript.json').write_text(json.dumps(result,indent=2))
    srt='\n\n'.join(f"{i+1}\n{stamp(s['start'])} --> {stamp(s['end'])}\n{s['text'].strip()}" for i,s in enumerate(result['segments']))
    (folder/'captions.srt').write_text(srt+'\n')
    return result

def run(request):
    op=request['operation'];a=request['args'];folder=Path(request['folder']);folder.mkdir(parents=True,exist_ok=True)
    t=time.monotonic();out=folder/'result.wav';outputs=[];extra={}
    if op in basic.OPERATIONS:
        result=basic.run_job({'id':request['id'],'operation':op,'args':json.dumps(a)})
        result['outputs']=[result['path']];return result
    if op in ('generate_soundscape','arrange_audio','prepare_broadcast'):
        import audio_workflows
        out={'generate_soundscape':audio_workflows.soundscape,'arrange_audio':audio_workflows.arrange,'prepare_broadcast':audio_workflows.prepare}[op](a,folder)
        extra['recipe']=a
        if (folder/'scene.json').exists():extra['scene']=json.loads((folder/'scene.json').read_text())
        if op=='generate_soundscape':extra['sound_source']='uploaded recordings' if a.get('sources') else 'procedural synthesis, not field recordings'
    elif op=='compose_program':
        import program_renderer
        out=program_renderer.render(a,folder)
        extra['scene']=json.loads((folder/'scene.json').read_text())
    elif op=='render_scene':
        import scene_renderer
        out=scene_renderer.render(a,folder)
        extra['scene']=json.loads((folder/'scene.json').read_text())
    elif op in ('transcribe','caption_video'):
        transcription=transcribe(a['source'],folder,a.get('language'))
        out=folder/'transcript.txt';outputs=[str(folder/n) for n in ('transcript.txt','transcript.json','captions.srt')]
        extra['text']=transcription['text']
        if op=='caption_video':
            out=folder/'result.mp4'
            # A fixed filename in our own working directory avoids filter-path injection.
            subprocess.run([basic.FFMPEG,'-hide_banner','-nostdin','-y','-i',a['source'],
                '-vf','subtitles=captions.srt:force_style=FontSize=20','-c:v','libx264','-threads','2','-c:a','aac','-movflags','+faststart',str(out)],cwd=folder,check=True)
            outputs.insert(0,str(out))
    elif op=='separate_stems':
        cmd([sys.executable,'-m','demucs.separate','-n','htdemucs','-d','cpu','-j','2','--shifts','0','-o',str(folder/'stems'),a['source']])
        outputs=[str(x) for x in (folder/'stems').rglob('*.wav')]
        if len(outputs)<4:raise RuntimeError('Missing separated stems')
        out=next(Path(x) for x in outputs if Path(x).name=='vocals.wav')
    elif op=='mix_audio':
        ff('-i',a['source'],'-stream_loop','-1','-i',a['audio'],'-filter_complex',f"[1:a]volume={a.get('bed_gain',0.2)}[bed];[0:a][bed]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-16:TP=-1.5:LRA=11[out]",'-map','[out]','-ar','48000',out)
    elif op=='concat_video':
        clips=[]
        for i,path in enumerate(a['sources']):
            clip=folder/f'part-{i}.mp4';meta=basic.probe(path)
            args=['-i',path]
            if not any(s['codec_type']=='audio' for s in meta['streams']):args+=['-f','lavfi','-i','anullsrc=r=48000:cl=stereo','-shortest']
            ff(*args,'-vf','scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1','-r','24','-c:v','libx264','-threads','2','-pix_fmt','yuv420p','-c:a','aac','-ar','48000','-ac','2',clip)
            clips.append(clip)
        listing=folder/'concat.txt';listing.write_text('\n'.join(f"file '{p.name}'" for p in clips))
        out=folder/'result.mp4';ff('-f','concat','-safe','1','-i',listing,'-c','copy','-movflags','+faststart',out)
    elif op in ('neural_speech','design_voice','clone_voice'):
        import mlx.core as mx
        from mlx_audio.tts.utils import load_model
        from mlx_audio.audio_io import write
        mx.set_cache_limit(2*2**30)
        names={'neural_speech':'Higgs-Audio-v3','design_voice':'Qwen3-TTS-1.7B-VoiceDesign-8bit','clone_voice':'Qwen3-TTS-1.7B-Base-8bit'}
        model=load_model(str(BASE/'models'/names[op]))
        if op=='design_voice':iterator=model.generate_voice_design(text=a['text'],instruct=a['instruction'],max_tokens=2048)
        elif op=='clone_voice':iterator=model.generate(text=a['text'],ref_audio=a['source'],ref_text=a['ref_text'],max_tokens=2048)
        else:iterator=model.generate(text=a['text'],max_new_tokens=2048)
        chunks=[];rate=None
        for r in iterator:chunks.append(r.audio);rate=r.sample_rate
        if not chunks:raise RuntimeError('Speech model returned no audio')
        audio=mx.concatenate(chunks,axis=0) if len(chunks)>1 else chunks[0]
        write(str(out),audio,rate);extra['peak_memory_gib']=round(mx.get_peak_memory()/2**30,3)
    elif op=='generate_video':
        # Bound connector attention work per dispatch on the shared Studio GPU.
        os.environ['LTX2_GEMMA_MAX_LENGTH']='512'
        os.environ['LTX2_DIT_EVAL_EVERY']='1'
        frames=8*max(3,round(float(a.get('seconds',4))*24/8))+1
        out=folder/'result.mp4'
        quality=a.get('quality','standard');aspect=a.get('aspect','landscape')
        width,height={'landscape':(1280,768),'portrait':(768,1280),'square':(1024,1024)}[aspect]
        final_width,final_height={'landscape':(1280,720),'portrait':(720,1280),'square':(1024,1024)}[aspect]
        pipeline=['--distilled']
        if quality=='draft':
            width,height={'landscape':(384,256),'portrait':(256,384),'square':(384,384)}[aspect]
            final_width,final_height={'landscape':(384,216),'portrait':(216,384),'square':(384,384)}[aspect]
            pipeline=['--one-stage','--steps','8']
        elif quality=='high':pipeline=['--two-stage','--stage1-steps','20','--stage2-steps','3']
        generated=folder/'generated.mp4'
        args=[sys.executable,str(Path(__file__).with_name('video_runner.py')),'generate','--prompt',a['prompt'],*pipeline,
              '--model',str(BASE/'models/LTX-2.3-q8'),'--gemma',str(BASE/'models/gemma-3-12b-it-4bit'),
              '-H',str(height),'-W',str(width),'--frames',str(frames),'--frame-rate','24','--seed',str(a.get('seed',42)),'-o',str(generated)]
        if a.get('source'):args+=['--image',a['source']]
        cmd(args)
        ff('-i',generated,'-vf',f'crop={final_width}:{final_height},setsar=1','-c:v','libx264','-threads','2','-crf','18','-preset','medium','-c:a','aac','-b:a','192k','-movflags','+faststart',out)
        extra.update(quality=quality,aspect=aspect,generated_dimensions=[width,height],output_dimensions=[final_width,final_height],seed=a.get('seed',42),pipeline=' '.join(pipeline))
        metrics=folder/'video-metrics.json'
        if metrics.exists():extra.update(json.loads(metrics.read_text()))
    elif op=='generate_image':
        def api(path,data=None):
            req=urllib.request.Request('http://127.0.0.1:8955'+path,data=json.dumps(data).encode() if data else None,headers={'Content-Type':'application/json'})
            return json.load(urllib.request.urlopen(req,timeout=30))
        jid=api('/api/generate',{'prompt':a['prompt'],'tier':'low','shape':'square','request_id':request['id']})['id']
        extra['image_service_job']=jid
        for _ in range(1200):
            result=api('/api/job/'+jid)
            if result.get('state')=='done':break
            if result.get('state') in ('failed','cancelled'):raise RuntimeError(result.get('error_title','configured image service stopped'))
            time.sleep(1)
        else:raise TimeoutError('configured image service timed out')
        out=folder/'result.png';shutil.copyfile(Path.home()/'.image_service/images'/f'{jid}.png',out)
    elif op=='generate_music':
        cmd([str(BASE/'music-runtime/bin/python'),str(Path(__file__).with_name('music_task.py')),str(folder/'request.json')])
    else:raise ValueError('Unsupported operation')
    if not out.is_file() or out.stat().st_size==0:raise RuntimeError('Output missing or empty')
    result={'path':str(out),'outputs':outputs or [str(out)],'bytes':out.stat().st_size,'seconds':round(time.monotonic()-t,3),**extra}
    if out.suffix in ('.mp4','.wav'):
        result['probe']=basic.probe(str(out))
        if float(result['probe']['format'].get('duration',0))<=0:raise RuntimeError('Empty output duration')
    # Keep registered outputs and provenance; discard reproducible assembly scratch.
    if op in ('arrange_audio','prepare_broadcast'):
        for scratch in [*folder.glob('audio-*.wav'),folder/'set.wav']:
            scratch.unlink(missing_ok=True)
        if op=='prepare_broadcast':(folder/'result.wav').unlink(missing_ok=True)
    if op=='generate_music':shutil.rmtree(folder/'music',ignore_errors=True)
    if op=='generate_video':(folder/'generated.mp4').unlink(missing_ok=True)
    return result

if __name__=='__main__':
    request=json.loads(Path(sys.argv[1]).read_text())
    result=run(request)
    folder=Path(request['folder']);temp=folder/'result.json.partial'
    temp.write_text(json.dumps(result,indent=2));temp.replace(folder/'result.json')
    print(json.dumps({'operation':request['operation'],'status':'complete','path':result['path']}),flush=True)
