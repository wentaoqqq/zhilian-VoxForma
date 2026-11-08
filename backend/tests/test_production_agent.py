"""Batch workflow, real-media tool calls, failure/retry and delivery integration."""
import asyncio
import io
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import httpx
from openai import OpenAI
from PIL import Image
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.dependencies import require_consumer
from api.routers import production as api
from core import db, job_store
from core.job_queue import JobQueue
from production import agent, agent_gateway, agent_tools, batch_store, store, workspace
from production.batch_models import Brand, BatchRequest
import shutil
import subprocess


def ffmpeg(*args):
    tool = shutil.which('ffmpeg')
    if not tool or not shutil.which('ffprobe'):
        pytest.skip('Real-media checks require FFmpeg/FFprobe')
    subprocess.run([tool, '-nostdin', '-v', 'error', '-y', *map(str, args)], capture_output=True, check=True)


def upload(client, path):
    response = client.post('/production/assets', params={'filename': path.name}, content=path.read_bytes())
    assert response.status_code == 200, response.text
    return response.json()



@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path/'studio.db'))
    monkeypatch.setattr(store, 'ROOT', tmp_path/'media')
    monkeypatch.setattr(agent, 'queue', JobQueue('test-agent'))
    monkeypatch.setattr(api, 'queue', JobQueue('test-legacy'))
    agent.contexts.clear()
    db.ensure_schema()
    app = FastAPI(); app.include_router(api.router)
    app.dependency_overrides[require_consumer] = lambda: None
    with TestClient(app) as client:
        yield client
    agent.contexts.clear()


def media(client, tmp_path, name='clip.mp4', color='green'):
    p=tmp_path/name
    ffmpeg('-f','lavfi','-i',f'color=c={color}:s=64x64:r=25:d=0.3','-c:v','libx264',p)
    return upload(client,p)


def pack():
    root=store.ROOT/'packs/roundtable'
    (root/'4_engine/tools').mkdir(parents=True)
    (root/'4_engine/tools/build-html.mjs').write_text('// original pack marker')
    (root/'AGENTS.md').write_text('Original mandatory style and routing table')
    (root/'4_engine/tools/example.sh').write_text('npx hyperframes@0.6.93 render .')
    (root/'4_engine/tools/append-random-outro.sh').write_text('find \"$OUTRO_DIR\" -type f')
    (root/'4_engine/tools/batch_product_runner.py').write_text('raise RuntimeError("heuristic")')
    return root


def record(sources, workflow='talking_head'):
    brand=batch_store.save_brand(Brand(name='Fixture brand', material_ids=[sources[0]['id']]))
    request=BatchRequest(brand_id=brand['id'],workflow=workflow,source_ids=[s['id'] for s in sources])
    jid='a'*32
    job_store.create(jid,type='production-batch');job_store.mark_running(jid)
    snapshot={'options':request.model_dump(),'brand':brand,'sources':sources}
    items=[{'source_id':s['id'],'filename':s['filename'],'status':'pending','artifacts':{},'error':None} for s in sources]
    with db.db_conn() as con:
        con.execute('INSERT INTO production_batches(id,request_json,items_json) VALUES (?,?,?)',(jid,json.dumps(snapshot),json.dumps(items)))
    root=store.ROOT/'batches'/jid; (root/'.voxforma').mkdir(parents=True)
    agent.contexts[jid]={'token':'fixture','root':root,'sources':sources,'brand':brand,'job':SimpleNamespace(id=jid,is_cancelled=False,cancel_event=asyncio.Event())}
    return jid,root,brand,request


def post(client,jid,tool,body):
    return client.post(f'/production/agent/{jid}/{tool}',headers={'Authorization':'Bearer fixture'},json=body)


@pytest.fixture
def vision_provider(monkeypatch):
    requests=[]
    def respond(request):
        body=json.loads(request.content);requests.append(body)
        answer={'passed':True,'issues':[],'subject':'visible fixture','best_cover_time':0}
        return httpx.Response(200,json={'id':'fixture','object':'chat.completion','created':1,'model':'fixture','choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(answer)}}]})
    sdk=OpenAI(api_key='fixture',base_url='http://fixture/v1',http_client=httpx.Client(transport=httpx.MockTransport(respond)),max_retries=0)
    monkeypatch.setattr(agent_gateway.llm_skills,'resolve_skill_client',lambda _:SimpleNamespace(client=sdk,model='fixture'))
    yield requests
    sdk.close()


def test_source_kinds_and_brand_validation(client,tmp_path):
    for name,data,kind in [('script.txt',b'A complete narration.','text'),('cover.png',None,'image')]:
        path=tmp_path/name
        if data:path.write_bytes(data)
        else:Image.new('RGB',(50,80),'red').save(path)
        asset=upload(client,path)
        assert asset['kind']==kind
        assert client.post('/production/brands',json={'name':'Brand','outro_ids':[asset['id']]}).status_code==400
    wav=tmp_path/'audio.wav';ffmpeg('-f','lavfi','-i','sine=duration=0.3',wav)
    assert upload(client,wav)['kind']=='audio'
    assert client.post('/production/brands',json={'name':'  '}).status_code==422


def test_workspace_uses_full_original_engine_and_retries_retain_intermediates(client,tmp_path):
    src=media(client,tmp_path);pack()
    jid,root,brand,request=record([src])
    root=workspace.prepare(jid,request,brand,[src])
    instructions=(root/'AGENTS.md').read_text()
    assert all(tool in instructions for tool in ['vision','analyze','asr','tts','audit','publish','ingest'])
    assert (root/'4_engine/tools/build-html.mjs').is_file()
    assert (root/'.voxforma/original-AGENTS.md').read_text()=='Original mandatory style and routing table'
    assert not (root/'4_engine/tools/batch_product_runner.py').exists()
    assert 'npx' not in (root/'4_engine/tools/example.sh').read_text()
    assert 'find -L' in (root/'4_engine/tools/append-random-outro.sh').read_text()
    (root/'saved-narration.wav').write_bytes(b'saved')
    retry=request.model_copy(update={'retry_of':jid})
    new=workspace.prepare('retry',retry,brand,[src])
    assert (new/'saved-narration.wav').read_bytes()==b'saved'
    assert len(list((new/'0_inbox_\u53e3\u64ad').iterdir()))==1


def test_full_tools_see_actual_frames_and_publish_immutable_four_files(client,tmp_path,vision_provider):
    a=media(client,tmp_path);b=media(client,tmp_path,'second.mp4','red')
    jid,root,brand,_=record([a,b])
    source=root/'source.mp4';source.write_bytes(store.asset_path(a['id']).read_bytes())
    result=post(client,jid,'analyze',{'input':'source.mp4','output':'work/source.json'})
    assert result.status_code==200,result.text
    assert len(result.json()['sample_times'])==9
    image_parts=vision_provider[-1]['messages'][1]['content']
    assert any(p.get('image_url',{}).get('url','').startswith('data:image/jpeg;base64,') for p in image_parts)
    video=root/'finished.mp4';ffmpeg('-f','lavfi','-i','color=c=green:s=1080x1920:r=30:d=0.3','-c:v','libx264','-preset','ultrafast',video)
    Image.new('RGB',(1080,1440),'green').save(root/'cover.jpg')
    (root/'pub.txt').write_text('Actual publication title');(root/'title.md').write_text('# Actual cover title')
    delivery={'source_id':a['id'],'video':'finished.mp4','cover':'cover.jpg','publication':'pub.txt','cover_title':'title.md','used':[a['id']]}
    assert post(client,jid,'publish',delivery).status_code==400
    audited=post(client,jid,'audit',{k:delivery[k] for k in ['source_id','video','cover']})
    assert audited.status_code==200 and audited.json()['passed'],audited.text
    assert post(client,jid,'publish',delivery).status_code==200
    assert post(client,jid,'publish',delivery).json()['existing']
    assert store.get_asset(a['id'])['use_count']==1
    assert post(client,jid,'fail',{'source_id':a['id'],'reason':'late error'}).status_code==409
    assert post(client,jid,'fail',{'source_id':b['id'],'reason':'Missing footage'}).status_code==200
    archived=client.get(f'/production/batches/{jid}/download')
    with zipfile.ZipFile(io.BytesIO(archived.content)) as archive:
        assert sorted(Path(p).suffix for p in archive.namelist())==['.jpg','.md','.mp4','.txt']
    old=client.get(f'/production/batches/{jid}/items/{a["id"]}/files/publication').content
    (root/'pub.txt').write_text('Later unrelated edits')
    assert client.get(f'/production/batches/{jid}/items/{a["id"]}/files/publication').content==old
    assert client.post(f'/production/agent/{jid}/vision',json={'image':'cover.jpg','prompt':'test'}).status_code==403
    assert post(client,jid,'vision',{'image':'../secret','prompt':'test'}).status_code==400


def test_batch_snapshot_idempotency_and_failed_only_retry(client,tmp_path,monkeypatch):
    a=media(client,tmp_path);b=media(client,tmp_path,'b.mp4','red');pack()
    brand=batch_store.save_brand(Brand(name='Saved brand',outro_ids=[a['id']]))
    monkeypatch.setattr(agent.agent_runtime,'status',lambda:{'installed':True})
    monkeypatch.setattr(agent.llm_skills,'resolve_skill',lambda _:SimpleNamespace(ready=True))
    async def execute(job,request,brand,sources):
        job_store.mark_running(job.id)
        batch=batch_store.get_batch(job.id)
        for i,item in enumerate(batch['items']):item['status']='done' if i==0 else 'failed'
        batch_store.update_items(job.id,batch['items']);job_store.mark_done(job.id)
    monkeypatch.setattr(agent,'execute',execute)
    body={'brand_id':brand['id'],'source_ids':[a['id'],b['id']],'request_id':'once','workflow':'product'}
    first=client.post('/production/batches',json=body);assert first.status_code==200,first.text
    jid=first.json()['id']
    assert client.post('/production/batches',json=body).json()['id']==jid
    import time
    for _ in range(50):
        if client.get('/production/batches/'+jid).json()['status']=='done':break
        time.sleep(.01)
    batch_store.save_brand(Brand(name='Changed brand',outro_ids=[b['id']]),brand['id'])
    retry=client.post('/production/batches/'+jid+'/retry');assert retry.status_code==200,retry.text
    assert retry.json()['request']['options']['source_ids']==[b['id']]
    assert retry.json()['request']['brand']['name']=='Saved brand'
    assert retry.json()['request']['options']['retry_of']==jid
    assert client.post('/production/ai/plan',json={}).status_code==410


def test_asr_uses_real_word_times_and_unloads(tmp_path,monkeypatch):
    import sys
    from types import ModuleType
    module=ModuleType('services.asr_backend');calls=[]
    class Backend:
        def transcribe(self,source,**kw):
            calls.append(kw)
            return {'segments':[{'start':.12,'end':.93,'text':'Actual words','words':[{'word':'Actual','start':.12,'end':.4},{'word':'words','start':.51,'end':.93}]}]}
        def unload(self):calls.append('unloaded')
    module.load_active_asr_backend=lambda **kw:(calls.append(kw) or Backend())
    monkeypatch.setitem(sys.modules,'services.asr_backend',module)
    monkeypatch.delenv('AVATAR_GPU_SOCKET',raising=False);monkeypatch.delenv('AVATAR_GPU_AUTOSTART',raising=False)
    monkeypatch.setenv('VOXFORMA_WORKSPACE',str(tmp_path));(tmp_path/'.voxforma').mkdir()
    out=tmp_path/'asr.json';agent_tools.asr(tmp_path/'a.wav',out)
    rows=json.loads(out.read_text())
    assert rows[0]['words'][1]=={'w':'words','s':.51,'e':.93}
    assert calls[0]=={'require_installed':True} and calls[-1]=='unloaded'


def test_intake_images_register_usable_brand_material(client,tmp_path):
    path=tmp_path/'picture.jpg';Image.new('RGB',(100,200),'red').save(path);source=upload(client,path)
    jid,root,brand,_=record([source],workflow='intake')
    (root/'clip.jpg').write_bytes(path.read_bytes())
    (root/'index.json').write_text(json.dumps({'items':[{'file':'clip.jpg','scene':'real scene','description':'Red fixture','trigger_keywords':['fixture']}]}))
    result=post(client,jid,'ingest',{'source_id':source['id'],'index':'index.json'})
    assert result.status_code==200,result.text
    indexed=store.get_asset(result.json()['indexed'][0]);assert indexed['kind']=='image' and indexed['scene']=='real scene'
    assert batch_store.get_batch(jid)['items'][0]['status']=='done'
    assert indexed['id'] in batch_store.get_brand(brand['id'])['material_ids']



@pytest.mark.parametrize('workflow', ['talking_head', 'intake'])
def test_cli_zero_exit_without_artifacts_never_counts_as_success(client,tmp_path,monkeypatch,workflow):
    source=media(client,tmp_path);pack()
    jid,root,brand,request=record([source],workflow)
    real_spawn=agent.spawn_subprocess
    async def spawn(*args,**kwargs):
        import sys
        return await real_spawn(sys.executable, '-c', 'print("Agent says done but produced no files")', **kwargs)
    monkeypatch.setattr(agent,'spawn_subprocess',spawn)
    job=agent.contexts[jid]['job']
    with pytest.raises(RuntimeError,match='did not complete'):
        asyncio.run(agent.execute(job,request,brand,[source]))
    assert batch_store.get_batch(jid)['status']=='failed'
    assert batch_store.get_batch(jid)['items'][0]['status']=='failed'
    assert jid not in agent.contexts

def test_cancel_terminates_owned_process_and_queue_keeps_working(client,tmp_path,monkeypatch):
    import sys,psutil
    source=media(client,tmp_path);pack()
    jid,root,brand,request=record([source])
    real_spawn=agent.spawn_subprocess
    async def spawn(*args,**kwargs):
        code="import subprocess,sys,time;from pathlib import Path;p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(300)']);Path('child.pid').write_text(str(p.pid));time.sleep(300)"
        return await real_spawn(sys.executable,'-c',code,**kwargs)
    monkeypatch.setattr(agent,'spawn_subprocess',spawn)
    async def scenario():
        q=JobQueue('cancel-agent')
        await q.start()
        try:
            await q.submit('owned',lambda job:agent.execute(job,request,brand,[source]),job_id=jid)
            for _ in range(150):
                if (root/'child.pid').exists():break
                await asyncio.sleep(.02)
            assert (root/'child.pid').exists()
            child=int((root/'child.pid').read_text())
            await q.cancel(jid);await q.wait(jid,timeout=12)
            assert batch_store.get_batch(jid)['status']=='cancelled'
            assert not psutil.pid_exists(child)
            async def after(job):return 'still working'
            next_job=await q.submit('next',after)
            await q.wait(next_job.id,timeout=3)
            assert next_job.result=='still working'
        finally:await q.stop()
    asyncio.run(scenario())
