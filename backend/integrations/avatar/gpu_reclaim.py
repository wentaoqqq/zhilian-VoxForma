"""Temporary, path-restricted preemption of the approved minimax ComfyUI."""
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import time

COMFY_ROOT = Path('/home/dministrator/minimax-h3')


def approved_role(argv, cwd):
    cwd = Path(cwd)
    if not argv or cwd != COMFY_ROOT / 'ComfyUI':
        return None
    interpreter = Path(os.path.abspath(cwd / argv[0]))
    if interpreter not in {COMFY_ROOT / '.venv/bin' / name
                           for name in ('python', 'python3', 'python3.12')}:
        return None
    index = 1
    while index < len(argv):
        arg = argv[index]
        if arg in ('-u', '-B', '-E', '-I', '-s', '-S', '-O', '-OO', '-q', '-v'):
            index += 1
        elif arg in ('-X', '-W'):
            index += 2
        else:
            if arg.startswith('-'):
                return None
            script = Path(os.path.abspath(cwd / arg))
            return {COMFY_ROOT / 'ComfyUI/main.py': 'comfyui',
                    COMFY_ROOT / 'scripts/observed_server.py': 'comfyui-watcher'}.get(script)
    return None


def _role(pid):
    proc = Path('/proc') / str(pid)
    if proc.stat().st_uid != os.geteuid():
        return None
    argv = proc.joinpath('cmdline').read_bytes().decode().rstrip('\0').split('\0')
    return approved_role(argv, proc.joinpath('cwd').resolve())


def reclaim_approved_comfyui():
    # Conda's older Python builds can omit pidfd bindings; the host Python
    # supplies them without changing the model's inference environment.
    if not hasattr(os, 'pidfd_open') or not hasattr(signal, 'pidfd_send_signal'):
        result = subprocess.run(['/usr/bin/python3', str(Path(__file__).resolve())],
                                capture_output=True, text=True, check=True, timeout=15)
        print(result.stdout, end='', flush=True)
        return json.loads(result.stdout.splitlines()[-1])['reclaimed_pids']
    targets = []
    try:
        for proc in Path('/proc').iterdir():
            if not proc.name.isdigit():
                continue
            fd = None
            try:
                pid = int(proc.name)
                role = _role(pid)
                if role is None:
                    continue
                # A pidfd keeps a recycled PID from receiving our signals.
                fd = os.pidfd_open(pid)
                if _role(pid) != role:
                    os.close(fd)
                    fd = None
                    continue
                targets.append((pid, fd, role))
            except (OSError, UnicodeError, ValueError):
                if fd is not None:
                    os.close(fd)
        targets.sort(key=lambda item: item[2] != 'comfyui-watcher')
        print(json.dumps({'temporary_gpu_reclaim': [
            {'pid': pid, 'program': role} for pid, _, role in targets]}), flush=True)
        pending = []
        for _, fd, _ in targets:
            try:
                signal.pidfd_send_signal(fd, signal.SIGTERM)
                pending.append(fd)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + 5
        while pending and time.monotonic() < deadline:
            finished, _, _ = select.select(pending, [], [], .2)
            pending = [fd for fd in pending if fd not in finished]
        for fd in pending:
            try:
                signal.pidfd_send_signal(fd, signal.SIGKILL)
            except ProcessLookupError:
                pass
        return [pid for pid, _, _ in targets]
    finally:
        for _, fd, _ in targets:
            os.close(fd)


if __name__ == '__main__':
    print(json.dumps({'reclaimed_pids': reclaim_approved_comfyui()}), flush=True)
