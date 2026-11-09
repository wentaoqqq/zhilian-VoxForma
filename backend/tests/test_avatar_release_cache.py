"""Exercise the real release handler without importing a GPU/TTS runtime."""
import ast
import asyncio
import contextvars
from pathlib import Path
import os
import time
import sys
from types import SimpleNamespace
from fastapi.responses import JSONResponse
import pytest

SOURCE=Path(__file__).resolve().parents[1]/'integrations/avatar/avatar_server.py'


@pytest.mark.parametrize('remaining,status',[([],200),(['still-loaded'],409)])
def test_release_checks_own_models_and_caches_without_student_budget(monkeypatch,remaining,status):
    import services
    import subprocess
    events=[]
    async def unload():events.append('unload');return {'results':{}}
    def clear():events.append('allocator');return None
    def loaded():events.append('snapshot');return {'models':remaining}
    def probe(*args,**kwargs):pytest.fail('Lease release must not apply the Student driver-memory budget')
    lifecycle=SimpleNamespace(unload_all=unload,free_vram=clear,list_loaded=loaded)
    monkeypatch.setitem(sys.modules,'services.model_lifecycle',lifecycle)
    monkeypatch.setattr(services,'model_lifecycle',lifecycle,raising=False)
    monkeypatch.setattr(subprocess,'run',probe)
    monkeypatch.setenv('AVATAR_STUDENT_MIN_FREE_MIB','13000')
    namespace={'asyncio':asyncio,'time':time,'os':os,'mm':SimpleNamespace(_get_gpu_pool=lambda:SimpleNamespace(stats=lambda:{'running':0,'queued':0})),'gpu_client':SimpleNamespace(current_lease=contextvars.ContextVar('lease',default=None)),'internal_lease':lambda _: {'token':'fixture'},'JSONResponse':JSONResponse,'smi_path':lambda:'nvidia-smi'}
    function=next(n for n in ast.parse(SOURCE.read_text()).body if isinstance(n,ast.AsyncFunctionDef) and n.name=='_release_models')
    exec(compile(ast.Module(body=[function],type_ignores=[]),str(SOURCE),'exec'),namespace)
    result=asyncio.run(namespace['_release_models'](None))
    assert getattr(result,'status_code',200)==status
    assert events==['unload','allocator','snapshot']
    assert namespace['gpu_client'].current_lease.get() is None


@pytest.mark.parametrize('free,raises',[(14000,False),(500,True)])
def test_student_still_checks_its_memory_budget(monkeypatch,free,raises):
    import importlib.util
    import subprocess
    from types import ModuleType
    root=SOURCE.parent
    monkeypatch.syspath_prepend(str(root))
    if sys.platform=='win32':monkeypatch.setitem(sys.modules,'fcntl',ModuleType('fcntl'))
    spec=importlib.util.spec_from_file_location('video_memory_gate_test',root/'gpu_runtime.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.setattr(subprocess,'run',lambda *a,**kw:SimpleNamespace(stdout=f'{free}, 500'))
    monkeypatch.setenv('AVATAR_STUDENT_MIN_FREE_MIB','13000')
    if raises:
        with pytest.raises(RuntimeError,match='need at least 13000'):module.check_gpu_for_video()
    else:module.check_gpu_for_video()
