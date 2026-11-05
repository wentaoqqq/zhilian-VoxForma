"""Video registry. File-only inspection: never downloads or loads tensor weights."""
from pathlib import Path
import os

def root():
    from services.video_install import active_install, candidate
    explicit = os.environ.get('OMNIVOICE_TBDUB_DIR', '').strip()
    return Path(explicit).expanduser() if explicit else (active_install() or candidate())

def python():
    explicit = os.environ.get('OMNIVOICE_TBDUB_PYTHON', '').strip()
    return Path(explicit).expanduser() if explicit else root() / '.venv/bin/python'

def active_backend_id():
    return 'tbdub-student'

def model_rows():
    from services.video_install import manifest, valid
    specs = manifest()['files']
    rows = []
    for role, label in [('student','TBDub Student'), ('vae','Wan2.2 VAE'),
                        ('hubert','HuBERT large'), ('face','Face Landmarker'),
                        ('prompt','Null prompt'), ('config','Runtime configuration'),
                        ('rife','RIFE v4.25')]:
        files = [s for s in specs if s['role'] == role]
        installed = bool(files) and all(valid(root()/'checkpoints'/s['path'], s, checksum=False) for s in files)
        paths = [root()/'checkpoints'/s['path'] for s in files]
        size = sum(p.stat().st_size for p in paths if p.is_file())
        rows.append({'repo_id':'local/tbdub-'+role, 'label':label, 'role':'Video',
                     'engines':['tbdub-student'], 'families':['video'], 'required':False,
                     'required_for_generation':role!='rife', 'installed':installed,
                     'supported':os.name=='posix', 'size_gb':round(size/1024**3,3),
                     'download_size_bytes':sum(s['size'] for s in files),
                     'size_on_disk_bytes':size, 'nb_files':sum(p.is_file() for p in paths),
                     'source':'managed', 'managed':True, 'downloadable':False,
                     'relative_path':'checkpoints/'+files[0]['path']})
    return rows

def list_backends():
    from services.video_install import supported
    rows = model_rows()
    missing = [m['label'] for m in rows if m['required_for_generation'] and not m['installed']]
    code = all((root()/p).is_file() for p in ('inference.py','webui/unified_jobs.py','runtime_config.py'))
    runtime = python().is_file() and ((root()/'.tbdub-install.json').is_file() or
                                    (python().parents[1]/'voicestudio-runtime-verified.json').is_file())
    host_ok, reason = supported()
    ready = host_ok and code and runtime and not missing
    return [{'id':'tbdub-student', 'display_name':'TBDub Student', 'available':ready,
             'reason':None if ready else (reason or 'Install the engine runtime and model weights'),
             'isolation_mode':'subprocess', 'gpu_compat':['cuda'], 'effective_device':'cuda',
             'routing_status':'accelerated', 'supports_cloning':False,
             'supports_voice_conversion':False, 'weights_installed':not missing,
             'runtime_installed':runtime, 'source_installed':code, 'missing_models':missing,
             'models':rows, 'native_fps':25, 'output_fps':[25,60] if rows[-1]['installed'] else [25],
             'inference_steps':2, 'streaming':True, 'managed':True,
             'one_click_install':host_ok, 'lifecycle':'per-job', 'quality_preserved':True}]

def family_payload():
    return {'active':active_backend_id(), 'active_model':'local/tbdub-student',
            'env_override':bool(os.environ.get('OMNIVOICE_VIDEO_BACKEND')), 'backends':list_backends()}
