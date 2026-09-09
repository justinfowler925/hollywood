"""Persistent, bounded private HLS scene channel with render-ahead and filler."""
import json,os,re,time,threading,subprocess,shutil,uuid,math
from pathlib import Path
import engine,basic,scene_renderer
LOCK=threading.RLock()

def folder(project):
    engine.project_exists(project)
    if not re.fullmatch(r'[A-Za-z0-9_-]+',project):raise ValueError('Invalid project')
    p=engine.STATE/'scene-channels'/project;p.mkdir(parents=True,exist_ok=True);return p

def save(project,state):
    p=folder(project)/'state.json';tmp=p.with_suffix('.partial');tmp.write_text(json.dumps(state,indent=2));tmp.replace(p)

def read(project):
    p=folder(project)/'state.json'
    return json.loads(p.read_text()) if p.exists() else {'desired':'stopped','status':'unconfigured','project':project}

def status(project='studio'):
    with LOCK:
        s=read(project)
        return {k:v for k,v in s.items() if k not in ('ready','playlist','retired')}|{'ready_chunks':len(s.get('ready',[])),'buffer_seconds':len(s.get('ready',[]))*s.get('seconds',0),'preview_url':f'/api/scene-channel/{project}/live/channel.m3u8'}

def configure(project,parameters):
    with LOCK:
        s=read(project)
        if s.get('desired')=='running' or s.get('job'):raise ValueError('Stop the scene channel before editing')
        parameters=dict(parameters);sequence=parameters.pop('sequence',None)
        a=scene_renderer.validate({'renderer':'godot','seconds':16,**parameters})
        if isinstance(sequence,str):sequence=[x.strip() for x in sequence.split(',')]
        sequence=sequence or [a['scene']]
        if not isinstance(sequence,list) or not 1<=len(sequence)<=12 or any(x not in scene_renderer.scene_catalog.PRESETS for x in sequence):raise ValueError('Select 1–12 scene names in sequence')
        if 'music_visualizer' in sequence and not a.get('audio'):raise ValueError('Music scenes require audio')
        if a['resolution']=='4k':raise ValueError('4K is available for exports; live profiles are 720p and 1080p')
        if a['renderer']!='godot' or a['seconds'] not in (8,16,24):raise ValueError('Live scenes require Godot and 8, 16 or 24 second chunks')
        # Each channel run owns only its transient files; preserve exported media.
        for p in folder(project).glob('chunk-*.ts'):p.unlink()
        (folder(project)/'channel.m3u8').unlink(missing_ok=True)
        s={'project':project,'desired':'stopped','status':'ready','recipe':a,'sequence':sequence,'seconds':a['seconds'],'generation':uuid.uuid4().hex,'next_index':0,'media_sequence':0,'publish_index':0,'ready':[],'playlist':[],'retired':[],'job':None,'next_publish':0,'underruns':0,'generated':0,'error':None}
        save(project,s);return status(project)

def control(project,action):
    with LOCK:
        s=read(project)
        if action not in ('start','stop'):raise ValueError('Use start or stop')
        if 'recipe' not in s:raise ValueError('Configure a scene first')
        s['desired']='running' if action=='start' else 'stopped'
        s['status']='buffering' if action=='start' else 'stopped'
        if action=='stop' and s.get('job'):
            engine.cancel(s['job'])
        if action=='start':s['next_publish']=time.time()+s['seconds']
        save(project,s)
        if s.get('playlist'):publish(project,s)
        return status(project)

def discard_job(jid):
    # Only this controller's explicitly transient jobs are eligible for cleanup.
    j=engine.get_job(jid)
    if not j.get('transient') or not (j.get('request_key') or '').startswith('scene-buffer:'):raise ValueError('Refusing to delete non-buffer media')
    if j['status'] not in ('succeeded','failed','cancelled'):return
    p=engine.STATE/'outputs'/jid
    shutil.rmtree(p,ignore_errors=True)
    with engine.db() as c:
        c.execute('DELETE FROM assets WHERE job=?',(jid,));c.execute('DELETE FROM jobs WHERE id=?',(jid,))

def duration(project,s,name):
    durations=s.setdefault('durations',{})
    if name not in durations:
        result=subprocess.run([basic.FFPROBE,'-v','error','-show_entries','format=duration','-of','json',str(folder(project)/name)],capture_output=True,text=True,check=True)
        durations[name]=float(json.loads(result.stdout)['format']['duration'])
    return durations[name]

def publish(project,s):
    lines=['#EXTM3U','#EXT-X-VERSION:3',f'#EXT-X-TARGETDURATION:{math.ceil(max([duration(project,s,n) for n in s["playlist"]] or [s["seconds"]]))}',f'#EXT-X-MEDIA-SEQUENCE:{s["media_sequence"]}',f'#EXT-X-DISCONTINUITY-SEQUENCE:{s["media_sequence"]}']
    for name in s['playlist']:lines+=['#EXT-X-DISCONTINUITY',f'#EXTINF:{duration(project,s,name):.6f},',name]
    if s.get('desired')=='stopped':lines.append('#EXT-X-ENDLIST')
    p=folder(project)/'channel.m3u8';tmp=p.with_suffix('.partial');tmp.write_text('\n'.join(lines)+'\n');tmp.replace(p)

def tick(project):
    with LOCK:
        s=read(project)
        if s.get('job'):
            j=engine.get_job(s['job'])
            if j['status']=='succeeded':
                name=f'chunk-{s["next_index"]:010d}.ts';target=folder(project)/name
                subprocess.run([basic.FFMPEG,'-hide_banner','-loglevel','error','-nostdin','-y','-i',j['result']['path'],'-ss','0','-c','copy','-bsf:v','h264_mp4toannexb','-f','mpegts',str(target)],check=True,timeout=60)
                duration(project,s,name);s['ready'].append(name);s['next_index']+=1;s['generated']+=1;s['last_render_seconds']=j['result']['seconds']
                finished=s['job'];s['job']=None;save(project,s);discard_job(finished)
            elif j['status'] in ('failed','cancelled'):
                s['error']=j.get('error');s['retry_after']=time.time()+15;finished=s['job'];s['job']=None;save(project,s);discard_job(finished)
        if s.get('desired')!='running':save(project,s);return
        if not s['playlist'] and len(s['ready'])>=2:
            s['playlist']=[s['ready'].pop(0),s['ready'].pop(0)];s['next_publish']=time.time()+duration(project,s,s['playlist'][-1]);s['status']='running';publish(project,s)
        elif s['playlist'] and time.time()>=s['next_publish']:
            if s['ready']:
                name=s['ready'].pop(0);s['status']='running'
            else:
                # A retained prepared segment fills latency without stopping HLS.
                name=f'chunk-filler-{s["publish_index"]:010d}.ts'
                if not (folder(project)/name).exists():os.link(folder(project)/s['playlist'][-1],folder(project)/name)
                s.setdefault('durations',{})[name]=duration(project,s,s['playlist'][-1]);s['underruns']+=1;s['status']='filler'
            s['playlist'].append(name);s['publish_index']+=1;s['next_publish']=time.time()+duration(project,s,name)
            if len(s['playlist'])>3:s['retired'].append(s['playlist'].pop(0));s['media_sequence']+=1
            while len(s['retired'])>3:
                old=s['retired'].pop(0);(folder(project)/old).unlink(missing_ok=True);s.get('durations',{}).pop(old,None)
            publish(project,s)
        if not s.get('job') and len(s['ready'])<3 and time.time()>=s.get('retry_after',0):
            args={**s['recipe'],'scene':s.get('sequence',[s['recipe']['scene']])[s['next_index']%len(s.get('sequence',[s['recipe']['scene']]))],'start_time':s['recipe']['start_time']+s['next_index']*s['seconds']}
            j=engine.submit('render_scene',args,start_worker=False,project=project,request_key=f'scene-buffer:{s["generation"]}:{s["next_index"]}',transient=True)
            s['job']=j['id']
        save(project,s)
        if s['playlist']:publish(project,s)

def reconcile():
    root=engine.STATE/'scene-channels'
    referenced=set()
    if root.exists():
        for p in root.glob('*/state.json'):
            s=json.loads(p.read_text())
            if s.get('job'):referenced.add(s['job'])
    with engine.db() as c:
        rows=c.execute("SELECT id FROM jobs WHERE transient=1 AND request_key LIKE 'scene-buffer:%'").fetchall()
    for row in rows:
        if row['id'] not in referenced:
            engine.cancel(row['id'])
            discard_job(row['id'])

def work(stop):
    with LOCK:reconcile()
    while not stop.is_set():
        root=engine.STATE/'scene-channels'
        if root.exists():
            for p in root.glob('*/state.json'):
                try:tick(p.parent.name)
                except Exception as exc:
                    with LOCK:
                        s=read(p.parent.name);s['error']=str(exc);save(p.parent.name,s)
        stop.wait(.5)
