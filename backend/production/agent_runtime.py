"""Pinned managed CLI runtime; no install or network calls when opening the UI."""
import os
import json
import platform
from pathlib import Path
import shutil
import subprocess
import sys

from production import store

OPENCODE_VERSION = '1.2.27'
HYPERFRAMES_VERSION = '0.6.93'


def runtime_root():
    return store.ROOT / 'runtime'


def tool_env():
    env = os.environ.copy()
    env['HYPERFRAMES_NO_TELEMETRY'] = '1'
    env['DO_NOT_TRACK'] = '1'
    # beginFrame warmup hangs under the tested WSL graphics stack; screenshots
    # retain the original frame output without that Chromium scheduling path.
    if 'microsoft' in platform.release().lower():
        env.setdefault('PRODUCER_FORCE_SCREENSHOT', 'true')
    from core.config import DATA_DIR
    tools = [str(runtime_root() / 'node_modules/.bin'), str(Path(sys.executable).parent), str(Path(DATA_DIR).parent / 'tools')]
    env['PATH'] = os.pathsep.join(tools + [env.get('PATH', '')])
    ready = runtime_root() / 'ready.json'
    if ready.is_file():
        browser = json.loads(ready.read_text()).get('browser')
        if browser and Path(browser).is_file():
            env['HYPERFRAMES_BROWSER_PATH'] = browser
    return env


def binary(name):
    configured = os.environ.get('VOXFORMA_' + name.upper() + '_BIN')
    return configured or shutil.which(name, path=tool_env()['PATH'])


def status():
    packs = store.ROOT / 'packs'
    return {'installed': bool(binary('opencode') and binary('hyperframes') and (runtime_root() / 'ready.json').is_file()), 'opencode': bool(binary('opencode')), 'hyperframes': bool(binary('hyperframes')), 'packs': [p.name for p in packs.iterdir() if p.is_dir()] if packs.exists() else []}


def install():
    npm = binary('npm')
    if not npm:
        raise RuntimeError('Node.js/npm is missing. Install the Node runtime first.')
    runtime_root().mkdir(parents=True, exist_ok=True)
    subprocess.run([npm, 'install', '--prefix', str(runtime_root()), '--no-audit', '--no-fund', '--save-exact', 'opencode-ai@' + OPENCODE_VERSION, 'hyperframes@' + HYPERFRAMES_VERSION], env=tool_env(), check=True)
    subprocess.run([sys.executable, '-m', 'pip', 'install', '--target', str(runtime_root() / 'python'), 'fonttools==4.61.1', 'brotli==1.2.0'], env=tool_env(), check=True)
    missing = [name for name in ['bash', 'ffmpeg', 'ffprobe', 'jq'] if not binary(name)]
    if missing:
        raise RuntimeError('Install these original-workflow prerequisites: ' + ', '.join(missing))
    subprocess.run([binary('hyperframes'), 'browser', 'ensure'], env=tool_env(), check=True)
    browser = subprocess.check_output([binary('hyperframes'), 'browser', 'path'], env=tool_env(), text=True).strip().splitlines()[-1]
    if not Path(browser).is_file():
        raise RuntimeError('The rendering browser is missing. Retry runtime installation.')
    (runtime_root() / 'ready.json').write_text(json.dumps({'opencode': OPENCODE_VERSION, 'hyperframes': HYPERFRAMES_VERSION, 'browser': browser}))
    return status()
