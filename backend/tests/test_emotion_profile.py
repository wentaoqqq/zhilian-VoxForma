"""Saved emotion samples stay independent of speaker identity and queue state."""
import ast
import contextvars
from contextlib import contextmanager
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import sys

from fastapi import HTTPException
import pytest

BACKEND = Path(__file__).resolve().parents[1]


def function_scope(path, name, **scope):
    tree = ast.parse(path.read_text(encoding='utf8'))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(path), 'exec'), scope)
    return scope[name]


def test_saved_emotion_resolves_locked_sample_without_touching_speaker(tmp_path):
    from core.path_security import contained_join
    conn = sqlite3.connect(':memory:');conn.row_factory = sqlite3.Row
    conn.execute('CREATE TABLE voice_profiles (id TEXT, locked_audio_path TEXT, ref_audio_path TEXT)')
    conn.execute('INSERT INTO voice_profiles VALUES (?,?,?)', ('emotion', 'locked.wav', 'reference.wav'))
    (tmp_path / 'locked.wav').write_bytes(b'emotion')
    @contextmanager
    def database():yield conn
    resolve = function_scope(BACKEND/'api/routers/generation.py', '_emotion_profile_audio',
                             db_conn=database, VOICES_DIR=tmp_path, contained_join=contained_join,
                             Path=Path, HTTPException=HTTPException)
    assert resolve('emotion') == str(tmp_path/'locked.wav')
    with pytest.raises(HTTPException):resolve('missing')
    conn.execute("UPDATE voice_profiles SET locked_audio_path='../outside.wav'")
    with pytest.raises(HTTPException):resolve('emotion')
    conn.close()


@pytest.mark.parametrize('engine', ['indextts20','indextts2'])
def test_avatar_snapshots_emotion_before_queueing(monkeypatch, engine):
    source = BACKEND/'integrations/avatar/avatar_server.py'
    tree = ast.parse(source.read_text(encoding='utf8'))
    fields = next(n for n in tree.body if isinstance(n,ast.Assign) and
                  any(isinstance(t,ast.Name) and t.id=='NATIVE_FIELDS' for t in n.targets))
    scope = {};exec(compile(ast.Module(body=[fields],type_ignores=[]),str(source),'exec'),scope)
    backend = SimpleNamespace(supports_cloning=False)
    monkeypatch.setitem(sys.modules,'services.tts_backend',SimpleNamespace(
        active_backend_id=lambda:engine,get_backend_class=lambda _:backend,
        list_backends=lambda:[{'id':engine,'available':True}]))
    snapshots = []
    def snapshot(profile):snapshots.append(profile);return ('saved-emotion.wav','ignored transcript')
    prepare = function_scope(source,'prepare_native_job',NATIVE_FIELDS=scope['NATIVE_FIELDS'],
                             snapshot_voice=snapshot,HTTPException=HTTPException)
    form={'emo_profile_id':'emotion-profile','emo_alpha':'.7','language':'Chinese'}
    data={'tts_engine':engine,'native_form':form,'emotion_enabled':False}
    prepare(data)
    assert snapshots==['emotion-profile']
    assert data['emotion']=='saved-emotion.wav' and data['emotion_enabled']
    assert data['emotion_weight']==.7
    assert data['native_form']=={'language':'Chinese'}
    assert form['emo_profile_id']=='emotion-profile'


@pytest.mark.parametrize('extra', [{'emo_text':'sad'}, {'emo_vector':'[0,0,0,0,0,0,0,0]'}, {'emo_alpha':'nan'}])
def test_avatar_rejects_conflicting_emotion_or_invalid_strength(monkeypatch, extra):
    source=BACKEND/'integrations/avatar/avatar_server.py'
    monkeypatch.setitem(sys.modules,'services.tts_backend',SimpleNamespace(
        active_backend_id=lambda:'indextts20',get_backend_class=lambda _:object,
        list_backends=lambda:[{'id':'indextts20','available':True}]))
    fields={'emo_profile_id','emo_alpha','emo_text','emo_vector'}
    prepare=function_scope(source,'prepare_native_job',NATIVE_FIELDS=fields,
        snapshot_voice=lambda _:pytest.fail('Reject invalid input before snapshot'),HTTPException=HTTPException)
    with pytest.raises(HTTPException):prepare({'tts_engine':'indextts20','native_form':{'emo_profile_id':'x',**extra}})


@pytest.mark.parametrize('engine,extra,allowed', [
    ('indextts20', {}, True), ('indextts2', {}, True),
    ('omnivoice', {}, False), ('indextts20', {'emo_text':'sad'}, False),
    ('indextts20', {'emo_vector':'vector'}, False),
])
def test_native_request_resolves_emotion_without_replacing_speaker(monkeypatch, engine, extra, allowed):
    source=BACKEND/'api/routers/generation.py'
    tree=ast.parse(source.read_text(encoding='utf8'))
    fn=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='generate_speech')
    start=next(i for i,n in enumerate(fn.body) if isinstance(n,ast.Import) and any(a.name=='math' for a in n.names))
    end=next(i for i,n in enumerate(fn.body[start:],start) if isinstance(n,ast.ImportFrom) and n.module=='services.tts_backend')
    monkeypatch.setitem(sys.modules,'avatar_conditioning',SimpleNamespace(emotion_audio=contextvars.ContextVar('emotion',default=None)))
    scope={'emo_profile_id':'emotion-profile','profile_id':'speaker-profile','engine_id':engine,
           'emo_text':None,'emo_vector':None,'emo_alpha':.75,'HTTPException':HTTPException,
           'backend_cls':SimpleNamespace(supports_emotion=True),
           '_parse_emotion_vector':lambda raw: [0]*8 if raw else None,
           '_emotion_profile_audio':lambda profile:'emotion.wav' if profile=='emotion-profile' else None,**extra}
    code=compile(ast.Module(body=fn.body[start:end],type_ignores=[]),str(source),'exec')
    if allowed:
        exec(code,scope)
        assert scope['emotion_kwargs']=={'emo_audio':'emotion.wav','emo_alpha':.75}
        assert scope['profile_id']=='speaker-profile'
    else:
        with pytest.raises(HTTPException):exec(code,scope)
