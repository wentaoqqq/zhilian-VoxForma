"""A job-owned speech process must exit before the video can use its GPU."""
import asyncio
import json
import os
from pathlib import Path
import sys


async def stop_process_tree(proc):
    import psutil
    try:
        parent=psutil.Process(proc.pid)
        processes=parent.children(recursive=True)+[parent]
    except psutil.NoSuchProcess:
        processes=[]
    for process in reversed(processes):
        try:process.terminate()
        except psutil.NoSuchProcess:pass
    if processes:
        _,alive=await asyncio.to_thread(psutil.wait_procs,processes,timeout=5)
        for process in alive:
            try:process.kill()
            except psutil.NoSuchProcess:pass
    await proc.wait()


async def run_isolated_speech(params, reference, emotion, output):
    from gpu_client import current_lease
    lease=current_lease.get()
    if not lease or not lease.get('token'):raise RuntimeError('Speech worker requires a GPU lease')
    folder=Path(output).parent
    request=folder/'native-worker-request.json';result=folder/'native-worker-result.json';log=folder/'native-worker.log'
    request.write_text(json.dumps({'params':params,'reference':str(reference) if reference else None,
                                   'emotion':str(emotion) if emotion else None,'output':str(output),'result':str(result)}))
    result.unlink(missing_ok=True)
    env=os.environ.copy();env['AVATAR_GPU_DELEGATE']=lease['token'];env['PYTHONUNBUFFERED']='1'
    options={'start_new_session':True} if os.name!='nt' else {'creationflags':0x08000000}
    with log.open('wb') as stream:
        proc=await asyncio.create_subprocess_exec(sys.executable,str(Path(__file__).with_name('native_speech_worker.py')),
            '--request',str(request),env=env,stdout=stream,stderr=stream,**options)
        try:
            await proc.wait()
        finally:
            if proc.returncode is None:
                await stop_process_tree(proc)
    if proc.returncode or not result.exists():
        tail=log.read_bytes()[-5000:].decode(errors='replace')
        raise RuntimeError(f'Isolated speech exited {proc.returncode}: {tail}')
    return json.loads(result.read_text())
