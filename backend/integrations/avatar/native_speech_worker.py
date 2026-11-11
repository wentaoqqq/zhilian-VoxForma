"""Reuse VoiceStudio's native speech implementation inside a disposable process."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys


async def run(path):
    root=Path(__file__).resolve().parents[3]
    sys.path[:0]=[str(root),str(root/'backend')]
    import gpu_client
    token=os.environ.get('AVATAR_GPU_DELEGATE')
    if not token:raise RuntimeError('Missing delegated GPU lease')
    gpu_client.rpc('validate',token=token,timeout=5)
    from avatar_server import native_speech_in_process
    data=json.loads(Path(path).read_text())
    scope=gpu_client.current_lease.set({'token':token})
    try:
        result=await native_speech_in_process(data['params'],Path(data['reference']) if data['reference'] else None,
                                              data['emotion'],Path(data['output']))
        from services import model_lifecycle
        await model_lifecycle.unload_all()
        Path(data['result']).write_text(json.dumps(result))
    finally:
        gpu_client.current_lease.reset(scope)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--request',required=True)
    asyncio.run(run(parser.parse_args().request))
