"""Durable, single-channel playout. Copies pre-encoded programs; no GPU inference."""
from __future__ import annotations
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import time
from urllib.parse import urlsplit
import engine
import basic


def init():
    with engine.db() as c:c.execute('''CREATE TABLE IF NOT EXISTS channels(
      project TEXT PRIMARY KEY, asset TEXT NOT NULL, desired TEXT DEFAULT 'stopped',
      status TEXT DEFAULT 'stopped', pid INTEGER, attempts INTEGER DEFAULT 0,
      error TEXT, updated REAL, external INTEGER DEFAULT 0)''')
    with engine.db() as c:
        c.execute('BEGIN IMMEDIATE')
        cols={r[1] for r in c.execute('PRAGMA table_info(channels)')}
        for field in ('start_at','stop_at'):
            if field not in cols:c.execute(f'ALTER TABLE channels ADD COLUMN {field} REAL')
        if 'playlist' not in cols:c.execute("ALTER TABLE channels ADD COLUMN playlist TEXT")

def folder(project):
    engine.project_exists(project)
    p=engine.STATE/'broadcasts'/project;p.mkdir(parents=True,exist_ok=True)
    return p

def status(project='studio'):
    init();engine.project_exists(project)
    with engine.db() as c:r=c.execute('SELECT * FROM channels WHERE project=?',(project,)).fetchone()
    if not r:return {'project':project,'status':'unconfigured','desired':'stopped','external':False}
    result=dict(r);result.pop('pid',None)
    result['asset_ids']=json.loads(result.pop('playlist') or '[]') or [result['asset']]
    result['preview_url']=f'/api/broadcast/{project}/live/channel.m3u8'
    result['program_url']='/api/assets/'+result['asset']
    return result

def configure(project:str,asset_id:str,endpoint:str|None=None,start_at:float|None=None,stop_at:float|None=None,asset_ids:list[str]|None=None):
    if not isinstance(asset_id,str):raise ValueError('Program asset ID must be text')
    init();a=engine.asset(asset_id,project)
    ids=asset_ids if asset_ids is not None else [asset_id]
    if not isinstance(ids,list) or not 1<=len(ids)<=12 or any(not isinstance(x,str) for x in ids) or len(set(ids))!=len(ids) or ids[0]!=asset_id:raise ValueError('Choose 1–12 distinct prepared programs; asset_id must be the first')
    profiles=set()
    for aid in ids:
        item=engine.asset(aid,project)
        job=engine.get_job(item['job']) if item['job'] else None
        if not job or job['operation']!='prepare_broadcast' or job['status']!='succeeded' or item['kind']!='video':raise ValueError('Every filler must be a completed broadcast program')
        profiles.add(job['args'].get('resolution','1080p') if job['args'].get('scene')=='ocean' else '720p')
    if len(profiles)>1:raise ValueError('Programs in a rotation must have the same resolution; choose only 720p, 1080p or 4K programs')
    import math
    for value in (start_at,stop_at):
        if value is not None and (not isinstance(value,(int,float)) or not math.isfinite(value) or value<0):raise ValueError('Invalid schedule time')
    if stop_at is not None and stop_at<=max(time.time(),start_at or 0):raise ValueError('Stop time must follow start time and be in the future')
    with engine.db() as c:
        c.execute('BEGIN IMMEDIATE')
        r=c.execute('SELECT * FROM channels WHERE project=?',(project,)).fetchone()
        if r and (r['desired']=='running' or r['pid']):raise ValueError('Stop the broadcast before replacing its program or destination')
        # A prepared output is normalized, has both streams and closed two-second GOPs.
        if not a['job'] or engine.get_job(a['job'])['operation']!='prepare_broadcast':raise ValueError('Select a completed Build broadcast program result')
        job=engine.get_job(a['job'])
        if job['status']!='succeeded' or a['kind']!='video':raise ValueError('Program is not complete')
        p=folder(project)/'destination.json'
        if endpoint is not None:
            if not isinstance(endpoint,str):raise ValueError('Destination must be text')
            if endpoint:
                try:u=urlsplit(endpoint);port=u.port
                except ValueError:raise ValueError('Invalid destination URL')
                if u.scheme not in ('rtmp','rtmps') or not u.hostname or not u.path.strip('/') or len(endpoint)>2000 or any(x in endpoint for x in "|[]'\\\r\n "):
                    raise ValueError('Use a complete RTMP or RTMPS ingest URL with stream key')
            # Never return the ingest secret in API state, logs, plans or receipts.
            temp=p.with_suffix('.partial')
            fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
            with os.fdopen(fd,'w') as f:json.dump({'endpoint':endpoint},f)
            os.chmod(temp,0o600);temp.replace(p)
        external=bool(json.loads(p.read_text()).get('endpoint')) if p.exists() else False
        c.execute('INSERT INTO channels(project,asset,updated,external) VALUES(?,?,?,?) ON CONFLICT(project) DO UPDATE SET asset=excluded.asset,external=excluded.external,error=NULL,updated=excluded.updated',(project,asset_id,time.time(),int(external)))
        c.execute('UPDATE channels SET start_at=?,stop_at=?,playlist=? WHERE project=?',(start_at,stop_at,json.dumps(ids),project))
    return status(project)

def control(project,action):
    init()
    if action not in ('start','stop'):raise ValueError('Action must be start or stop')
    with engine.db() as c:
        c.execute('BEGIN IMMEDIATE')
        r=c.execute('SELECT * FROM channels WHERE project=?',(project,)).fetchone()
        if not r:raise ValueError('Build and save a program first')
        if action=='start':
            if r['stop_at'] and r['stop_at']<=time.time():raise ValueError('Save a new schedule before restarting this program')
            other=c.execute("SELECT project FROM channels WHERE desired='running' AND project!=?",(project,)).fetchone()
            if other:raise ValueError('Stop the active channel before starting another')
        c.execute('UPDATE channels SET desired=?,error=NULL,updated=? WHERE project=?',('running' if action=='start' else 'stopped',time.time(),project))
    return status(project)

def update(project,**values):
    with engine.db() as c:c.execute('UPDATE channels SET '+','.join(k+'=?' for k in values)+',updated=? WHERE project=?',(*values.values(),time.time(),project))

def kill(pid):
    try:os.killpg(pid,signal.SIGTERM)
    except ProcessLookupError:return

def command(project,source,endpoint):
    p=folder(project)
    outputs=f'[f=hls:hls_time=2:hls_list_size=6:hls_flags=delete_segments+omit_endlist:hls_start_number_source=epoch]{p}/channel.m3u8'
    if endpoint:outputs+='|[f=flv:onfail=abort]'+endpoint
    return [basic.FFMPEG,'-v','error','-nostdin','-re','-stream_loop','-1',*(['-f','concat','-safe','1'] if source.endswith('.ffconcat') else []),'-i',source,'-map','0:v:0','-map','0:a:0','-c','copy','-tag:v','7','-tag:a','10','-progress',str(p/'progress.txt'),'-f','tee',outputs]

def work(stop):
    init()
    with open(engine.STATE/'broadcast-worker.lock','a') as lease:
        try:fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        with engine.db() as c:rows=c.execute('SELECT * FROM channels').fetchall()
        for r in rows:
            if r['pid']:
                cmd=subprocess.run(['ps','-p',str(r['pid']),'-o','command='],capture_output=True,text=True).stdout
                if str(folder(r['project'])/'progress.txt') in cmd:kill(r['pid'])
            update(r['project'],pid=None,status='recovering' if r['desired']=='running' else 'stopped')
        active={};retry_at={};failures={}
        try:
            while not stop.is_set():
                with engine.db() as c:rows=c.execute('SELECT * FROM channels').fetchall()
                for row in rows:
                    project=row['project'];p=active.get(project)
                    if row['desired']=='running' and row['stop_at'] and time.time()>=row['stop_at']:
                        control(project,'stop');continue
                    if p and row['desired']=='stopped':
                        kill(p.pid)
                        try:p.wait(timeout=5)
                        except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait()
                        active.pop(project);update(project,pid=None,status='stopped');retry_at.pop(project,None);continue
                    if p and p.poll() is not None:
                        active.pop(project);n=failures.get(project,0)+1;failures[project]=n
                        retry_at[project]=time.monotonic()+min(60,2**min(n,6))
                        update(project,pid=None,status='reconnecting',error='Stream stopped; reconnecting. Check destination and network.');p=None
                    if p:
                        progress=folder(project)/'progress.txt'
                        age=time.time()-progress.stat().st_mtime if progress.exists() else 999
                        if age<10 and (folder(project)/'channel.m3u8').exists():update(project,status='running')
                        elif time.time()-row['updated']>30:
                            kill(p.pid)
                        continue
                    if row['desired']!='running':
                        if row['status']!='stopped':update(project,status='stopped')
                        continue
                    if row['start_at'] and time.time()<row['start_at']:
                        update(project,status='scheduled');continue
                    if time.monotonic()<retry_at.get(project,0):continue
                    try:
                        ids=json.loads(row['playlist'] or '[]') or [row['asset']]
                        lines=['ffconcat version 1.0']
                        for i,aid in enumerate(ids):
                            source=engine.asset(aid,project)['path']
                            if not Path(source).is_file():raise ValueError('Saved program is missing')
                            link=folder(project)/f'program-{i}.mp4';link.unlink(missing_ok=True);os.link(source,link)
                            lines.append(f"file 'program-{i}.mp4'")
                        for stale in folder(project).glob('program-*.mp4'):
                            if int(stale.stem.split('-')[1])>=len(ids):stale.unlink()
                        listing=folder(project)/'programs.ffconcat';listing.write_text('\n'.join(lines)+'\n');source=str(listing)
                        dest=folder(project)/'destination.json'
                        endpoint=json.loads(dest.read_text()).get('endpoint','') if dest.exists() else ''
                        progress=folder(project)/'progress.txt';progress.unlink(missing_ok=True)
                        (folder(project)/'channel.m3u8').unlink(missing_ok=True)
                        for stale in folder(project).glob('channel*.ts'):stale.unlink(missing_ok=True)
                        # Secret URLs appear in FFmpeg errors; discard stderr and expose safe status only.
                        p=subprocess.Popen(command(project,source,endpoint),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
                        active[project]=p;update(project,pid=p.pid,status='starting',attempts=row['attempts']+1,error=None)
                    except Exception:
                        retry_at[project]=time.monotonic()+30;update(project,status='reconnecting',error='Unable to start saved program; retrying in 30 seconds')
                stop.wait(0.5)
        finally:
            for project,p in active.items():
                kill(p.pid)
                try:p.wait(timeout=5)
                except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait()
                update(project,pid=None,status='recovering')
