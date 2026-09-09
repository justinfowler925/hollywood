"""Private Studio web service with durable projects, uploads, conversation and jobs."""
import asyncio
import contextlib
import os
import re
from pathlib import Path
import shutil
import threading
import uuid
import hashlib
import json
from urllib.parse import urlparse
os.environ['STUDIO_MEDIA_MANAGED']='1'
import engine
import producer
import broadcast
import scene_channel
from fastapi import FastAPI,HTTPException,Request,UploadFile,File
from fastapi.responses import FileResponse,JSONResponse
from starlette.concurrency import run_in_threadpool

HERE=Path(__file__).resolve().parent
SOURCE_FILES=('scene-channel-ui.js','scene_channel.py','program_renderer.py','scene_catalog.py','scene_renderer.py','scenes/ocean_blender.py','ocean_scene.py','ocean/Renderer.swift','ocean/ocean.metal','audio_workflows.py','broadcast.py','basic.py','engine.py','department.py','job_task.py','video_runner.py','music_task.py','producer.py','service.py','index.html','broadcast-ui.js','app.js','style.css','models.json')
SOURCE_FILES+=tuple('scenes/godot_sky/'+p.name for p in sorted((HERE/'scenes/godot_sky').iterdir()) if p.is_file())
SOURCE_DIGEST=hashlib.sha256(b''.join(name.encode()+b'\0'+(HERE/name).read_bytes() for name in SOURCE_FILES)).hexdigest()
CHAT_LOCKS={};VOICE_LOCK=threading.Lock();VOICE_MODEL=None

@contextlib.asynccontextmanager
async def lifespan(app):
    engine.init();stop=threading.Event()
    worker=threading.Thread(target=engine.work,kwargs={'forever':True,'stop':stop},daemon=True);worker.start()
    broadcaster=threading.Thread(target=broadcast.work,args=(stop,),daemon=True);broadcaster.start()
    scene_worker=threading.Thread(target=scene_channel.work,args=(stop,),daemon=True);scene_worker.start()
    yield
    stop.set();scene_worker.join(timeout=10);worker.join(timeout=15);broadcaster.join(timeout=10)

app=FastAPI(lifespan=lifespan)

@app.middleware('http')
async def boundaries(request:Request,call_next):
    host=request.headers.get('host','').split(':')[0]
    if host not in ('127.0.0.1','localhost','testserver') and not host.endswith('.example.invalid'):
        return JSONResponse({'error':'Use the private Studio address'},status_code=403)
    origin=request.headers.get('origin')
    if origin and urlparse(origin).netloc!=request.headers.get('host'):
        return JSONResponse({'error':'Origin mismatch'},status_code=403)
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff';response.headers['Referrer-Policy']='same-origin';response.headers['X-Frame-Options']='DENY'
    return response

@app.exception_handler(ValueError)
async def invalid(request,exc):return JSONResponse({'error':str(exc)},status_code=400)

@app.get('/')
def index():return FileResponse(HERE/'index.html')
@app.get('/app.js')
def script():return FileResponse(HERE/'app.js',media_type='application/javascript')
@app.get('/broadcast-ui.js')
def broadcast_script():return FileResponse(HERE/'broadcast-ui.js',media_type='application/javascript')
@app.get('/style.css')
def style():return FileResponse(HERE/'style.css',media_type='text/css')
@app.get('/api/health')
def health():return {'ok':True,'service':'studio-media','executor':'jf-studio','source_digest':SOURCE_DIGEST,'free_gib':round(shutil.disk_usage(engine.STATE).free/2**30,1)}
@app.get('/api/capabilities')
def capabilities():return engine.capabilities()
@app.get('/api/projects')
def projects():return engine.projects()
@app.post('/api/projects')
async def create_project(request:Request):return engine.create_project((await request.json()).get('name',''))
@app.get('/api/projects/{project}')
def project(project:str):return engine.snapshot(project)

@app.post('/api/projects/{project}/upload')
async def upload(project:str,file:UploadFile=File(...)):
    engine.project_exists(project);suffix=Path(file.filename or '').suffix.lower()
    if suffix not in ('.mp4','.mov','.mkv','.webm','.wav','.mp3','.m4a','.aiff','.flac','.ogg','.png','.jpg','.jpeg','.webp','.heic','.srt','.txt'):raise ValueError('Upload an audio, video, image or caption file')
    folder=engine.STATE/'uploads';folder.mkdir(exist_ok=True);path=folder/(uuid.uuid4().hex+suffix);size=0
    try:
        with open(path,'wb') as target:
            while data:=await file.read(1024*1024):
                size+=len(data)
                if size>2*2**30 or shutil.disk_usage(folder).free<10*2**30:raise ValueError('Upload exceeds 2 GiB or available scratch space')
                target.write(data)
        if size==0:raise ValueError('Empty upload')
        return engine.register_asset(project,path,Path(file.filename or path.name).name)
    except Exception:path.unlink(missing_ok=True);raise

@app.get('/api/assets/{asset_id}')
def download(asset_id:str):
    a=engine.asset(asset_id);path=Path(a['path']).resolve()
    if not path.is_relative_to(engine.STATE.resolve()):raise HTTPException(403,'Asset is outside department storage')
    return FileResponse(path,filename=a['name'],content_disposition_type='inline')
@app.get('/api/jobs/{job_id}')
def job(job_id:str):return engine.get_job(job_id)
@app.get('/api/jobs/{job_id}/log')
def log(job_id:str):
    engine.get_job(job_id);path=engine.STATE/'outputs'/job_id/'task.log'
    if not path.exists():return {'log':'Waiting to start'}
    with open(path,'rb') as f:f.seek(max(0,path.stat().st_size-12000));text=f.read().decode('utf-8',errors='replace')
    return {'log':text}
@app.post('/api/jobs/{job_id}/cancel')
def cancel(job_id:str):return engine.cancel(job_id)
@app.post('/api/jobs/{job_id}/retry')
def retry(job_id:str):return engine.retry(job_id)

@app.post('/api/projects/{project}/jobs')
async def submit(project:str,request:Request):
    data=await request.json();params=data.get('parameters',{})
    def resolve(v):
        if isinstance(v,list):return [resolve(x) for x in v]
        return engine.asset(str(v).removeprefix('asset:'),project)['path']
    params={k:resolve(v) if k in ('source','sources','audio') else v for k,v in params.items()}
    return engine.submit(data.get('operation'),params,project=project,request_key=data.get('request_key'))

@app.post('/api/projects/{project}/chat')
async def chat(project:str,request:Request):
    engine.project_exists(project);data=await request.json();text=data.get('text','');key=data.get('request_key') or uuid.uuid4().hex
    if not isinstance(text,str) or not 1<=len(text.strip())<=10000:raise ValueError('Enter a message of 1–10000 characters')
    if not isinstance(key,str) or len(key)>120:raise ValueError('Invalid request ID')
    lock=CHAT_LOCKS.setdefault(project,asyncio.Lock())
    async with lock:
        try:return await run_in_threadpool(producer.perform,project,text,key)
        except Exception as exc:raise HTTPException(503,detail=f'The producer could not finish this turn: {str(exc)[:180]}. Your project is saved; retry this message.') from exc

def voice_transcribe(path):
    global VOICE_MODEL
    import torch,whisper
    with VOICE_LOCK:
        torch.set_num_threads(4)
        if VOICE_MODEL is None:VOICE_MODEL=whisper.load_model(str(engine.STATE.parent/'models/whisper-cpu/base.pt'),device='cpu')
        result=VOICE_MODEL.transcribe(str(path),fp16=False,language='en',condition_on_previous_text=False)
        return ' '.join(s['text'].strip() for s in result.get('segments',[]) if s.get('no_speech_prob',0)<0.6).strip()

@app.post('/api/transcribe-turn')
async def turn(request:Request):
    data=await request.body()
    if not 44<len(data)<8*1024*1024 or data[:4]!=b'RIFF':raise ValueError('Send a PCM WAV turn under 8 MiB')
    folder=engine.STATE/'turns';folder.mkdir(exist_ok=True);path=folder/(uuid.uuid4().hex+'.wav');path.write_bytes(data)
    try:return {'text':await run_in_threadpool(voice_transcribe,path)}
    finally:path.unlink(missing_ok=True)


@app.get('/api/broadcast/{project}')
def broadcast_status(project:str):return broadcast.status(project)
@app.post('/api/broadcast/{project}/configure')
async def broadcast_configure(project:str,request:Request):
    data=await request.json()
    return broadcast.configure(project,data.get('asset_id',''),data.get('endpoint'),data.get('start_at'),data.get('stop_at'),data.get('asset_ids'))
@app.post('/api/broadcast/{project}/{action}')
def broadcast_control(project:str,action:str):return broadcast.control(project,action)
@app.get('/api/broadcast/{project}/live/{name}')
def broadcast_media(project:str,name:str):
    import re
    if not (name=='channel.m3u8' or re.fullmatch(r'channel[0-9]+\.ts',name)):raise HTTPException(404)
    path=broadcast.folder(project)/name
    if not path.is_file():raise HTTPException(404,'Preview is warming up')
    return FileResponse(path,media_type='application/vnd.apple.mpegurl' if name.endswith('m3u8') else 'video/mp2t',headers={'Cache-Control':'no-store'})



@app.get('/api/projects/{project}/scene-channel')
def scene_status(project:str):return scene_channel.status(project)

@app.post('/api/projects/{project}/scene-channel')
def scene_configure(project:str,parameters:dict):
    if parameters.get('audio'):parameters['audio']=engine.asset(parameters['audio'],project)['path']
    return scene_channel.configure(project,parameters)

@app.post('/api/projects/{project}/scene-channel/{action}')
def scene_control(project:str,action:str):return scene_channel.control(project,action)

@app.get('/api/scene-channel/{project}/live/{name}')
def scene_live(project:str,name:str):
    if not re.fullmatch(r'(channel\.m3u8|chunk-[a-z0-9-]+\.ts)',name):raise HTTPException(404)
    path=scene_channel.folder(project)/name
    if not path.is_file():raise HTTPException(404)
    return FileResponse(path,media_type='application/vnd.apple.mpegurl' if name.endswith('m3u8') else 'video/mp2t',headers={'Cache-Control':'no-store'})

@app.get('/scene-channel-ui.js')
def scene_channel_script():return FileResponse(HERE/'scene-channel-ui.js',media_type='application/javascript')

if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=8960,log_level='info',timeout_keep_alive=45)
