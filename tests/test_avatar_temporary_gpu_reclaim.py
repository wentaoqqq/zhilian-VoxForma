import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


spec = importlib.util.spec_from_file_location('gpu_reclaim', Path(__file__).resolve().parents[1]
    / 'backend/integrations/avatar/gpu_reclaim.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_only_the_approved_project_and_entrypoints_match():
    # Use Linux path semantics even when this selector test runs on Windows.
    import posixpath
    from pathlib import PurePosixPath
    from unittest.mock import patch
    root = PurePosixPath('/home/dministrator/minimax-h3')
    with patch.object(module, 'Path', PurePosixPath), patch.object(module, 'COMFY_ROOT', root), \
            patch.object(module.os.path, 'abspath', posixpath.abspath):
        cwd = root / 'ComfyUI'
        assert module.approved_role(['../.venv/bin/python', '-u', '-X', 'faulthandler',
                                     'main.py', '--port', '8188'], cwd) == 'comfyui'
        assert module.approved_role(['../.venv/bin/python', '-u',
                                     '../scripts/observed_server.py'], cwd) == 'comfyui-watcher'
        assert module.approved_role(['/usr/bin/python3', 'main.py'], cwd) is None
        assert module.approved_role(['../.venv/bin/python', 'main.py'], '/other/ComfyUI') is None
        assert module.approved_role(['../.venv/bin/python', 'other.py', 'main.py'], cwd) is None
        assert module.approved_role(['../.venv/bin/python', '-c', 'main.py'], cwd) is None
        assert module.approved_role(['../.venv/bin/python', '-m', 'main.py'], cwd) is None
        assert module.approved_role(['../.venv/bin/python', '../scripts/other.py'], cwd) is None


def test_cleanup_requires_both_low_memory_and_the_temporary_switch(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, 'fcntl', SimpleNamespace())
    monkeypatch.setitem(sys.modules, 'avatar_paths', SimpleNamespace(
        smi_path=lambda: 'nvidia-smi', data_dir=lambda: tmp_path))
    monkeypatch.setitem(sys.modules, 'gpu_client', SimpleNamespace(Lease=None, current_lease=None))
    calls = []
    monkeypatch.setitem(sys.modules, 'gpu_reclaim', SimpleNamespace(
        reclaim_approved_comfyui=lambda: calls.append('reclaim') or [123]))
    monkeypatch.setenv('AVATAR_STUDENT_MIN_FREE_MIB', '13000')
    runtime_spec = importlib.util.spec_from_file_location('gpu_runtime_check',
        Path(__file__).resolve().parents[1] / 'backend/integrations/avatar/gpu_runtime.py')
    runtime = importlib.util.module_from_spec(runtime_spec)
    runtime_spec.loader.exec_module(runtime)
    monkeypatch.setattr(runtime, '_gpu_memory', lambda: (11000, 5000))
    with pytest.raises(RuntimeError, match='GPU memory remains occupied'):
        runtime.check_gpu_for_video()
    assert not calls
    flag = tmp_path / 'avatar-runtime/temporary-comfy-reclaim.enabled'
    flag.parent.mkdir()
    flag.touch()
    monkeypatch.setattr(runtime, '_gpu_memory', lambda: (14000, 2000))
    runtime.check_gpu_for_video()
    assert not calls
    memory = iter([(11000, 5000), (14000, 2000)])
    monkeypatch.setattr(runtime, '_gpu_memory', lambda: next(memory))
    monkeypatch.setattr(runtime.time, 'sleep', lambda seconds: None)
    runtime.check_gpu_for_video()
    assert calls == ['reclaim']


def test_conda_without_pidfd_uses_the_same_helper_under_host_python(monkeypatch):
    monkeypatch.delattr(module.os, 'pidfd_open', raising=False)
    commands = []
    def run(command, **kwargs):
        commands.append(command)
        assert kwargs['check'] is True
        return SimpleNamespace(stdout='{"reclaimed_pids": [548]}\n')
    monkeypatch.setattr(module.subprocess, 'run', run)
    assert module.reclaim_approved_comfyui() == [548]
    assert commands == [['/usr/bin/python3', str(Path(module.__file__).resolve())]]
