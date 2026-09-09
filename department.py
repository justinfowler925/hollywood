"""CLI and MCP facade for the Hollywood."""
import argparse
import json
import engine
import broadcast
import scene_channel
from engine import STATE,OPERATIONS,db,validate,submit,get_job,work,capabilities
from basic import probe,FFMPEG,FFPROBE

def serve():
    from mcp.server.fastmcp import FastMCP
    engine.init();app=FastMCP('Hollywood')
    def scene_api(path,data=None):
        import urllib.request
        request=urllib.request.Request('http://127.0.0.1:8960'+path,data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json'})
        return json.load(urllib.request.urlopen(request,timeout=15))
    @app.tool()
    def get_scene_channel(project:str='studio')->dict:
        return scene_api('/api/projects/'+project+'/scene-channel')
    @app.tool()
    def configure_scene_channel(project:str,parameters:dict)->dict:
        """Save a private visual channel. audio must be a project asset ID."""
        return scene_api('/api/projects/'+project+'/scene-channel',parameters)
    @app.tool()
    def control_scene_channel(project:str,action:str)->dict:
        return scene_api('/api/projects/'+project+'/scene-channel/'+action,{})
    app.tool(name='studio_media_capabilities')(capabilities)
    app.tool(name='get_studio_broadcast')(broadcast.status)
    app.tool(name='configure_studio_broadcast')(broadcast.configure)
    app.tool(name='control_studio_broadcast')(broadcast.control)
    app.tool(name='inspect_studio_media')(probe)
    app.tool(name='get_studio_media_job')(get_job)
    app.tool(name='cancel_studio_media_job')(engine.cancel)
    app.tool(name='retry_studio_media_job')(engine.retry)
    app.tool(name='list_studio_media_projects')(engine.projects)
    app.tool(name='create_studio_media_project')(engine.create_project)
    app.tool(name='get_studio_media_project')(engine.snapshot)
    @app.tool()
    def submit_studio_media_job(operation:str,parameters:dict,project:str='studio',request_key:str|None=None)->dict:
        """Submit a media job. Paths refer to Studio files. Poll its returned ID."""
        return submit(operation,parameters,project=project,request_key=request_key)
    app.run(transport='stdio')

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['capabilities','inspect','submit','job','work','serve','cancel','retry','projects','project'])
    parser.add_argument('value',nargs='?');parser.add_argument('--parameters',default='{}');parser.add_argument('--project',default='studio');parser.add_argument('--request-key')
    args=parser.parse_args();engine.init()
    if args.command=='work':work()
    elif args.command=='serve':serve()
    else:
        result={'capabilities':lambda:capabilities(),'inspect':lambda:probe(args.value),
          'submit':lambda:submit(args.value,json.loads(args.parameters),project=args.project,request_key=args.request_key),
          'job':lambda:get_job(args.value),'cancel':lambda:engine.cancel(args.value),
          'retry':lambda:engine.retry(args.value),'projects':engine.projects,
          'project':lambda:engine.snapshot(args.value or 'studio')}[args.command]()
        print(json.dumps(result,indent=2))
