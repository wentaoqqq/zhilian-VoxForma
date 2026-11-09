"""Real API/queue/workspace regressions with a fake provider and simulated CLI.

These tests verify evidence transport, isolation and orchestration, NOT model
classification/ASR quality or production rendering quality. No model is loaded.
"""
import asyncio
import base64
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from types import ModuleType, SimpleNamespace
import uuid
import wave

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
from openai import OpenAI
from PIL import Image
import pytest

from api.dependencies import require_consumer
from api.routers import production as api
from core import db, job_store
from core.job_queue import JobQueue
from production import agent, agent_tools, batch_store, conversations, product_store, store


class SimulatedProcess:
    """Subprocess-shaped event stream; endpoint calls still run the real gateway."""
    def __init__(self, finish=None):
        self.returncode = None
        self.stdout = self
        self.finish = finish
        self.emitted = False

    async def read(self, _size):
        if self.emitted:
            return b''
        self.emitted = True
        try:
            return await self.finish() if self.finish else b''
        finally:
            self.returncode = 0

    async def communicate(self):
        self.returncode = 0
        return b'', b''

    async def wait(self):
        self.returncode = 0
        return 0


@pytest.fixture
def harness(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'studio.db'))
    monkeypatch.setattr(store, 'ROOT', tmp_path / 'media')
    monkeypatch.setattr(agent, 'queue', JobQueue('conversation-test', concurrency=1))
    monkeypatch.setattr(api, 'queue', JobQueue('legacy-test'))
    monkeypatch.setattr(agent, 'contexts', {})
    monkeypatch.setattr(conversations, '_locks', {})
    monkeypatch.setattr(agent.agent_runtime, 'status', lambda: {'installed': True})
    monkeypatch.setattr(agent.agent_runtime, 'binary', lambda name: 'fixture-' + name)
    monkeypatch.setattr(agent.llm_skills, 'resolve_skill', lambda _: SimpleNamespace(ready=True))
    monkeypatch.setenv('HF_HUB_OFFLINE', '1')
    monkeypatch.setenv('HF_HUB_CACHE', str(tmp_path / 'empty-hf-cache'))
    db.ensure_schema()
    pack = store.ROOT / 'packs/roundtable'
    (pack / '4_engine/tools').mkdir(parents=True)
    (pack / '4_engine/tools/build-html.mjs').write_text('// fixture engine marker', encoding='utf-8')
    (pack / 'AGENTS.md').write_text('Fixture original workflow rules.', encoding='utf-8')
    app = FastAPI()
    app.dependency_overrides[require_consumer] = lambda: None
    app.include_router(api.router)
    h = SimpleNamespace(app=app, runs=[], provider_calls=[], asr_calls=[], action='discuss', tool_results=[],
                        category='detail', prepare_cli=None, tool_calls=[], release=threading.Event())
    h.release.set()

    def provider(request):
        body = json.loads(request.content)
        h.provider_calls.append(body)
        answer = ({'passed': True, 'issues': []} if 'Review the ACTUAL finished video' in json.dumps(body) else
                  {'category_id': h.category, 'description': 'Fixture evidence description', 'tags': ['visible-fixture']})
        return httpx.Response(200, json={
            'id': 'fixture', 'object': 'chat.completion', 'created': 1, 'model': 'fixture',
            'choices': [{'index': 0, 'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': json.dumps(answer)}}],
        })

    sdk = OpenAI(api_key='fixture', base_url='http://fixture/v1',
                 http_client=httpx.Client(transport=httpx.MockTransport(provider)), max_retries=0)
    monkeypatch.setattr(agent.llm_skills, 'resolve_skill_client', lambda _: SimpleNamespace(client=sdk, model='fixture'))

    async def spawn(*args, **kwargs):
        if 'production.agent_tools' in args and 'asr' in args:
            source, output = Path(args[-2]), Path(args[-1])
            h.asr_calls.append(source.read_bytes())
            # Exercise the actual ASR CLI adapter with an installed-backend stub.
            with monkeypatch.context() as local:
                local.setenv('VOXFORMA_WORKSPACE', str(output.parents[2]))
                local.delenv('AVATAR_GPU_SOCKET', raising=False)
                local.delenv('AVATAR_GPU_AUTOSTART', raising=False)
                agent_tools.asr(source, output)
            return SimulatedProcess()
        root = Path(kwargs['cwd'])
        batch_id = kwargs['env']['VOXFORMA_BATCH_ID']
        run = {'id': batch_id, 'args': args, 'root': root,
               'history': json.loads((root / 'conversation.json').read_text(encoding='utf-8')) if (root / 'conversation.json').exists() else {'messages': []},
               'inputs': json.loads((root / 'batch-inputs.json').read_text(encoding='utf-8')),
               'product': json.loads((root / 'product.json').read_text(encoding='utf-8')),
               'materials': json.loads((root / '1_assets/\u7d20\u6750\u7d22\u5f15.json').read_text(encoding='utf-8')),
               'prior_batches': json.loads((root / 'conversation-batches.json').read_text(encoding='utf-8')) if (root / 'conversation-batches.json').exists() else [],
               'provider_calls_before_cli': len(h.provider_calls)}
        h.runs.append(run)

        async def finish():
            while not h.release.is_set():
                await asyncio.sleep(.01)
            if h.prepare_cli:
                h.prepare_cli(root)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
                headers = {'Authorization': 'Bearer ' + kwargs['env']['VOXFORMA_AGENT_TOKEN']}
                for call in h.tool_calls:
                    tool, payload = call[:2]
                    expected = call[2] if len(call) > 2 else 200
                    result = await client.post(f'/production/agent/{batch_id}/{tool}', json=payload, headers=headers)
                    h.tool_results.append({'tool': tool, 'status': result.status_code, 'body': result.json()})
                    assert result.status_code == expected, result.text
                # Repeating the final callback must replace the same final turn.
                for _ in range(2 if batch_store.get_batch(batch_id)['request']['options'].get('conversation_id') else 0):
                    result = await client.post(f'/production/agent/{batch_id}/respond',
                                               json={'text': 'Fixture final answer', 'action': h.action}, headers=headers)
                    assert result.status_code == 200, result.text
            run['inputs_after'] = json.loads((root / 'batch-inputs.json').read_text(encoding='utf-8'))
            run['materials_after'] = json.loads((root / '1_assets/\u7d20\u6750\u7d22\u5f15.json').read_text(encoding='utf-8'))
            event = {'type': 'text', 'sessionID': 'session-' + batch_id,
                     'part': {'id': 'stable-part', 'text': 'Fixture streamed answer'}}
            # The runtime can retransmit an event without duplicating a message.
            return ((json.dumps(event) + '\n') * 2).encode()

        return SimulatedProcess(finish)

    monkeypatch.setattr(agent, 'spawn_subprocess', spawn)
    with TestClient(app) as client:
        h.client = client
        try:
            yield h
        finally:
            h.release.set()
            client.portal.call(agent.queue.stop)
    sdk.close()


def product(h, name='Product', **extra):
    result = h.client.post('/production/products', json={
        'name': name, 'categories': [{'id': 'detail', 'name': name + ' Details'}], **extra,
    })
    assert result.status_code == 200, result.text
    return result.json()


def conversation(h, p, video_type='product'):
    result = h.client.post('/production/conversations', json={'product_id': p['id'], 'video_type_id': video_type})
    assert result.status_code == 200, result.text
    return result.json()


def upload(h, p, video_type='product', filename='misleading-building.png', color='red', payload=None):
    if payload is None:
        output = io.BytesIO()
        Image.new('RGB', (64, 64), color).save(output, format='PNG')
        payload = output.getvalue()
    result = h.client.post('/production/assets', params={
        'product_id': p['id'], 'video_type_id': video_type, 'filename': filename,
    }, content=payload)
    assert result.status_code == 200, result.text
    return result.json()


def send(h, c, text='Discuss the current plan.', **extra):
    result = h.client.post(f"/production/conversations/{c['id']}/messages", json={'text': text, **extra})
    assert result.status_code == 200, result.text
    return result.json()


def wait(h, batch_id):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        result = h.client.get('/production/batches/' + batch_id)
        assert result.status_code == 200, result.text
        batch = result.json()
        if batch['status'] not in {'pending', 'running'}:
            return batch
        time.sleep(.02)
    pytest.fail('Conversation batch did not finish within 20 seconds: ' + batch_id)


def turn(h, c, **kwargs):
    result = send(h, c, **kwargs)
    return wait(h, result['batches'][-1]['id'])


def recorded_batch(p, source, video_type='product', status='done', item_status='done'):
    bid = uuid.uuid4().hex
    job_store.create(bid, type='production-batch')
    if status == 'done':
        job_store.mark_done(bid)
    elif status == 'running':
        job_store.mark_running(bid)
    with db.db_conn() as conn:
        conn.execute('INSERT INTO production_batches(id,request_json,items_json) VALUES (?,?,?)', (
            bid, json.dumps({'options': {'product_id': p['id'], 'video_type_id': video_type}}),
            json.dumps([{'source_id': source['id'], 'status': item_status}]),
        ))
    return bid


def test_create_conversations_and_filter_product_type(harness):
    h = harness
    p, q = product(h, 'Alpha'), product(h, 'Beta')
    a, b, c = conversation(h, p), conversation(h, p, 'narration'), conversation(h, q)
    assert a['messages'] == [] and a['batches'] == []
    result = h.client.get('/production/conversations', params={'product_id': p['id'], 'video_type_id': 'product'})
    assert [r['id'] for r in result.json()] == [a['id']]
    assert h.client.post('/production/conversations', json={'product_id': p['id'], 'video_type_id': 'foreign'}).status_code == 400
    assert h.client.get('/production/conversations/missing').status_code == 404
    assert {r['id'] for r in h.client.get('/production/conversations').json()} == {a['id'], b['id'], c['id']}


def test_discussion_without_materials_completes_and_message_retries_are_idempotent(harness):
    h = harness
    p = product(h, categories=[])
    c = conversation(h, p)
    first = turn(h, c, request_id='one-turn')
    assert first['status'] == 'done' and first['items'] == []
    again = send(h, c, request_id='one-turn')
    assert len(again['batches']) == 1
    assert len([m for m in again['messages'] if m['role'] == 'user']) == 1
    assert [m['text'] for m in again['messages'] if m['role'] == 'assistant'].count('Fixture final answer') == 1
    assert [m['text'] for m in again['messages'] if m['role'] == 'assistant'].count('Fixture streamed answer') == 1
    assert len(h.runs) == 1 and not h.provider_calls
    # Request keys are local to a conversation, never global to the client.
    other = turn(h, conversation(h, p), request_id='one-turn')
    assert other['id'] != first['id'] and other['status'] == 'done'
    assert not (store.ROOT / 'deliveries').exists()


def test_discussion_with_pending_inputs_needs_no_categories_or_vision(harness, monkeypatch):
    h = harness
    p = product(h, categories=[])
    source = upload(h, p)
    monkeypatch.setattr(agent.llm_skills, 'resolve_skill', lambda skill: SimpleNamespace(ready=skill == 'media_production'))
    batch = turn(h, conversation(h, p), text='Explain the available options; do not make a video.')
    assert batch['status'] == 'done', batch['error']
    assert batch['request']['options']['source_ids'] == [source['id']]
    assert batch['items'][0]['status'] == 'skipped'
    assert product_store.get_asset(p['id'], source['id'])['classification_status'] == 'pending'
    assert not h.provider_calls and not h.asr_calls and not h.tool_results
    assert not (store.ROOT / 'deliveries').exists()
    assert not (h.runs[0]['root'] / '.voxforma/classification').exists()


def test_produce_response_without_deliveries_cannot_claim_success(harness):
    h = harness
    p = product(h, categories=[])
    c = conversation(h, p)
    h.action = 'produce'
    batch = turn(h, c, text='Make a video.', source_ids=[])
    assert batch['status'] == 'failed'
    assert 'did not complete' in batch['error']
    assert not (store.ROOT / 'deliveries').exists()


def test_preflight_failure_leaves_no_phantom_turn_or_idempotency_key(harness, monkeypatch):
    h = harness
    p = product(h)
    c = conversation(h, p)
    monkeypatch.setattr(agent.agent_runtime, 'status', lambda: {'installed': False})
    result = h.client.post(f"/production/conversations/{c['id']}/messages", json={
        'text': 'Discuss.', 'source_ids': [], 'request_id': 'retry-after-setup',
    })
    assert result.status_code == 400
    current = h.client.get('/production/conversations/' + c['id']).json()
    assert current['messages'] == [] and current['batches'] == []
    monkeypatch.setattr(agent.agent_runtime, 'status', lambda: {'installed': True})
    assert turn(h, c, request_id='retry-after-setup', source_ids=[])['status'] == 'done'


def test_context_and_cli_sessions_never_cross_product_type_or_conversation(harness):
    h = harness
    p, q = product(h, 'Alpha'), product(h, 'Beta')
    a = conversation(h, p)
    first = turn(h, a, text='ALPHA_PRODUCT_PRIVATE_CONTEXT', source_ids=[])
    second = turn(h, a, text='Continue the same discussion.', source_ids=[])
    assert first['status'] == second['status'] == 'done'
    continued = next(r for r in h.runs if r['id'] == second['id'])
    assert '--session' in continued['args']
    assert 'session-' + first['id'] in continued['args']
    assert 'ALPHA_PRODUCT_PRIVATE_CONTEXT' in json.dumps(continued['history'])
    assert 'Fixture final answer' in json.dumps(continued['history'])
    for c, marker in [(conversation(h, p, 'narration'), 'ALPHA_NARRATION'), (conversation(h, q), 'BETA_PRODUCT'), (conversation(h, p), 'NEW_ALPHA_THREAD')]:
        batch = turn(h, c, text=marker, source_ids=[])
        assert batch['status'] == 'done'
        run = next(r for r in h.runs if r['id'] == batch['id'])
        assert '--session' not in run['args']
        assert [m['text'] for m in run['history']['messages']] == [marker]
        assert run['product']['id'] == c['product_id']


@pytest.mark.parametrize('invalid_scope', ['product', 'video_type', 'role'])
def test_sources_must_belong_to_conversation_product_type_and_input_role(harness, invalid_scope):
    h = harness
    p, q = product(h), product(h, 'Other')
    c = conversation(h, p)
    source = upload(h, q if invalid_scope == 'product' else p,
                    video_type='narration' if invalid_scope == 'video_type' else 'product')
    if invalid_scope == 'role':
        product_store.update_asset(p['id'], source['id'], role='material')
    result = h.client.post(f"/production/conversations/{c['id']}/messages", json={'text': 'Start', 'source_ids': [source['id']]})
    assert result.status_code == 400
    assert h.client.get('/production/conversations/' + c['id']).json()['messages'] == []
    assert not h.runs


def test_automatic_selection_uses_pending_dicts_and_keeps_classified_inputs(harness):
    h = harness
    p, q = product(h), product(h, 'Other')
    ready = upload(h, p, color='red')
    unclassified = upload(h, p, color='orange')
    done = upload(h, p, color='green')
    active = upload(h, p, color='blue')
    queued = upload(h, p, color='purple')
    material = upload(h, p, color='yellow')
    upload(h, p, video_type='narration', color='cyan')
    upload(h, q, color='black')
    product_store.classify_asset(p['id'], ready['id'], 'detail')
    product_store.classify_asset(p['id'], unclassified['id'], None)
    product_store.classify_asset(p['id'], material['id'], 'detail')
    product_store.update_asset(p['id'], material['id'], role='material')
    recorded_batch(p, done)
    recorded_batch(p, active, status='running', item_status='running')
    recorded_batch(p, queued, status='pending', item_status='pending')
    pending = product_store.pending_assets(p['id'], 'product')
    assert all(isinstance(a, dict) for a in pending)
    assert {a['id'] for a in pending} == {ready['id'], unclassified['id']}
    batch = turn(h, conversation(h, p))
    assert batch['status'] == 'done'
    assert set(batch['request']['options']['source_ids']) == {ready['id'], unclassified['id']}
    assert all(i['status'] == 'skipped' for i in batch['items'])
    assert {a['id'] for a in product_store.pending_assets(p['id'], 'product')} == {ready['id'], unclassified['id']}


def test_image_classification_transmits_actual_pixels_and_only_product_categories(harness):
    h = harness
    p = product(h, 'Alpha')
    product(h, 'Other', categories=[{'id': 'foreign', 'name': 'Foreign-only category'}])
    asset = upload(h, p)
    h.action, h.tool_calls = 'organize', [('classify', {})]
    batch = turn(h, conversation(h, p))
    assert batch['status'] == 'done', batch['error']
    assert len(h.provider_calls) == 1
    parts = h.provider_calls[0]['messages'][1]['content']
    prompt = next(part['text'] for part in parts if part['type'] == 'text')
    assert 'Alpha Details' in prompt and 'Foreign-only category' not in prompt
    payload = next(part['image_url']['url'] for part in parts if part['type'] == 'image_url')
    with Image.open(io.BytesIO(base64.b64decode(payload.split(',', 1)[1]))) as picture:
        red, green, blue = picture.getpixel((32, 32))
        assert red > 240 and green < 15 and blue < 15
    classified = product_store.get_asset(p['id'], asset['id'])
    assert classified['category_id'] == 'detail' and classified['classification_status'] == 'classified'
    assert 'category_id' not in store.get_asset(asset['id'])
    assert h.runs[0]['provider_calls_before_cli'] == 0
    assert h.runs[0]['inputs_after']['inputs'][0]['metadata']['classification_status'] == 'classified'


def test_unknown_content_stays_unclassified_and_foreign_category_fails_classification(harness):
    h = harness
    p = product(h)
    asset = upload(h, p)
    h.category = None
    h.action, h.tool_calls = 'organize', [('classify', {})]
    c = conversation(h, p)
    batch = turn(h, c)
    assert batch['status'] == 'done'
    current = product_store.get_asset(p['id'], asset['id'])
    assert current['classification_status'] == 'unclassified' and current['category_id'] is None
    assert product_store.pending_assets(p['id'], 'product')[0]['id'] == asset['id']
    h.category = 'foreign'
    failed = turn(h, c, text='Try another classification.')
    assert failed['items'][0]['status'] == 'failed'
    assert 'unknown category' in h.tool_results[-1]['body']['failed'][0]['error']
    assert len(h.runs) == 2 and not (store.ROOT / 'deliveries').exists()
    assert product_store.get_asset(p['id'], asset['id'])['category_id'] is None


def test_video_classification_extracts_real_contact_sheet_before_cli(harness, tmp_path):
    h = harness
    tool = shutil.which('ffmpeg')
    if not tool or not shutil.which('ffprobe'):
        pytest.skip('Contact-sheet evidence requires installed FFmpeg/FFprobe')
    path = tmp_path / 'misleading-video.mp4'
    subprocess.run([tool, '-nostdin', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                    'color=c=red:s=64x64:r=25:d=0.5', '-c:v', 'libx264', str(path)], capture_output=True, check=True)
    p = product(h)
    source = upload(h, p, filename=path.name, payload=path.read_bytes())
    h.action, h.tool_calls = 'organize', [('classify', {})]
    batch = turn(h, conversation(h, p))
    assert batch['status'] == 'done', batch['error']
    root = h.runs[0]['root']
    assert len(list((root / '.voxforma/classification').glob(source['id'] + '-*.jpg'))) == 9
    parts = h.provider_calls[0]['messages'][1]['content']
    payload = next(part['image_url']['url'] for part in parts if part['type'] == 'image_url')
    with Image.open(io.BytesIO(base64.b64decode(payload.split(',', 1)[1]))) as sheet:
        assert sheet.size == (1440, 960)
        red, green, blue = sheet.getpixel((240, 160))
        assert red > 200 and green < 30 and blue < 30


@pytest.mark.parametrize('kind', ['text', 'audio'])
def test_nonvisual_classification_uses_actual_contents_and_installed_asr_adapter(harness, monkeypatch, kind):
    h = harness
    evidence = 'The spoken or written content is about red fixtures.'
    p = product(h)
    if kind == 'text':
        payload, filename = evidence.encode(), 'misleading-architecture.txt'
    else:
        output = io.BytesIO()
        with wave.open(output, 'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(8000)
            wav.writeframes(b'\x01\x00' * 1600)
        payload, filename = output.getvalue(), 'misleading-architecture.wav'
        adapter = ModuleType('services.asr_backend')
        calls = []

        class Backend:
            def transcribe(self, source, **kwargs):
                assert Path(source).read_bytes() == payload
                calls.append(('transcribe', kwargs))
                return {'segments': [{'text': evidence, 'start': 0, 'end': .2}]}

            def unload(self):
                calls.append('unload')

        def load(**kwargs):
            calls.append(('load', kwargs))
            return Backend()

        adapter.load_active_asr_backend = load
        monkeypatch.setitem(sys.modules, 'services.asr_backend', adapter)
    upload(h, p, filename=filename, payload=payload)
    h.action, h.tool_calls = 'organize', [('classify', {})]
    batch = turn(h, conversation(h, p))
    assert batch['status'] == 'done', batch['error']
    body = h.provider_calls[0]
    assert evidence in body['messages'][1]['content']
    assert 'misleading-architecture' not in json.dumps(body)
    if kind == 'audio':
        assert h.asr_calls == [payload]
        assert calls == [('load', {'require_installed': True}), ('transcribe', {'word_timestamps': True}), 'unload']


def test_history_excludes_future_queued_user_messages(harness):
    h = harness
    p = product(h)
    c = conversation(h, p)
    h.release.clear()

    async def block_queue():
        async def block(_job):
            while not h.release.is_set():
                await asyncio.sleep(.01)
        await agent.queue.start()
        await agent.queue.submit('Queue blocker', block, job_id='fixture-blocker')

    h.client.portal.call(block_queue)
    turns = [send(h, c, text=label, source_ids=[]) for label in ('FIRST_ONLY', 'SECOND_FUTURE', 'THIRD_FUTURE')]
    ids = [t['batches'][-1]['id'] for t in turns]
    assert not h.runs
    h.release.set()
    for bid in ids:
        assert wait(h, bid)['status'] == 'done'
    histories = {r['id']: [m['text'] for m in r['history']['messages'] if m['role'] == 'user'] for r in h.runs}
    assert histories[ids[0]] == ['FIRST_ONLY']
    assert histories[ids[1]] == ['FIRST_ONLY', 'SECOND_FUTURE']
    assert histories[ids[2]] == ['FIRST_ONLY', 'SECOND_FUTURE', 'THIRD_FUTURE']
    runs = {r['id']: r for r in h.runs}
    # All three users were saved before the first assistant answered. The
    # second execution still needs the first answer, grouped before user #2.
    for index, bid in enumerate(ids):
        messages = runs[bid]['history']['messages']
        assert messages[-1]['role'] == 'user' and messages[-1]['batch_id'] == bid
        assert {m['batch_id'] for m in messages if m['role'] == 'assistant'} == set(ids[:index])
        assert [p['batch_id'] for p in runs[bid]['prior_batches']] == ids[:index]
        for earlier in ids[:index]:
            assert any(m['batch_id'] == earlier and m['text'] == 'Fixture final answer' for m in messages)


def test_removing_category_with_conversation_preserves_scoped_asset_and_file(harness):
    h = harness
    p = product(h)
    source = upload(h, p)
    c = conversation(h, p)
    h.action, h.tool_calls = 'organize', [('classify', {})]
    assert turn(h, c)['status'] == 'done'
    p['categories'] = []
    result = h.client.put('/production/products/' + p['id'], json=p)
    assert result.status_code == 200, result.text
    current = product_store.get_asset(p['id'], source['id'])
    assert current['category_id'] is None and current['classification_status'] == 'pending'
    assert current['description'] == 'Fixture evidence description'
    assert store.asset_path(source['id']).is_file()
    assert h.client.get('/production/conversations/' + c['id']).json()['messages']
    assert h.client.delete('/production/products/' + p['id']).status_code == 200
    assert store.asset_path(source['id']).is_file()
    assert store.get_asset(source['id'])['id'] == source['id']


def test_ingest_existing_source_promotes_material_association_for_broll(harness):
    h = harness
    p = product(h)
    source = upload(h, p)

    def prepare(root):
        shutil.copyfile(store.asset_path(source['id']), root / 'indexed.png')
        (root / 'index.json').write_text(json.dumps({'items': [{
            'file': 'indexed.png', 'category_id': 'detail', 'description': 'Indexed evidence', 'trigger_keywords': ['red'],
        }]}), encoding='utf-8')

    h.prepare_cli = prepare
    h.action = 'organize'
    h.tool_calls = [('classify', {}), ('ingest', {'source_id': source['id'], 'index': 'index.json'})]
    batch = turn(h, conversation(h, p))
    assert batch['status'] == 'done', batch['error']
    assert product_store.get_asset(p['id'], source['id'])['role'] == 'material'
    assert product_store.get_product(p['id'])['material_ids'] == [source['id']]
    assert product_store.pending_assets(p['id'], 'product') == []
    # A subsequent upload must preserve the promoted role and classification.
    repeated = upload(h, p)
    assert repeated['role'] == 'material' and repeated['classification_status'] == 'classified'
    assert repeated['description'] == 'Indexed evidence'


def test_classify_tool_classifies_pending_material_library_before_broll_selection(harness):
    h = harness
    p = product(h)
    source = upload(h, p, color='red')
    material = upload(h, p, color='green')
    product_store.update_asset(p['id'], material['id'], role='material')
    assert product_store.get_product(p['id'])['material_ids'] == [material['id']]
    h.action, h.tool_calls = 'organize', [('classify', {})]
    batch = turn(h, conversation(h, p), source_ids=[source['id']])
    assert batch['status'] == 'done', batch['error']
    indexed = next(i for i in h.runs[0]['materials_after']['items'] if i['shot_id'] == material['id'])
    assert indexed['category_id'] == 'detail', 'The classify tool must refresh pending role=material before B-roll selection.'
    assert indexed['description'] == 'Fixture evidence description'
    assert product_store.get_asset(p['id'], material['id'])['classification_status'] == 'classified'
    assert len(h.provider_calls) == 2


def test_retry_creates_a_new_user_turn_and_skipped_discussion_is_not_a_delivery(harness):
    h = harness
    p = product(h)
    source = upload(h, p)
    c = conversation(h, p)
    h.action = 'produce'
    failed = turn(h, c, text='Produce this input.')
    assert failed['status'] == 'failed' and failed['items'][0]['status'] == 'failed'
    h.action, h.tool_calls = 'organize', [('classify', {})]
    result = h.client.post('/production/batches/' + failed['id'] + '/retry')
    assert result.status_code == 200, result.text
    retry = wait(h, result.json()['id'])
    assert retry['status'] == 'done' and retry['items'][0]['status'] == 'skipped'
    assert retry['request']['options']['retry_of'] == failed['id']
    current = h.client.get('/production/conversations/' + c['id']).json()
    users = [m for m in current['messages'] if m['role'] == 'user']
    assert [m['batch_id'] for m in users] == [failed['id'], retry['id']]
    assert {b['id'] for b in current['batches']} == {failed['id'], retry['id']}
    assert product_store.pending_assets(p['id'], 'product')[0]['id'] == source['id']
    assert h.client.get('/production/batches/' + retry['id'] + '/download').status_code == 409
    assert h.client.post('/production/batches/' + retry['id'] + '/retry').status_code == 400


@pytest.mark.parametrize('invalid_scope', ['product', 'video_type', 'role'])
def test_select_tool_rejects_foreign_product_type_or_non_input_without_mutating_batch(harness, invalid_scope):
    h = harness
    p, q = product(h), product(h, 'Foreign')
    source = upload(h, q if invalid_scope == 'product' else p,
                    video_type='narration' if invalid_scope == 'video_type' else 'product')
    if invalid_scope == 'role':
        product_store.update_asset(p['id'], source['id'], role='material')
    h.tool_calls = [('select', {'source_ids': [source['id']]}, 400)]
    batch = turn(h, conversation(h, p), source_ids=[])
    assert batch['status'] == 'done' and batch['items'] == []
    assert batch['request']['options']['source_ids'] == []
    assert h.runs[0]['inputs_after']['inputs'] == []
    assert not h.provider_calls


def test_direct_product_batch_classifies_before_starting_cli(harness):
    h = harness
    p = product(h)
    source = upload(h, p)
    result = h.client.post('/production/batches', json={
        'product_id': p['id'], 'video_type_id': 'product', 'source_ids': [source['id']],
    })
    assert result.status_code == 200, result.text
    batch = wait(h, result.json()['id'])
    # The simulated executor publishes no artifact; classification success is
    # independent from, and cannot establish, production success.
    assert batch['status'] == 'failed'
    assert h.runs[0]['provider_calls_before_cli'] == 1
    assert h.runs[0]['inputs']['inputs'][0]['metadata']['classification_status'] == 'classified'


def test_explicit_classify_audit_publish_and_history_select_create_immutable_deliveries(harness, tmp_path):
    h = harness
    tool = shutil.which('ffmpeg')
    if not tool or not shutil.which('ffprobe'):
        pytest.skip('Publication flow requires installed FFmpeg/FFprobe')
    video = tmp_path / 'finished.mp4'
    subprocess.run([tool, '-nostdin', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                    'color=c=red:s=1080x1920:r=30:d=0.3', '-c:v', 'libx264', '-preset', 'ultrafast', str(video)],
                   capture_output=True, check=True)
    p = product(h)
    source = upload(h, p)
    c = conversation(h, p)
    revision = ['First immutable publication']

    def prepare(root):
        shutil.copyfile(video, root / 'finished.mp4')
        Image.new('RGB', (1080, 1440), 'red').save(root / 'cover.jpg')
        (root / 'publication.txt').write_text(revision[0], encoding='utf-8')
        (root / 'cover.md').write_text('# Cover fixture', encoding='utf-8')

    delivery = {'source_id': source['id'], 'video': 'finished.mp4', 'cover': 'cover.jpg',
                'publication': 'publication.txt', 'cover_title': 'cover.md', 'used': []}
    audit = {key: delivery[key] for key in ('source_id', 'video', 'cover')}
    h.action, h.prepare_cli = 'produce', prepare
    h.tool_calls = [('publish', delivery, 400), ('classify', {}), ('publish', delivery, 400),
                    ('audit', audit), ('publish', delivery)]
    first = turn(h, c, text='Start making the selected input.')
    assert first['status'] == 'done', first['error']
    assert first['items'][0]['status'] == 'done'
    assert 'Classify' in h.tool_results[0]['body']['detail']
    assert 'audit' in h.tool_results[2]['body']['detail']
    assert len(h.provider_calls) == 2  # Input image evidence + finished video/cover evidence.
    assert product_store.pending_assets(p['id'], 'product') == []
    url = f"/production/batches/{first['id']}/items/{source['id']}/files/publication"
    assert h.client.get(url).text == 'First immutable publication'
    assert h.client.get('/production/batches/' + first['id'] + '/download').status_code == 200
    revision[0] = 'Revised publication'
    h.tool_calls = [('select', {'source_ids': [source['id'], source['id']]}), ('classify', {}),
                    ('audit', audit), ('publish', delivery)]
    second = turn(h, c, text='Revise the previous output using its original input.')
    assert second['status'] == 'done', second['error']
    assert second['request']['options']['source_ids'] == [source['id']]
    assert h.runs[1]['inputs']['inputs'] == []  # Completed input was not auto-selected.
    assert [i['id'] for i in h.runs[1]['inputs_after']['inputs']] == [source['id']]
    assert h.runs[1]['prior_batches'][0]['batch_id'] == first['id']
    assert h.runs[1]['prior_batches'][0]['items'][0]['files']['publication']
    assert h.client.get(url).text == 'First immutable publication'
    assert h.client.get(f"/production/batches/{second['id']}/items/{source['id']}/files/publication").text == 'Revised publication'
