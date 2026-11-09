"""Old audio-only requests reach Studio inside managed avatar installations."""
import importlib.util
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
