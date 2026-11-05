"""Own lightweight queue/scheduler children when no system services exist.

Healthy external services are reused, never killed. Control processes do not
load models. Actual inference still requires the shared GPU lease.
"""
from __future__ import annotations
import atexit
import fcntl
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
import urllib.request
from avatar_paths import data_dir, assets_dir, socket_path, gpu_lock

_lock = threading.RLock()
_children = []

def _spawn(argv, env, name):
    from core.contained_subprocess import spawn_owned
    logs = data_dir()/'avatar-runtime'
    logs.mkdir(parents=True, exist_ok=True)
    stream = (logs/(name+'.log')).open('a')
    try:
        proc = spawn_owned([str(x) for x in argv], stdout=stream, stderr=stream, env=env)
        _children.append(proc)
        return proc
    finally:
        stream.close()

def stop_owned():
    for proc in reversed(_children):
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except Exception:
                proc.kill()

atexit.register(stop_owned)

def ensure_scheduler():
    import gpu_client
    path = Path(gpu_client.SOCKET)
    with _lock:
        try:
            with socket.socket(socket.AF_UNIX) as sock:
                sock.settimeout(2)
                sock.connect(str(path))
            return
        except (FileNotFoundError, ConnectionRefusedError):
            pass
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.with_suffix('.startup.lock').open('a') as startup:
            fcntl.flock(startup, fcntl.LOCK_EX)
            try:
                with socket.socket(socket.AF_UNIX) as sock:
                    sock.settimeout(2)
                    sock.connect(str(path))
                return
            except (FileNotFoundError, ConnectionRefusedError):
                pass
            env = dict(os.environ, AVATAR_GPU_SOCKET=str(path), AVATAR_GPU_LOCK=str(gpu_lock()),
                       AVATAR_GPU_STATE=str(data_dir()/'avatar-runtime/gpu-state.json'))
            registered = data_dir()/'avatar-runtime/voice-service-registered'
            registered.parent.mkdir(parents=True, exist_ok=True)
            registered.touch()
            proc = _spawn([sys.executable, Path(__file__).with_name('gpu_scheduler.py')], env, 'scheduler')
            for _ in range(100):
                if proc.poll() is not None:
                    raise RuntimeError('GPU scheduler failed to start; see avatar-runtime/scheduler.log')
                try:
                    with socket.socket(socket.AF_UNIX) as sock:
                        sock.settimeout(.2)
                        sock.connect(str(path))
                    return
                except (FileNotFoundError, ConnectionRefusedError):
                    time.sleep(.1)
            raise RuntimeError('GPU scheduler did not become ready')

def queue_url():
    return os.environ.get('AVATAR_QUEUE_URL', 'http://127.0.0.1:8765').rstrip('/')

def _identity():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(queue_url()+'/api/runtime', timeout=3) as response:
            return json.load(response)
    except Exception:
        return None

def ensure_queue():
    from services import video_backend
    from urllib.parse import urlparse
    root = video_backend.root().resolve()
    expected = str(root)
    with _lock:
        identity = _identity()
        if identity:
            if identity.get('root') != expected or identity.get('assets') != str(assets_dir().resolve()):
                raise RuntimeError('Another digital-human queue uses this port; it was left unchanged.')
            return
        if not video_backend.list_backends()[0]['available']:
            raise RuntimeError('Install TBDub Student from Settings > Models first.')
        url = urlparse(queue_url())
        if url.hostname != '127.0.0.1' or not url.port or url.path:
            raise RuntimeError('Managed queue URL must be an IPv4 loopback URL with a port.')
        ensure_scheduler()
        env = dict(os.environ, OMNIVOICE_DATA_DIR=str(data_dir()), OMNIVOICE_TBDUB_DIR=expected,
                   OMNIVOICE_TBDUB_PYTHON=str(video_backend.python()), AVATAR_ASSETS_ROOT=str(assets_dir()),
                   AVATAR_INTEGRATION_DIR=str(Path(__file__).parent),
                   AVATAR_GPU_SOCKET=str(socket_path()), AVATAR_GPU_LOCK=str(gpu_lock()),
                   AVATAR_GPU_COORDINATION_ENABLED='1', TBDUB_WEB_HOST='127.0.0.1',
                   TBDUB_WEB_PORT=str(url.port),
                   PYTHONPATH=str(Path(__file__).parent)+os.pathsep+str(Path(__file__).resolve().parents[3]),
                   PATH=str(root/'bin')+os.pathsep+os.environ.get('PATH',''))
        assets_dir().mkdir(parents=True, exist_ok=True)
        proc = _spawn([video_backend.python(), root/'webui/app.py'], env, 'queue')
        for _ in range(150):
            if proc.poll() is not None:
                raise RuntimeError('Digital-human queue failed to start; see avatar-runtime/queue.log')
            identity = _identity()
            if identity and identity.get('root') == expected:
                return
            time.sleep(.1)
        raise RuntimeError('Digital-human queue did not become ready')
