"""Shared Unix-socket GPU admission protocol. No torch dependency."""
from __future__ import annotations
import contextvars, json, os, socket, uuid, asyncio, functools

from avatar_paths import socket_path
SOCKET = str(socket_path())
current_lease = contextvars.ContextVar('avatar_gpu_lease', default=None)

def rpc(action, *, timeout=60, **payload):
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(timeout)
        try:sock.connect(SOCKET)
        except (FileNotFoundError, ConnectionRefusedError):
            if os.environ.get('AVATAR_GPU_AUTOSTART') != '1':raise
            from video_runtime import ensure_scheduler
            ensure_scheduler();sock.connect(SOCKET)
        stream=sock.makefile('rwb')
        stream.write(json.dumps(dict(action=action, **payload)).encode()+b'\n');stream.flush()
        raw=stream.readline(1024*1024)
        if not raw:raise RuntimeError('GPU scheduler disconnected')
        response=json.loads(raw)
        if not response.get('ok'):raise RuntimeError(response.get('error','GPU admission failed'))
        return response

def require_lease():
    lease=current_lease.get()
    if not lease:raise RuntimeError('GPU work requires an active global lease')
    rpc('validate',token=lease['token'],timeout=5)
    return lease

async def acquire_async(owner,kind='voice'):
    task=asyncio.create_task(asyncio.to_thread(rpc,'acquire',owner=owner,kind=kind,pid=os.getpid(),request_id=uuid.uuid4().hex,timeout=21660))
    try:return await asyncio.shield(task)
    except asyncio.CancelledError:
        async def reap():
            try:
                value=await task
                await asyncio.to_thread(rpc,'release',token=value['token'],timeout=150)
            except Exception:pass
        asyncio.create_task(reap())
        raise

def async_job(fn):
    @functools.wraps(fn)
    async def wrapped(*args,**kwargs):
        value=await acquire_async('voicestudio:background:'+uuid.uuid4().hex[:10])
        context=current_lease.set(value)
        try:return await fn(*args,**kwargs)
        finally:
            try:await asyncio.shield(asyncio.to_thread(rpc,'release',token=value['token'],timeout=150))
            finally:current_lease.reset(context)
    return wrapped

class Lease:
    def __init__(self, owner, kind='voice'):
        self.owner=owner;self.kind=kind;self.value=None
    def __enter__(self):
        self.value=rpc('acquire',timeout=21660,owner=self.owner,kind=self.kind,pid=os.getpid(),request_id=uuid.uuid4().hex)
        self.context=current_lease.set(self.value)
        return self
    def __exit__(self,*exc):
        try:rpc('release',token=self.value['token'],timeout=150)
        finally:current_lease.reset(self.context)
    def clear_voice(self):
        return rpc('clear_voice',token=self.value['token'],timeout=150)
