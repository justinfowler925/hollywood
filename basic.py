"""Studio media department: persistent, serialized, explicitly supported media jobs."""
import json
import math
import os
from pathlib import Path
import subprocess

STATE = Path(os.environ.get('STUDIO_MEDIA_STATE', '/opt/hollywood/state'))
FFMPEG = '/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg'
FFPROBE = '/opt/homebrew/opt/ffmpeg-full/bin/ffprobe'
OPERATIONS = {
    'speak': 'Native macOS speech to an audio file; text required, voice optional.',
    'extract_audio': 'Extract a media file audio track to a 48 kHz WAV; source required.',
    'normalize_audio': 'Loudness normalization to -16 LUFS with optional FFT noise reduction; source required.',
    'trim_video': 'Re-encode a video excerpt to H.264/AAC MP4; source, start and duration required.',
    'render_title_video': 'Render a supplied still image with an audio track to H.264/AAC MP4; source image and audio required.',
}

def source_path(value):
    p = Path(str(value)).expanduser()
    if not p.is_absolute() or not p.is_file():
        raise ValueError('Source must be an existing absolute file path on the Studio')
    return str(p.resolve())

def validate(operation, args, deferred=False):
    if operation not in OPERATIONS:
        raise ValueError('Unsupported operation; inspect capabilities before submitting')
    allowed = {'speak': {'text','voice'}, 'extract_audio': {'source'},
               'normalize_audio': {'source','denoise'}, 'trim_video': {'source','start','duration'},
               'render_title_video': {'source','audio'}}[operation]
    if set(args) - allowed:
        raise ValueError('Unexpected parameters: ' + ', '.join(sorted(set(args)-allowed)))
    a = dict(args)
    if operation == 'speak':
        if not isinstance(a.get('text'), str) or not a['text'].strip() or len(a['text']) > 20000:
            raise ValueError('text must contain 1–20000 characters')
        if 'voice' in a and (not isinstance(a['voice'],str) or len(a['voice']) > 100):
            raise ValueError('Invalid voice')
    else:
        value = a.get('source', '')
        a['source'] = value if deferred and isinstance(value, str) and value.startswith('job:') else source_path(value)
    if operation == 'render_title_video':
        value = a.get('audio', '')
        a['audio'] = value if deferred and isinstance(value, str) and value.startswith('job:') else source_path(value)
    if operation == 'normalize_audio' and 'denoise' in a and not isinstance(a['denoise'], bool):
        raise ValueError('denoise must be true or false')
    if operation == 'trim_video':
        for key in ('start','duration'):
            value = float(a.get(key, -1))
            if not math.isfinite(value) or value < 0 or (key == 'duration' and not 0 < value <= 3600):
                raise ValueError('start must be nonnegative; duration must be 0–3600 seconds')
            a[key] = value
    return a

def probe(path):
    r = subprocess.run([FFPROBE,'-v','error','-show_format','-show_streams','-of','json',source_path(path)],
                       capture_output=True, text=True, timeout=60, check=True)
    return json.loads(r.stdout)

def run_job(row):
    a = validate(row['operation'],json.loads(row['args']))
    folder = STATE/'outputs'/row['id']
    folder.mkdir(parents=True,exist_ok=True)
    op = row['operation']
    out = folder/('result.mp4' if op in ('trim_video','render_title_video') else 'result.wav')
    base = [FFMPEG,'-hide_banner','-nostdin','-y','-threads','2']
    if op == 'speak':
        text_file = folder/'speech.txt'
        text_file.write_text(a['text'])
        cmd = ['/usr/bin/say','-f',str(text_file),'-o',str(folder/'speech.aiff')]
        if a.get('voice'): cmd += ['-v',a['voice']]
        with open(folder/'speech.log','w') as log:
            subprocess.run(cmd,stdout=log,stderr=log,timeout=600,check=True)
        cmd = base + ['-i',str(folder/'speech.aiff'),'-ar','48000',str(out)]
    elif op == 'extract_audio':
        cmd = base + ['-i',a['source'],'-map','0:a:0','-vn','-ar','48000',str(out)]
    elif op == 'normalize_audio':
        filters = ('afftdn=nf=-25,' if a.get('denoise') else '') + 'loudnorm=I=-16:TP=-1.5:LRA=11'
        cmd = base + ['-i',a['source'],'-map','0:a:0','-vn','-af',filters,'-ar','48000',str(out)]
    elif op == 'trim_video':
        cmd = base + ['-ss',str(a['start']),'-i',a['source'],'-t',str(a['duration']),
                      '-map','0:v:0','-map','0:a:0?','-c:v','libx264','-threads','2',
                      '-pix_fmt','yuv420p','-c:a','aac','-movflags','+faststart',str(out)]
    else:
        cmd = base + ['-loop','1','-i',a['source'],'-i',a['audio'],'-map','0:v:0','-map','1:a:0',
                      '-vf','scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2',
                      '-c:v','libx264','-threads','2','-tune','stillimage','-r','24','-pix_fmt','yuv420p',
                      '-c:a','aac','-shortest','-movflags','+faststart',str(out)]
    with open(folder/'render.log','w') as log:
        subprocess.run(cmd,stdin=subprocess.DEVNULL,stdout=log,stderr=log,timeout=7200,check=True)
    meta = probe(str(out))
    if float(meta['format'].get('duration',0)) <= 0:
        raise RuntimeError('Output has no positive duration')
    return {'path':str(out),'bytes':out.stat().st_size,'probe':meta}
