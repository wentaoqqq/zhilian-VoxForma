from avatar_paths import smi_path
from contextlib import contextmanager
import fcntl,os,subprocess,json,time
from gpu_client import Lease,current_lease

@contextmanager
def execution_lease(root,jid):
    if os.environ.get('AVATAR_GPU_COORDINATION_ENABLED')=='1':
        with Lease(jid,'avatar') as lease:
            lease.clear_voice()
            yield lease
    else:
        class Legacy:
            def clear_voice(self):pass
        with (root/'.run.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            yield Legacy()

def delegate_env():
    lease=current_lease.get()
    return {'AVATAR_GPU_DELEGATE':lease['token']} if lease else {}

def check_gpu_for_video():
    result=subprocess.run([smi_path(),'--query-gpu=memory.free,memory.used','--format=csv,noheader,nounits'],capture_output=True,text=True,errors='replace',check=True,timeout=8)
    free,used=map(lambda x:int(x.strip()),result.stdout.splitlines()[0].split(','))
    minimum=int(os.environ.get('AVATAR_STUDENT_MIN_FREE_MIB','13000'))
    print(json.dumps({'gpu_before_video':{'free_mib':free,'used_mib':used,'minimum_free_mib':minimum}}),flush=True)
    if free<minimum:raise RuntimeError(f'GPU memory remains occupied: free {free} MiB, need at least {minimum} MiB before Student')
