"""Durable project/asset/job store and restartable media executors."""
import concurrent.futures
import contextlib
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
import urllib.request
import basic
import audio_workflows
import scene_renderer
import program_renderer

STATE=basic.STATE
HERE=Path(__file__).resolve().parent
RUNTIME=Path(os.environ.get('STUDIO_MEDIA_PYTHON','/opt/hollywood/runtime/bin/python'))
GPU_LOCK=Path('/tmp/hollywood-gpu.lock')
GPU_OPS={'compose_program','render_scene','neural_speech','design_voice','clone_voice','generate_video','generate_music'}
OPERATIONS={'compose_program':program_renderer.DESCRIPTION,'render_scene':scene_renderer.DESCRIPTION,**basic.OPERATIONS,**audio_workflows.OPERATIONS,
 'transcribe':'Transcribe source audio/video to text, JSON and timed SRT captions.',
 'caption_video':'Transcribe source video and burn readable captions into MP4.',
 'separate_stems':'Separate source audio into vocals, drums, bass and other WAV stems.',
 'mix_audio':'Mix source audio with an audio bed; bed_gain defaults to 0.2.',
 'concat_video':'Join sources (array of videos) into normalized H.264/AAC MP4.',
 'neural_speech':'Generate expressive speech with Higgs; text required.',
 'design_voice':'Generate speech with a described Qwen voice; text and instruction required.',
 'clone_voice':'Generate speech with a Qwen reference voice; text, source audio and ref_text required.',
 'generate_video':'LTX text/image-to-video; HD by default. quality draft/standard/high, aspect landscape/portrait/square, seconds 1–8, optional seed.',
 'generate_image':'Generate an image through the existing configured image service queue; prompt required.',
 'generate_music':'Generate music with ACE-Step; prompt, seconds 10–120, lyrics optional.'}

class ClosingConnection(sqlite3.Connection):
    def __exit__(self,*args):
        try:return super().__exit__(*args)
        finally:self.close()

def db():
    STATE.mkdir(parents=True,exist_ok=True)
    c=sqlite3.connect(STATE/'jobs.sqlite3',timeout=30,factory=ClosingConnection)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,operation TEXT,args TEXT,status TEXT,created REAL,updated REAL,result TEXT,error TEXT)')
    columns={r[1] for r in c.execute('PRAGMA table_info(jobs)')}
    for name,kind in [('project','TEXT'),('request_key','TEXT'),('dependencies',"TEXT DEFAULT '[]'"),('attempts','INTEGER DEFAULT 0'),('pid','INTEGER'),('progress',"TEXT DEFAULT ''"),('lane',"TEXT DEFAULT 'cpu'"),('transient','INTEGER DEFAULT 0')]:
        if name not in columns:c.execute(f'ALTER TABLE jobs ADD COLUMN {name} {kind}')
    c.executescript('''
    CREATE UNIQUE INDEX IF NOT EXISTS job_request ON jobs(request_key) WHERE request_key IS NOT NULL;
    CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,name TEXT,created REAL);
    CREATE TABLE IF NOT EXISTS assets(id TEXT PRIMARY KEY,project TEXT,name TEXT,path TEXT,kind TEXT,bytes INTEGER,created REAL,job TEXT);
    CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY,project TEXT,role TEXT,text TEXT,created REAL,request_key TEXT UNIQUE);
    CREATE TABLE IF NOT EXISTS turn_plans(request_key TEXT PRIMARY KEY,project TEXT,text TEXT,plan TEXT,created REAL);
    ''')
    c.commit()
    return c

def init():
    with db() as c:c.execute('INSERT OR IGNORE INTO projects VALUES(?,?,?)',('studio','Studio',time.time()))

def decode(row):
    r=dict(row)
    for key in ('args','result','dependencies'):
        if key in r:r[key]=json.loads(r[key]) if r[key] else None
    return r

def get_job(job_id):
    with db() as c:r=c.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
    if r is None:raise ValueError('Unknown job')
    return decode(r)

def projects():
    with db() as c:return [dict(r) for r in c.execute('SELECT * FROM projects ORDER BY created')]

def create_project(name):
    name=str(name).strip()
    if not 1<=len(name)<=120:raise ValueError('Project name must contain 1–120 characters')
    p={'id':uuid.uuid4().hex,'name':name,'created':time.time()}
    with db() as c:c.execute('INSERT INTO projects VALUES(:id,:name,:created)',p)
    return p

def project_exists(project):
    with db() as c:r=c.execute('SELECT id FROM projects WHERE id=?',(project,)).fetchone()
    if not r:raise ValueError('Unknown project')

def register_asset(project,path,name=None,job=None):
    p=Path(path).resolve()
    if not p.is_file():raise ValueError('Asset file is missing')
    suffix=p.suffix.lower()
    kind='video' if suffix in ('.mp4','.mov','.webm','.mkv') else 'audio' if suffix in ('.wav','.mp3','.m4a','.aiff','.ogg','.flac') else 'image' if suffix in ('.png','.jpg','.jpeg','.webp','.heic') else 'text'
    item={'id':uuid.uuid4().hex,'project':project,'name':name or p.name,'path':str(p),'kind':kind,'bytes':p.stat().st_size,'created':time.time(),'job':job}
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        if job:
            existing=c.execute('SELECT * FROM assets WHERE job=? AND path=?',(job,str(p))).fetchone()
            if existing:return dict(existing)
        c.execute('INSERT INTO assets VALUES(:id,:project,:name,:path,:kind,:bytes,:created,:job)',item)
    return item

def asset(asset_id,project=None):
    with db() as c:r=c.execute('SELECT * FROM assets WHERE id=?',(asset_id,)).fetchone()
    if not r or (project and r['project']!=project):raise ValueError('Unknown asset in project')
    return dict(r)

def snapshot(project='studio'):
    project_exists(project)
    with db() as c:
        return {'project':project,'jobs':[decode(r) for r in c.execute('SELECT * FROM jobs WHERE project=? AND transient=0 ORDER BY created DESC LIMIT 200',(project,))],
                'assets':[dict(r) for r in c.execute('SELECT * FROM assets WHERE project=? AND (job IS NULL OR job NOT IN (SELECT id FROM jobs WHERE transient=1)) ORDER BY created DESC LIMIT 300',(project,))],
                'messages':[dict(r) for r in c.execute('SELECT * FROM (SELECT * FROM messages WHERE project=? ORDER BY created DESC LIMIT 1000) ORDER BY created',(project,))]}

def message(project,role,text,request_key=None):
    item={'id':uuid.uuid4().hex,'project':project,'role':role,'text':str(text),'created':time.time(),'request_key':request_key}
    with db() as c:c.execute('INSERT OR IGNORE INTO messages VALUES(:id,:project,:role,:text,:created,:request_key)',item)
    return item

def finite_number(value,low,high):
    import math
    n=float(value)
    if not math.isfinite(n) or not low<=n<=high:raise ValueError(f'Value must be {low}–{high}')
    return n

def validate(operation,args,deferred=False):
    if operation not in OPERATIONS:raise ValueError('Unsupported operation')
    if not isinstance(args,dict):raise ValueError('Parameters must be an object')
    if operation=='compose_program':return program_renderer.validate(args,deferred)
    if operation=='render_scene':return scene_renderer.validate(args,deferred)
    if operation in audio_workflows.OPERATIONS:return audio_workflows.validate(operation,args,deferred)
    if operation=='generate_music':args=audio_workflows.music_args(args)
    if operation in basic.OPERATIONS:return basic.validate(operation,args,deferred=deferred)
    specs={'transcribe':({'source'},{'source','language'}),'caption_video':({'source'},{'source','language'}),
     'separate_stems':({'source'},{'source'}),'mix_audio':({'source','audio'},{'source','audio','bed_gain'}),
     'concat_video':({'sources'},{'sources'}),'neural_speech':({'text'},{'text'}),
     'design_voice':({'text','instruction'},{'text','instruction'}),
     'clone_voice':({'text','source','ref_text'},{'text','source','ref_text'}),
     'generate_video':({'prompt'},{'prompt','source','seconds','seed','quality','aspect'}),
     'generate_image':({'prompt'},{'prompt'}),'generate_music':({'prompt'},{'prompt','seconds','lyrics','seed','preset','bpm','key'})}
    required,allowed=specs[operation]
    if set(args)-allowed or required-set(args):raise ValueError(f'{operation}: required {sorted(required)}, allowed {sorted(allowed)}')
    a=dict(args)
    for key in ('source','audio'):
        if key in a and not (deferred and str(a[key]).startswith('job:')):a[key]=basic.source_path(a[key])
    if 'sources' in a:
        if not isinstance(a['sources'],list) or not 1<=len(a['sources'])<=20:raise ValueError('sources must contain 1–20 media paths')
        a['sources']=[x if deferred and str(x).startswith('job:') else basic.source_path(x) for x in a['sources']]
    for key in ('text','prompt','instruction','ref_text','lyrics','language'):
        if key in a and (not isinstance(a[key],str) or len(a[key])>20000 or (key!='lyrics' and not a[key].strip())):raise ValueError('Invalid '+key)
    if operation=='generate_video':
        a.setdefault('quality','standard');a.setdefault('aspect','landscape');a.setdefault('seconds',4)
        if a['quality'] not in ('draft','standard','high'):raise ValueError('quality must be draft, standard or high')
        if a['aspect'] not in ('landscape','portrait','square'):raise ValueError('aspect must be landscape, portrait or square')
    if 'seconds' in a:a['seconds']=finite_number(a['seconds'],1 if operation=='generate_video' else 10,8 if operation=='generate_video' else 120)
    if 'bed_gain' in a:a['bed_gain']=finite_number(a['bed_gain'],0,2)
    if 'seed' in a:a['seed']=int(finite_number(a['seed'],0,2**31-1))
    return a

def submit(operation,args,start_worker=True,project='studio',request_key=None,dependencies=None,transient=False):
    init();project_exists(project)
    dependencies=dependencies or []
    for dep in dependencies:
        if get_job(dep)['project']!=project:raise ValueError('Dependency must belong to this project')
    a=validate(operation,args,bool(dependencies))
    if request_key and len(request_key)>200:raise ValueError('Request key too long')
    now=time.time();job_id=uuid.uuid4().hex
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        if request_key:
            existing=c.execute('SELECT * FROM jobs WHERE request_key=?',(request_key,)).fetchone()
            if existing:
                previous=json.loads(existing['args'])
                # Newly introduced defaults must not duplicate historical requests.
                comparison=dict(a)
                if operation=='generate_video':
                    for key in ('quality','aspect','seconds'):
                        if key not in previous and key not in args:comparison.pop(key,None)
                if existing['operation']!=operation or previous!=comparison or existing['project']!=project:raise ValueError('Idempotency key already used for another request')
                return decode(existing)
        c.execute('INSERT INTO jobs(id,operation,args,status,created,updated,project,request_key,dependencies,lane,transient) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                  (job_id,operation,json.dumps(a),'queued',now,now,project,request_key,json.dumps(dependencies),'gpu' if operation in GPU_OPS else 'cpu',int(transient)))
    if start_worker and os.environ.get('STUDIO_MEDIA_MANAGED')!='1':
        managed=False
        try:
            with urllib.request.urlopen('http://127.0.0.1:8960/api/health',timeout=0.5) as response:
                health=json.load(response)
                managed=health.get('ok') is True and health.get('service')=='studio-media'
        except (OSError,ValueError):pass
        if not managed:
            with open(STATE/'worker.log','ab') as log:
                subprocess.Popen([str(RUNTIME),str(HERE/'department.py'),'work'],stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
    return get_job(job_id)

def cancel(job_id):
    with db() as c:
        c.execute("UPDATE jobs SET status=CASE WHEN status='queued' THEN 'cancelled' ELSE 'cancelling' END,updated=? WHERE id=? AND status IN ('queued','running','waiting')",(time.time(),job_id))
    return get_job(job_id)

def retry(job_id):
    r=get_job(job_id)
    if r['status'] not in ('failed','cancelled','interrupted'):raise ValueError('Only a stopped job can be retried')
    return submit(r['operation'],r['args'],project=r['project'],dependencies=r['dependencies'])

def update(job_id,**fields):
    fields['updated']=time.time()
    with db() as c:c.execute('UPDATE jobs SET '+','.join(k+'=?' for k in fields)+' WHERE id=?',(*fields.values(),job_id))

def resolve_args(args,deps):
    def resolve(value):
        if isinstance(value,list):return [resolve(x) for x in value]
        if isinstance(value,str) and value.startswith('job:'):
            jid=value[4:]
            if jid not in deps:raise ValueError('Undeclared dependency')
            return get_job(jid)['result']['path']
        return value
    return {k:resolve(v) for k,v in args.items()}

def execute(row,stop=None):
    stop=stop or threading.Event()
    jid=row['id'];folder=STATE/'outputs'/jid;folder.mkdir(parents=True,exist_ok=True)
    args=validate(row['operation'],resolve_args(json.loads(row['args']),json.loads(row['dependencies'] or '[]')))
    payload=folder/'request.json';payload.write_text(json.dumps({'id':jid,'operation':row['operation'],'args':args,'folder':str(folder)}))
    if shutil.disk_usage(STATE).free < 10*2**30:raise RuntimeError('Less than 10 GiB scratch space available')
    with contextlib.ExitStack() as stack:
        if row['lane']=='gpu':
            lock=stack.enter_context(open(GPU_LOCK,'a'))
            update(jid,progress='Waiting for shared media GPU lease')
            while True:
                if get_job(jid)['status']=='cancelling':update(jid,status='cancelled');return
                if stop.is_set():update(jid,status='queued',progress='Resuming after service restart');return
                try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);break
                except BlockingIOError:time.sleep(0.5)
        env=dict(os.environ,PATH='/opt/homebrew/bin:'+os.environ.get('PATH',''),HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',TOKENIZERS_PARALLELISM='false',OMP_NUM_THREADS='4',STUDIO_MEDIA_STATE=str(STATE))
        cmd=[str(RUNTIME),str(HERE/'job_task.py'),str(payload)]
        update(jid,progress='Processing media')
        with open(folder/'task.log','ab') as log:
            p=subprocess.Popen(cmd,stdout=log,stderr=log,stdin=subprocess.DEVNULL,env=env,start_new_session=True)
            update(jid,pid=p.pid)
            deadline=time.monotonic()+7200
            progress_check=0;last_progress=''
            try:
                while p.poll() is None:
                    cancelled=get_job(jid)['status']=='cancelling'
                    if cancelled or stop.is_set():
                        if cancelled and row['operation']=='generate_image':
                            import urllib.request,urllib.error
                            try:
                                req=urllib.request.Request('http://127.0.0.1:8955/api/cancel/'+jid,data=b'{}',method='POST')
                                with urllib.request.urlopen(req,timeout=10) as response:response.read()
                            except urllib.error.HTTPError as exc:
                                if exc.code!=404:raise
                        os.killpg(p.pid,signal.SIGTERM)
                        try:p.wait(timeout=8)
                        except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait()
                        if row['operation']=='render_scene':scene_renderer.cleanup(folder)
                        if row['operation']=='compose_program':program_renderer.cleanup(folder)
                        update(jid,status='cancelled' if cancelled else 'queued',pid=None,progress='Cancelled' if cancelled else 'Resuming after service restart');return
                    if time.monotonic()>deadline:raise TimeoutError('Job exceeded two-hour limit')
                    if row['operation']=='generate_video' and time.monotonic()>progress_check:
                        progress_check=time.monotonic()+3
                        with open(folder/'task.log','rb') as reader:
                            reader.seek(max(0,reader.seek(0,2)-4096))
                            lines=reader.read().decode('utf-8',errors='replace').replace('\r','\n').splitlines()
                        visible=[line.strip() for line in lines if line.startswith(('Denoising','[Loading','[Encoding','[Decoding','[Saving'))]
                        progress='Preparing video'
                        if visible:
                            line=visible[-1];steps=re.search(r'\|\s*(\d+)/(\d+)\s*\[',line)
                            if steps:progress=('Refining detail' if steps[2]=='3' else 'Generating motion')+f' · step {steps[1]} of {steps[2]}'
                            elif line.startswith('[Encoding'):progress='Reading your description'
                            elif line.startswith('[Decoding'):progress='Rendering video frames'
                            elif line.startswith('[Saving'):progress='Saving your take'
                            elif line.startswith('[Loading'):progress='Loading video model'
                        if progress!=last_progress:update(jid,progress=progress);last_progress=progress
                    if row['operation']=='render_scene' and time.monotonic()>progress_check:
                        progress_check=time.monotonic()+2
                        with open(folder/'task.log','rb') as reader:
                            reader.seek(max(0,reader.seek(0,2)-2048))
                            for line in reader.read().decode(errors='replace').splitlines():
                                try:frame=json.loads(line)
                                except (ValueError,TypeError):continue
                                if isinstance(frame,dict) and 'frame' in frame and 'total' in frame:
                                    update(jid,progress=f"Rendering scene · {frame['frame']}/{frame['total']} frames")
                    time.sleep(0.4)
                if p.returncode:raise RuntimeError(f'Media worker exited {p.returncode}; see task log')
            finally:
                if p.poll() is None:
                    os.killpg(p.pid,signal.SIGKILL);p.wait()
                if row['operation']=='render_scene':scene_renderer.cleanup(folder)
                if row['operation']=='compose_program':program_renderer.cleanup(folder)
                update(jid,pid=None)
        result=json.loads((folder/'result.json').read_text())
        assets=[]
        for output in result.get('outputs',[result['path']]):
            path=Path(output).resolve()
            if not path.is_relative_to(folder.resolve()):raise ValueError('Output outside job folder')
            label=row['operation'].replace('_',' ')+' · '+jid[:6]+' · '+path.name
            assets.append(register_asset(row['project'],path,name=label,job=jid))
        result['assets']=assets
        update(jid,status='succeeded',result=json.dumps(result),error=None,progress='Complete')

def work(forever=False,stop=None):
    init();stop=stop or threading.Event()
    with open(STATE/'service-worker.lock','a') as lease:
        while not stop.is_set():
            try:fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB);break
            except BlockingIOError:time.sleep(0.25)
        else:return
        # Old children are terminated before requeueing their deterministic jobs.
        with db() as c:
            for r in c.execute("SELECT * FROM jobs WHERE status IN ('running','waiting','cancelling')"):
                if r['pid']:
                    cmd=subprocess.run(['ps','-p',str(r['pid']),'-o','command='],capture_output=True,text=True).stdout
                    if 'job_task.py' in cmd and r['id'] in cmd:
                        try:os.killpg(r['pid'],signal.SIGKILL)
                        except ProcessLookupError:pass
                c.execute("UPDATE jobs SET status=?,pid=NULL WHERE id=?",('cancelled' if r['status']=='cancelling' else 'queued',r['id']))
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            active={}
            while not stop.is_set():
                for jid,(future,lane) in list(active.items()):
                    if future.done():
                        try:future.result()
                        except Exception as exc:update(jid,status='failed',error=str(exc),progress='Failed')
                        del active[jid]
                with db() as c:rows=c.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created").fetchall()
                for row in rows:
                    if sum(v[1]==row['lane'] for v in active.values()) >= (1 if row['lane']=='gpu' else 2):continue
                    deps=[get_job(x) for x in json.loads(row['dependencies'] or '[]')]
                    if any(x['status'] in ('failed','cancelled','interrupted') for x in deps):update(row['id'],status='failed',error='A dependency did not complete');continue
                    if any(x['status']!='succeeded' for x in deps):continue
                    with db() as c:
                        changed=c.execute("UPDATE jobs SET status='running',attempts=attempts+1,updated=? WHERE id=? AND status='queued'",(time.time(),row['id'])).rowcount
                    if changed:active[row['id']]=(pool.submit(execute,row,stop),row['lane'])
                if not forever and not active and not rows:return
                time.sleep(0.5)
            for jid,(future,lane) in active.items():
                try:future.result()
                except Exception as exc:update(jid,status='failed',error=str(exc),progress='Failed')

def capabilities():
    manifest=STATE/'qualified.json'
    qualified=json.loads(manifest.read_text()) if manifest.exists() else {}
    return {'executor':'jf-studio','operations':{k:{'description':v,'qualified':k in qualified,'evidence':qualified.get(k)} for k,v in OPERATIONS.items()},
            'scene_catalog':{'version':scene_renderer.scene_catalog.VERSION,'presets':scene_renderer.scene_catalog.PRESETS,'palettes':list(scene_renderer.scene_catalog.PALETTES),'cameras':scene_renderer.scene_catalog.CAMERAS},
            'ocean_evidence':json.loads((STATE/'ocean-qualified.json').read_text()) if (STATE/'ocean-qualified.json').exists() else None,
            'music_presets':audio_workflows.PRESETS,'broadcast_evidence':json.loads((STATE/'broadcast-qualified.json').read_text()) if (STATE/'broadcast-qualified.json').exists() else None,'model_archive':json.loads((HERE/'models.json').read_text()),'projects':projects()}
