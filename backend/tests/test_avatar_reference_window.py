"""The queue must not turn stored transcripts into explicit corrections."""
import ast
from pathlib import Path
from contextlib import contextmanager
from types import SimpleNamespace
import sys
import pytest
from fastapi import HTTPException


@pytest.mark.parametrize('engine', ['omnivoice','omnivoice-subprocess'])
@pytest.mark.parametrize('seconds,manual,expected',[(20.9,None,None),(19,None,'stored transcript'),(20.9,'manual correction','manual correction')])
def test_queue_keeps_best_window_reference_semantics(monkeypatch,tmp_path,engine,seconds,manual,expected):
    source=Path(__file__).resolve().parents[1]/'integrations/avatar/avatar_server.py'
    fn=next(n for n in ast.parse(source.read_text(encoding='utf8')).body if isinstance(n,ast.FunctionDef) and n.name=='prepare_native_job')
    cls=SimpleNamespace(supports_cloning=True,ref_strategy='best_window',max_ref_seconds=20)
    row={'kind':'clone'}
    @contextmanager
    def db():yield SimpleNamespace(execute=lambda *args:SimpleNamespace(fetchone=lambda:row))
    monkeypatch.setitem(sys.modules,'core.db',SimpleNamespace(db_conn=db))
    monkeypatch.setitem(sys.modules,'api.routers.generation',SimpleNamespace(_resolve_profile_conditioning=lambda row,**kw:{'ref_text':kw.get('ref_text') or 'stored transcript'}))
    monkeypatch.setitem(sys.modules,'services.tts_backend',SimpleNamespace(active_backend_id=lambda:engine,get_backend_class=lambda _:cls,list_backends=lambda:[{'id':engine,'available':True}],reference_duration_s=lambda _:seconds))
    scope={'NATIVE_FIELDS':{'ref_text'},'ASSETS':tmp_path,'HTTPException':HTTPException,'snapshot_voice':lambda _:('reference.wav','stored transcript')}
    exec(compile(ast.Module(body=[fn],type_ignores=[]),str(source),'exec'),scope)
    job={'tts_engine':engine,'voice_profile':'voice','native_form':{'ref_text':manual} if manual else {}}
    scope['prepare_native_job'](job)
    assert job['native_form'].get('ref_text')==expected
