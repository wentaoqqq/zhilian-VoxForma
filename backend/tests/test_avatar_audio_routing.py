"""Old audio-only requests reach Studio inside managed avatar installations."""
import importlib.util
import ast
import asyncio
import contextvars
import json
from pathlib import Path
import sys
import threading
from types import ModuleType, SimpleNamespace
import wave
from contextlib import contextmanager
from http.server import ThreadingHTTPServer

import httpx
import pytest
from fastapi import HTTPException

INTEGRATION=Path(__file__).resolve().parents[1]/'integrations/avatar'
WEB=INTEGRATION/'vendor/tbdub_student/webui'


@pytest.fixture
def legacy_api(tmp_path,monkeypatch):
    monkeypatch.syspath_prepend(str(INTEGRATION));monkeypatch.syspath_prepend(str(WEB))
    # These request-routing tests do not enter Unix GPU/file-lock operations.
    if sys.platform=='win32':monkeypatch.setitem(sys.modules,'fcntl',ModuleType('fcntl'))
    monkeypatch.setenv('OMNIVOICE_TBDUB_DIR',str(tmp_path/'student'))
    monkeypatch.setenv('AVATAR_ASSETS_ROOT',str(tmp_path/'assets'))
    def load(name,path):
        spec=importlib.util.spec_from_file_location(name,path)
        module=importlib.util.module_from_spec(spec);monkeypatch.setitem(sys.modules,name,module);spec.loader.exec_module(module);return module
    queue=load('unified_jobs',WEB/'unified_jobs.py')
    app=load('test_legacy_audio_web',WEB/'app.py')
    app.ASSETS.mkdir();(app.ASSETS/'reference.wav').write_bytes(b'fixture reference')
    requests=[]
    def submit(payload):requests.append(payload);return {'id':'fixture','status':'queued'}
    monkeypatch.setattr(app,'MANAGER',SimpleNamespace(submit=submit))
    server=ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    with httpx.Client(base_url=f'http://127.0.0.1:{server.server_port}',trust_env=False) as client:
        yield client,queue,requests,app
    server.shutdown();server.server_close();thread.join(3)


@pytest.mark.parametrize('managed,explicit,expected',[
    (True,None,'voicestudio'),(False,None,'legacy_indextts'),
    (True,'legacy_indextts','legacy_indextts'),(False,'voicestudio','voicestudio'),
])
def test_old_audio_only_request_default_and_explicit_provider(legacy_api,monkeypatch,managed,explicit,expected):
    client,_,requests,_=legacy_api
    if managed:monkeypatch.setenv('AVATAR_INTEGRATION_DIR',str(INTEGRATION))
    else:monkeypatch.delenv('AVATAR_INTEGRATION_DIR',raising=False)
    body={'mode':'tts','audio_only':True,'text':'Audio route check.','reference':'reference.wav'}
    if explicit:body['tts_provider']=explicit
    response=client.post('/api/jobs',json=body)
    assert response.status_code==202,response.text
    assert requests[0]['tts_provider']==expected
    assert requests[0]['video'] is None and requests[0]['audio_only'] is True


@pytest.mark.parametrize('engine,status', [('indextts2', 202), ('indextts20', 202), ('omnivoice', 400)])
def test_studio_emotion_audio_engine_allowlist(legacy_api, engine, status):
    client, _, requests, _ = legacy_api
    response = client.post('/api/jobs', json={
        'mode': 'tts', 'audio_only': True, 'text': 'Emotion route check.',
        'reference': 'reference.wav', 'emotion': 'reference.wav',
        'emotion_enabled': True, 'tts_provider': 'voicestudio', 'tts_engine': engine,
    })
    assert response.status_code == status, response.text
    if status == 202:
        assert requests[0]['tts_engine'] == engine
        assert requests[0]['emotion'] == 'reference.wav'
    else:
        assert not requests


def test_legacy_provider_keeps_its_original_engine_restriction(legacy_api):
    client, _, requests, _ = legacy_api
    response = client.post('/api/jobs', json={
        'mode': 'tts', 'audio_only': True, 'text': 'Legacy route check.',
        'reference': 'reference.wav', 'tts_provider': 'legacy_indextts', 'tts_engine': 'indextts20',
    })
    assert response.status_code == 400
    assert not requests


@pytest.mark.parametrize('engine,allowed', [('indextts2', True), ('indextts20', True), ('omnivoice', False)])
def test_internal_speech_emotion_engine_allowlist(tmp_path, monkeypatch, engine, allowed):
    """Run the real handler without importing its GPU scheduler/startup hooks."""
    source = INTEGRATION / 'avatar_server.py'
    function = next(n for n in ast.parse(source.read_text(encoding='utf-8')).body
                    if isinstance(n, ast.AsyncFunctionDef) and n.name == 'internal_speech')
    function.decorator_list = []
    folder = tmp_path / 'results/webui/job'; folder.mkdir(parents=True)
    request_file = folder / 'tts-request.json'
    request_file.write_text(json.dumps({'output': str(folder / 'speech.wav'), 'engine': engine,
                                       'emotion': 'emotion.wav', 'native_form': {}}))
    calls = []
    async def native(params, reference, emotion, output):
        calls.append((params['engine'], emotion)); return {'saved': True}
    async def until_cancelled(request, path, operation): return await operation
    async def body(): return {'request': str(request_file)}
    monkeypatch.setitem(sys.modules, 'services.tts_backend', SimpleNamespace(get_backend_class=lambda _: object))
    namespace = {'Request': object, 'Path': Path, 'ROOT': tmp_path, 'ASSETS': tmp_path,
                 'data_dir': lambda: tmp_path, 'json': json,
                 'HTTPException': HTTPException, 'UnsafePath': ValueError,
                 'internal_lease': lambda _: 'lease', 'contained_path': lambda _, value: Path(value),
                 'inside': lambda *_: True, 'speech_reference': lambda value: value,
                 'gpu_client': SimpleNamespace(current_lease=contextvars.ContextVar('test_lease', default=None)),
                 'native_speech': native, 'speech_until_cancelled': until_cancelled}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), 'exec'), namespace)
    operation = namespace['internal_speech'](SimpleNamespace(json=body))
    if allowed:
        assert asyncio.run(operation) == {'saved': True}
        assert calls == [(engine, 'emotion.wav')]
    else:
        with pytest.raises(HTTPException) as exc: asyncio.run(operation)
        assert exc.value.status_code == 422
        assert not calls
    assert namespace['gpu_client'].current_lease.get() is None


@pytest.mark.parametrize('engine,text,vector,allowed', [
    ('indextts2', None, None, True), ('indextts20', None, None, True),
    ('omnivoice', None, None, False), ('indextts20', 'sad', None, False),
    ('indextts20', None, [0] * 8, False),
])
def test_native_generate_emotion_audio_validation(engine, text, vector, allowed):
    """Exercise the actual validation/forwarding block without loading torch."""
    source = INTEGRATION.parents[1] / 'api/routers/generation.py'
    function = next(n for n in ast.parse(source.read_text(encoding='utf-8')).body
                    if isinstance(n, ast.AsyncFunctionDef) and n.name == 'generate_speech')
    start = next(i for i, n in enumerate(function.body) if isinstance(n, ast.If)
                 and isinstance(n.test, ast.BoolOp) and isinstance(n.test.values[0], ast.Name)
                 and n.test.values[0].id == '_avatar_emotion')
    namespace = {'_avatar_emotion': ('emotion.wav', 0.4), 'engine_id': engine,
                 'emotion_text': text, 'emotion_vector': vector, 'emo_alpha': None,
                 'HTTPException': HTTPException}
    block = compile(ast.Module(body=function.body[start:start + 4], type_ignores=[]), str(source), 'exec')
    if allowed:
        exec(block, namespace)
        assert namespace['emotion_kwargs'] == {'emo_audio': 'emotion.wav', 'emo_alpha': 0.4}
    else:
        with pytest.raises(HTTPException) as exc: exec(block, namespace)
        assert exc.value.status_code == 422


def test_audio_only_executes_studio_worker_and_never_student(legacy_api,monkeypatch,tmp_path):
    client,module,requests,app=legacy_api
    monkeypatch.setenv('AVATAR_INTEGRATION_DIR',str(INTEGRATION))
    response=client.post('/api/jobs',json={'mode':'tts','audio_only':True,'text':'Audio route check.','reference':'reference.wav'})
    assert response.status_code==202
    manager=module.JobManager(tmp_path/'student',app.ASSETS,Path(sys.executable))
    jid='fixture';manager.jobs[jid]={**requests[0],'id':jid};folder=manager.out/jid;folder.mkdir(parents=True)
    stages=[];commands=[]
    @contextmanager
    def lease(*args):yield SimpleNamespace(clear_voice=lambda:stages.append('released'))
    monkeypatch.setattr(module,'execution_lease',lease)
    def command(_jid,args,cwd,log,timeout,status_file=None):
        commands.append(args)
        request=json.loads(Path(args[-1]).read_text())
        with wave.open(request['output'],'wb') as f:
            f.setnchannels(1);f.setsampwidth(2);f.setframerate(16000);f.writeframes(b'\0\0'*3200)
        return 0
    monkeypatch.setattr(manager,'command',command)
    monkeypatch.setattr(module.subprocess,'run',lambda *a,**kw:SimpleNamespace(stdout='{"format":{"duration":"0.2"}}'))
    monkeypatch.setattr(module,'check_gpu_for_video',lambda:pytest.fail('Audio-only must not run Student GPU checks'))
    manager.execute(jid)
    assert len(commands)==1 and Path(commands[0][1]).name=='voice_worker.py'
    assert manager.get(jid)['status']=='succeeded' and manager.get(jid)['tts_released']
    assert (folder/'speech.wav').is_file() and stages==['released']


def test_audio_only_ignores_an_unused_character_selection(legacy_api,monkeypatch):
    client,_,requests,_=legacy_api
    monkeypatch.setenv('AVATAR_INTEGRATION_DIR',str(INTEGRATION))
    response=client.post('/api/jobs',json={'mode':'tts','audio_only':True,'character_id':'unused-character','text':'Audio only.','reference':'reference.wav'})
    assert response.status_code==202,response.text
    assert 'character_id' not in requests[0]
    assert requests[0]['video'] is None and requests[0]['tts_provider']=='voicestudio'


def test_cancel_endpoint_routes_the_exact_job_and_unknown_jobs_are_404(legacy_api, monkeypatch):
    client, _, _, app = legacy_api
    calls = []
    def cancel(jid):
        calls.append(jid)
        if jid == 'missing':
            raise KeyError(jid)
        return {'id': jid, 'status': 'cancelling', 'audio_output': 'saved.wav'}
    monkeypatch.setattr(app.MANAGER, 'cancel', cancel, raising=False)
    response = client.post('/api/jobs/exact-job/cancel')
    assert response.status_code == 200 and response.json()['id'] == 'exact-job'
    assert response.json()['audio_output'] == 'saved.wav'
    assert client.post('/api/jobs/missing/cancel').status_code == 404
    assert calls == ['exact-job', 'missing']
