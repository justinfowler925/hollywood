"""Isolated ACE-Step runtime; its Transformers pins differ from audio/video MLX."""
import json
import os
from pathlib import Path
import shutil
import sys
import torch
torch.set_num_threads(4)
os.environ['ACESTEP_CHECKPOINTS_DIR']='/opt/hollywood/music-models/checkpoints'
from acestep.handler import AceStepHandler
from acestep.llm_inference import LLMHandler
from acestep.inference import GenerationParams,GenerationConfig,generate_music

request=json.loads(Path(sys.argv[1]).read_text());a=request['args'];folder=Path(request['folder'])
handler=AceStepHandler()
message,ok=handler.initialize_service(project_root='/opt/hollywood/music-models',
    config_path='acestep-v15-xl-turbo',device='mps',use_mlx_dit=True,compile_model=False)
if not ok:raise RuntimeError(message)
params=GenerationParams(caption=a['prompt'],lyrics=a.get('lyrics',''),instrumental=not bool(a.get('lyrics')),duration=float(a.get('seconds',10)),inference_steps=8,seed=a.get('seed',42),thinking=False,**({'bpm':a['bpm']} if 'bpm' in a else {}),**({'keyscale':a['key']} if 'key' in a else {}))
config=GenerationConfig(batch_size=1,use_random_seed=False,seeds=[a.get('seed',42)],audio_format='wav')
result=generate_music(handler,LLMHandler(),params,config,save_dir=str(folder/'music'))
if not result.success or not result.audios:raise RuntimeError(result.error or result.status_message)
shutil.copyfile(result.audios[0]['path'],folder/'result.wav')
print('Music output saved',flush=True)
