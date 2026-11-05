"""Shared paths for the web bridge, queue and GPU arbiter."""
import os
from pathlib import Path

def data_dir():
    return Path(os.environ.get('OMNIVOICE_DATA_DIR', str(Path.home()/'.omnivoice')))

def assets_dir():
    return Path(os.environ.get('AVATAR_ASSETS_ROOT', str(data_dir()/'avatar-assets')))

def socket_path():
    explicit = os.environ.get('AVATAR_GPU_SOCKET')
    if explicit:
        return Path(explicit)
    legacy = Path('/run/avatar-gpu/scheduler.sock')
    return legacy if legacy.is_socket() else data_dir()/'avatar-runtime/gpu.sock'

def gpu_lock():
    return Path(os.environ.get('AVATAR_GPU_LOCK', str(data_dir()/'avatar-runtime'/'student.lock')))

def smi_path():
    import shutil
    return shutil.which('nvidia-smi') or '/usr/lib/wsl/lib/nvidia-smi'

def voice_url():
    fallback = f"http://127.0.0.1:{os.environ.get('OMNIVOICE_PORT', '3901')}"
    return os.environ.get('AVATAR_VOICE_URL', fallback).rstrip('/')
