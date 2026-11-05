"""Portable source/browser entrypoint for the VoiceStudio digital-human fork."""
import argparse
import os
from pathlib import Path
import sys

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', default=3900, type=int)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[3]
    adapter = Path(__file__).resolve().parent
    sys.path[:0] = [str(adapter), str(source/'backend'), str(source)]
    os.environ.setdefault('AVATAR_VOICE_URL', f'http://127.0.0.1:{args.port}')
    os.environ.setdefault('AVATAR_GPU_AUTOSTART', '1')
    import uvicorn
    uvicorn.run('avatar_server:app', host=args.host, port=args.port, workers=1)

if __name__ == '__main__':
    main()
