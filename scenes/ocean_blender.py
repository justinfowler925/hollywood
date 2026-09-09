"""Studio-owned procedural set. Run only through scene_renderer's bounded worker.
Uses Blender's Ocean modifier and EEVEE; no copied corpus code or external assets.
"""
import bpy, json, math, random, sys, time, subprocess
from pathlib import Path
from mathutils import Vector
args=json.loads(Path(sys.argv[sys.argv.index('--')+1]).read_text())
folder=Path(args['folder']); a=args['args']; random.seed(a['seed'])
bpy.ops.wm.read_factory_settings(use_empty=True)
s=bpy.context.scene
s.render.engine='BLENDER_EEVEE'
s.render.resolution_x=a['width']; s.render.resolution_y=a['height']; s.render.resolution_percentage=100
s.render.fps=24
s.eevee.taa_render_samples=32
s.eevee.use_raytracing=True
s.render.image_settings.file_format='PNG'; s.render.image_settings.color_mode='RGB'
s.view_settings.view_transform='AgX'
s.world=bpy.data.worlds.new('Maritime sky'); s.world.use_nodes=True
nodes=s.world.node_tree.nodes; links=s.world.node_tree.links
sky=nodes.new('ShaderNodeTexSky'); sky.sun_elevation=math.radians(42); sky.sun_rotation=math.radians(130)
nodes.get('Background').inputs['Color'].default_value=(.18,.42,.8,1); nodes.get('Background').inputs['Strength'].default_value=.6

def material(name,color,roughness=.5,metal=0):
 m=bpy.data.materials.new(name); m.use_nodes=True
 p=m.node_tree.nodes.get('Principled BSDF'); p.inputs['Base Color'].default_value=(*color,1)
 p.inputs['Roughness'].default_value=roughness; p.inputs['Metallic'].default_value=metal
 return m
water=material('Turquoise water',(.008,.065,.075),.13,.05)
p=water.node_tree.nodes.get('Principled BSDF'); p.inputs['IOR'].default_value=1.333
n=water.node_tree.nodes; l=water.node_tree.links
noise=n.new('ShaderNodeTexNoise'); noise.inputs['Scale'].default_value=5; noise.inputs['Detail'].default_value=3
tex=n.new('ShaderNodeTexCoord'); l.new(tex.outputs['Object'],noise.inputs['Vector'])
bump=n.new('ShaderNodeBump'); bump.inputs['Strength'].default_value=.3; bump.inputs['Distance'].default_value=.1
l.new(noise.outputs['Fac'],bump.inputs['Height']); l.new(bump.outputs['Normal'],p.inputs['Normal'])
bpy.ops.mesh.primitive_plane_add(size=2)
o=bpy.context.object; o.name='Animated FFT ocean'; o.data.materials.append(water)
m=o.modifiers.new('Wind driven deep water','OCEAN'); m.geometry_mode='GENERATE'; m.resolution=9; m.viewport_resolution=9
m.spatial_size=80; m.size=1; m.repeat_x=5; m.repeat_y=5; m.wave_scale=a['wave_height']; m.choppiness=1.2; m.wind_velocity=18
for poly in o.data.polygons:poly.use_smooth=True
# Horizon surface covers beyond the simulated patch.
bpy.ops.mesh.primitive_plane_add(size=10000,location=(0,0,-.25)); bpy.context.object.data.materials.append(water)
bpy.ops.object.light_add(type='SUN',location=(0,0,80)); sun=bpy.context.object
sun.rotation_euler=(.4,-.5,-.4); sun.data.energy=4; sun.data.angle=.08
# Volumetric ellipsoids form slowly advecting, genuinely three-dimensional clouds.
cloud=bpy.data.materials.new('Cloud water droplets'); cloud.use_nodes=True
n=cloud.node_tree.nodes; n.clear(); out=n.new('ShaderNodeOutputMaterial'); vol=n.new('ShaderNodeVolumePrincipled')
noise=n.new('ShaderNodeTexNoise'); noise.inputs['Scale'].default_value=3.5; noise.inputs['Detail'].default_value=3
ramp=n.new('ShaderNodeValToRGB'); ramp.color_ramp.elements[0].position=.35; ramp.color_ramp.elements[1].position=.7
ramp.color_ramp.elements[1].color=(.12,.12,.12,1)
cloud.node_tree.links.new(noise.outputs['Fac'],ramp.inputs[0]); cloud.node_tree.links.new(ramp.outputs[0],vol.inputs['Density']); vol.inputs['Color'].default_value=(.95,.97,1,1); vol.inputs['Emission Strength'].default_value=.04; vol.inputs['Emission Color'].default_value=(.65,.75,1,1)
cloud.node_tree.links.new(vol.outputs['Volume'],out.inputs['Volume'])
clouds=[]
for i in range(12):
 x=random.uniform(-160,160); y=random.uniform(90,220); z=random.uniform(27,45)
 bpy.ops.mesh.primitive_uv_sphere_add(segments=16,ring_count=8,location=(x,y,z))
 c=bpy.context.object; c.name='Drifting cloud'; c.scale=(random.uniform(14,26),9,random.uniform(3,6)); c.data.materials.append(cloud)
 clouds.append((c,x))
bpy.ops.object.camera_add(location=(0,-35,3.8)); cam=bpy.context.object
cam.rotation_euler=(Vector((0,70,5))-cam.location).to_track_quat('-Z','Y').to_euler(); cam.data.lens=32; cam.data.clip_end=20000
s.camera=cam
frame=folder/'frame.png'; s.render.filepath=str(frame)
start=time.monotonic()
# The saved recipe is the portable set; frames are encoded then immediately discarded.
frames=a.get('frames',round(a['seconds']*24))
encoder=None
try:
 if frames>1:
  encoder=subprocess.Popen([args['ffmpeg'],'-hide_banner','-loglevel','error','-y','-f','image2pipe','-framerate','24','-vcodec','png','-i','pipe:0','-an','-c:v','h264_videotoolbox','-b:v','8M','-pix_fmt','yuv420p','-movflags','+faststart',str(folder/'picture.mp4')],stdin=subprocess.PIPE)
 for f in range(frames):
  t=a['start_time']+f/24
  m.time=t
  for c,x in clouds:c.location.x=x+t*a['cloud_speed']
  bpy.ops.render.render(write_still=True)
  if encoder:encoder.stdin.write(frame.read_bytes()); frame.unlink()
  print(json.dumps({'frame':f+1,'total':frames,'elapsed':round(time.monotonic()-start,2)}),flush=True)
 if encoder:
  encoder.stdin.close()
  if encoder.wait()!=0:raise RuntimeError('Scene encoder failed')
 (folder/'render.json').write_text(json.dumps({'renderer':bpy.app.version_string,'frames':frames,'wall_seconds':time.monotonic()-start,'recipe':a},indent=2))
finally:
 if encoder and encoder.poll() is None:encoder.terminate(); encoder.wait()
