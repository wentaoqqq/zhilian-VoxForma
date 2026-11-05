"""Single-GPU arbiter. Existing TBDub queue remains the video job database."""
from __future__ import annotations
import collections, fcntl, json, os, secrets, socketserver, struct, threading, time, urllib.request, urllib.error
from pathlib import Path

from avatar_paths import socket_path, gpu_lock, data_dir, voice_url
SOCKET=socket_path()
LOCK=gpu_lock()
STATE=Path(os.environ.get('AVATAR_GPU_STATE',str(data_dir()/'avatar-runtime'/'gpu-state.json')))
LOCK.parent.mkdir(parents=True,exist_ok=True)
STATE.parent.mkdir(parents=True,exist_ok=True)
VOICE=voice_url()

def start_id(pid):
    try:return Path('/proc/sys/kernel/random/boot_id').read_text().strip()+':'+Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()[19]
    except OSError:return None

class Arbiter:
    def __init__(self):
        self.condition=threading.Condition();self.owner=None;self.pending=collections.deque();self.fd=None
        self.cleanup_lock=threading.RLock();self.recovering=False
        self.blocked=None
        if STATE.exists():
            old=json.loads(STATE.read_text())
            if old.get('active'):
                self.owner={k:v for k,v in old.items() if k not in ('active','blocked','time')}
                self.owner['token']=secrets.token_urlsafe(32)
                self.recovering=True
                self.blocked='Waiting for previous GPU owner to finish or exit'
                self.fd=LOCK.open('a')
                try:fcntl.flock(self.fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:pass
        threading.Thread(target=self.watchdog,daemon=True,name='gpu-owner-recovery').start()
    def watchdog(self):
        while True:
            time.sleep(1)
            with self.condition:
                owner=dict(self.owner) if self.owner else None
                recover=(owner is not None and (start_id(owner['pid'])!=owner['start_id'] or (self.blocked and not self.recovering)))
            if recover:
                try:self.release(owner['token'])
                except Exception:pass  # Retain ownership until cleanup succeeds.
    def persist(self):
        data={'active':bool(self.owner),'blocked':self.blocked,'time':time.time()}
        if self.owner:data.update({k:v for k,v in self.owner.items() if k!='token'})
        STATE.parent.mkdir(parents=True,exist_ok=True)
        temp=STATE.with_suffix('.tmp');temp.write_text(json.dumps(data));temp.replace(STATE)
    def validate(self,token):
        with self.condition:
            if not self.owner or not secrets.compare_digest(token or '',self.owner['token']):
                raise RuntimeError('Invalid or expired GPU lease')
    def clear_voice(self,token):
        with self.cleanup_lock:return self._clear_voice(token)
    def _clear_voice(self,token):
        self.validate(token)
        request=urllib.request.Request(VOICE+'/api/avatar/internal/release',data=b'{}',headers={'Content-Type':'application/json','X-Avatar-Lease':token})
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request,timeout=125) as response:data=json.load(response)
        except urllib.error.HTTPError as exc:
            # Surface a bounded response body, never the lease header.
            detail=exc.read(2048).decode(errors='replace')
            raise RuntimeError(f'Voice backend release HTTP {exc.code}: {detail}') from exc
        except urllib.error.URLError as exc:
            # No endpoint is needed before the new service is installed. Once
            # registered, connection loss requires recovery, never assume free.
            if not (STATE.parent/'voice-service-registered').exists():return {'unavailable_before_registration':True}
            raise RuntimeError('Voice backend release could not be confirmed') from exc
        if not data.get('released'):raise RuntimeError(data.get('error','Voice models remain busy'))
        return data
    def acquire(self,request):
        ticket=request['request_id'];pid=int(request['pid'])
        identity=start_id(pid)
        if identity is None:raise RuntimeError('GPU requester is no longer running')
        with self.condition:
            self.pending.append(ticket)
            try:
                deadline=time.monotonic()+21600
                while self.owner or self.pending[0]!=ticket or self.blocked:
                    if self.blocked:raise RuntimeError(self.blocked)
                    if start_id(pid)!=identity:raise RuntimeError('Queued GPU requester exited')
                    if time.monotonic()>deadline:raise RuntimeError('GPU queue wait timed out')
                    self.condition.wait(1)
                # Nonblocking physical lock keeps old cooperating CLI scripts
                # mutually exclusive without introducing a second lock order.
                self.fd=LOCK.open('a')
                while True:
                    if start_id(pid)!=identity:raise RuntimeError('Queued GPU requester exited')
                    try:fcntl.flock(self.fd,fcntl.LOCK_EX|fcntl.LOCK_NB);break
                    except BlockingIOError:
                        if time.monotonic()>deadline:raise RuntimeError('Legacy GPU lock is busy')
                        self.condition.wait(1)
                self.owner={'owner':str(request['owner'])[:180],'kind':request.get('kind','voice'),'pid':pid,'start_id':start_id(pid),'token':secrets.token_urlsafe(32),'since':time.time()}
                self.persist()
                return dict(self.owner)
            finally:
                if self.owner is None and self.fd:
                    self.fd.close();self.fd=None
                self.pending.remove(ticket);self.condition.notify_all()
    def release(self,token):
        with self.cleanup_lock:return self._release(token)
    def _release(self,token):
        self.validate(token)
        try:self.clear_voice(token)
        except Exception as exc:
            with self.condition:self.blocked='GPU release failed: '+str(exc);self.persist()
            raise
        with self.condition:
            self.owner=None;self.blocked=None;self.recovering=False
            if self.fd:self.fd.close();self.fd=None
            self.persist();self.condition.notify_all()
    def status(self):
        with self.condition:
            return {'owner':{k:v for k,v in (self.owner or {}).items() if k!='token'},'waiting':len(self.pending),'blocked':self.blocked}

arbiter=Arbiter()
class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        response=None;acquired=None
        try:
            pid,uid,gid=struct.unpack('3i',self.request.getsockopt(__import__('socket').SOL_SOCKET,__import__('socket').SO_PEERCRED,12))
            if uid not in (os.getuid(),0):raise RuntimeError('GPU scheduler access denied')
            request=json.loads(self.rfile.readline(65536))
            action=request.pop('action')
            if action=='acquire':
                request['pid']=pid;acquired=arbiter.acquire(request);response=acquired
            elif action=='validate':arbiter.validate(request.get('token'));response={}
            elif action=='release':
                # After arbiter restart, only the exact surviving owner PID
                # (including its start ID) may complete its old lease. It is
                # not permission for new work or for a different process.
                token=request.get('token')
                with arbiter.condition:
                    if arbiter.recovering and arbiter.owner and pid==arbiter.owner['pid'] and start_id(pid)==arbiter.owner['start_id']:
                        token=arbiter.owner['token']
                arbiter.release(token);response={}
            elif action=='clear_voice':response=arbiter.clear_voice(request.get('token'))
            elif action=='status':response=arbiter.status()
            else:raise RuntimeError('Unknown GPU scheduler operation')
            self.wfile.write(json.dumps(dict(ok=True,**response)).encode()+b'\n');self.wfile.flush()
        except (BrokenPipeError,ConnectionResetError):
            if acquired:arbiter.release(acquired['token'])
        except Exception as exc:
            try:self.wfile.write(json.dumps({'ok':False,'error':str(exc)}).encode()+b'\n')
            except OSError:pass

class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads=True

if __name__=='__main__':
    SOCKET.parent.mkdir(parents=True,exist_ok=True)
    if SOCKET.exists():
        import socket
        try:
            with socket.socket(socket.AF_UNIX) as s:s.connect(str(SOCKET))
        except ConnectionRefusedError:SOCKET.unlink()
        else:raise RuntimeError('GPU scheduler already running')
    with Server(str(SOCKET),Handler) as server:
        SOCKET.chmod(0o600);print('GPU_SCHEDULER_READY',flush=True);server.serve_forever()
