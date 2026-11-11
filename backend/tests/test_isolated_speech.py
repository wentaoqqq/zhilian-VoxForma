import asyncio,contextvars,importlib.util,json,sys
from pathlib import Path
from types import SimpleNamespace
import psutil,pytest


@pytest.fixture
def worker(monkeypatch,tmp_path):
    path=Path(__file__).resolve().parents[1]/'integrations/avatar/isolated_speech.py'
    spec=importlib.util.spec_from_file_location('isolated_speech_test',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.setattr(module,'__file__',str(tmp_path/'isolated_speech.py'))
    monkeypatch.setitem(sys.modules,'gpu_client',SimpleNamespace(current_lease=contextvars.ContextVar('lease',default={'token':'fixture'})))
    return module,tmp_path


@pytest.mark.asyncio
async def test_waits_for_worker_exit_before_reporting_saved(worker):
    module,folder=worker
    (folder/'native_speech_worker.py').write_text("import sys,json,os\nfrom pathlib import Path\nr=json.loads(Path(sys.argv[2]).read_text());assert os.environ['AVATAR_GPU_DELEGATE']=='fixture';Path(r['output']).write_bytes(b'audio');Path(r['result']).write_text(json.dumps({'saved':True}))\n")
    result=await module.run_isolated_speech({'text':'test'},None,None,folder/'speech.wav')
    assert result=={'saved':True} and (folder/'speech.wav').read_bytes()==b'audio'


@pytest.mark.asyncio
async def test_cancelling_stops_owned_worker_descendants(worker):
    module,folder=worker;marker=folder/'child.pid'
    (folder/'native_speech_worker.py').write_text("import sys,subprocess,time\nfrom pathlib import Path\np=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)']);Path(__file__).with_name('child.pid').write_text(str(p.pid));time.sleep(120)\n")
    task=asyncio.create_task(module.run_isolated_speech({},None,None,folder/'speech.wav'))
    try:
        for _ in range(100):
            if marker.exists():break
            await asyncio.sleep(.05)
        assert marker.exists();pid=int(marker.read_text());task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert not psutil.pid_exists(pid) or psutil.Process(pid).status()==psutil.STATUS_ZOMBIE
    finally:
        if not task.done():task.cancel();await asyncio.gather(task,return_exceptions=True)
