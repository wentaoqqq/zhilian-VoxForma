"""Full OpenCode execution agent. The application owns batches and deliveries."""
import asyncio
from contextlib import suppress
import json
import os
from pathlib import Path
import secrets
import shutil
import sys
import time
import uuid

from core import job_store
from core.db import db_conn
from core.job_queue import JobQueue
from production import store, batch_store, agent_runtime, workspace
from production.batch_models import BatchRequest
from services import llm_skills
from services.ffmpeg_utils import spawn_subprocess

queue = JobQueue('production-agent', concurrency=1)
contexts = {}


def status():
    runtime = agent_runtime.status()
    with db_conn() as db:
        row = db.execute("SELECT id,status,error FROM jobs WHERE type='production-runtime' ORDER BY created_at DESC LIMIT 1").fetchone()
    runtime['job'] = dict(row) if row else None
    return {'planner': llm_skills.describe('media_production'), 'vision': llm_skills.describe('media_vision'), 'runtime': runtime}


def preflight(request, brand):
    workspace.pack_root(brand['pack_id'])
    if not agent_runtime.status()['installed']:
        raise ValueError('Install the production-agent runtime once in setup before starting.')
    for skill in (['media_production'] if request.conversation_id else ['media_production', 'media_vision']):
        if not llm_skills.resolve_skill(skill).ready:
            raise ValueError('Configure production planning and vision providers in AI settings first.')
    ids = list(dict.fromkeys(request.source_ids))
    if request.product_id:
        from production import product_store
        video_type = next((t for t in brand['video_types'] if t['id'] == request.video_type_id), None)
        if not video_type:
            raise ValueError('This video type does not belong to the selected product.')
        request.workflow = video_type['workflow']
        sources = [product_store.get_asset(request.product_id, asset_id) for asset_id in ids]
        if any(s.get('video_type_id') != request.video_type_id or s.get('role') != 'input' for s in sources):
            raise ValueError('Inputs must belong to the selected product and video type.')
        if sources and not brand['categories'] and not request.conversation_id:
            raise ValueError('Add material categories to this product before processing uploaded inputs.')
    else:
        sources = [store.get_asset(asset_id) for asset_id in ids]
    allowed = {'product': {'video'}, 'talking_head': {'video'}, 'narration': {'audio', 'text'}, 'intake': {'video', 'image'}}[request.workflow]
    if not request.product_id and any(a.get('kind', 'video') not in allowed for a in sources):
        raise ValueError('Some inputs do not match the chosen production line.')
    for asset_id in ids + brand['outro_ids'] + brand['material_ids'] + brand['sticker_ids']:
        if not store.asset_path(asset_id).is_file():
            raise ValueError('A configured media file is missing. Upload it again.')
    if not request.product_id and request.workflow == 'product' and not brand['outro_ids']:
        raise ValueError('Add a brand-outro pool once in the brand settings.')
    if not request.conversation_id and request.workflow == 'narration' and any(a.get('kind') == 'text' for a in sources) and not brand.get('voice_profile'):
        raise ValueError('Choose a saved Studio voice once in the brand settings for script narration.')
    return sources


async def submit(request: BatchRequest, *, brand_snapshot=None, product_snapshot=None, batch_id=None):
    if request.request_id:
        with db_conn() as db:
            existing = db.execute('SELECT id FROM production_batches WHERE request_key=?', (request.request_id,)).fetchone()
        if existing:
            return batch_store.get_batch(existing[0])
    if request.product_id:
        from production import product_store
        brand = product_snapshot or product_store.get_product(request.product_id)
    else:
        brand = brand_snapshot or batch_store.get_brand(request.brand_id)
    sources = preflight(request, brand)
    batch_id = batch_id or uuid.uuid4().hex
    items = [{'source_id': a['id'], 'filename': a['filename'], 'status': 'pending', 'artifacts': {}, 'error': None} for a in sources]
    snapshot = {'options': request.model_dump(), 'brand': brand, 'sources': sources}
    if request.product_id:
        snapshot['product'] = brand
    with db_conn() as db:
        db.execute("INSERT INTO jobs(id,type,status,created_at,updated_at,meta_json) VALUES (?,'production-batch','pending',?,?,?)", (batch_id, time.time(), time.time(), json.dumps({'count': len(items), 'workflow': request.workflow})))
        db.execute('INSERT INTO production_batches(id,request_key,request_json,items_json) VALUES (?,?,?,?)', (batch_id, request.request_id, json.dumps(snapshot, ensure_ascii=False), json.dumps(items, ensure_ascii=False)))
    queue.purge_finished()
    await queue.start()
    await queue.submit(brand['name'], lambda job: execute(job, request, brand, sources), job_id=batch_id)
    return batch_store.get_batch(batch_id)


def child_env(batch_id, root, token):
    env = agent_runtime.tool_env()
    from core.config import DATA_DIR
    backend = Path(__file__).resolve().parents[1]
    env['PYTHONPATH'] = os.pathsep.join([str(agent_runtime.runtime_root() / 'python'), str(backend / 'integrations/avatar'), str(backend), str(backend.parent)])
    env['VOXFORMA_PYTHON'] = sys.executable
    env['PYTHON'] = sys.executable
    env['VOXFORMA_WORKSPACE'] = str(root)
    env['VOXFORMA_BATCH_ID'] = batch_id
    env['VOXFORMA_AGENT_TOKEN'] = token
    env['VOXFORMA_AGENT_URL'] = os.environ.get('AVATAR_VOICE_URL', 'http://127.0.0.1:' + os.environ.get('OMNIVOICE_PORT', '3900')).rstrip('/')
    env['HF_HUB_OFFLINE'] = '1'
    # Dedicated OpenCode state, without changing HOME or borrowing user logins.
    state = store.ROOT / 'agent-profile'
    for variable, folder in [('XDG_CONFIG_HOME', 'config'), ('XDG_DATA_HOME', 'data'), ('XDG_CACHE_HOME', 'cache')]:
        env[variable] = str(state / folder)
    env['OPENCODE_DISABLE_AUTOUPDATE'] = 'true'
    env['OPENCODE_DISABLE_SHARE'] = 'true'
    env['OPENCODE_DISABLE_CLAUDE_CODE'] = 'true'
    env['OPENCODE_DISABLE_PROJECT_CONFIG'] = 'true'
    env['OPENCODE_CONFIG_CONTENT'] = json.dumps({
        '$schema': 'https://opencode.ai/config.json', 'model': 'voxforma/production', 'autoupdate': False, 'share': 'disabled',
        'instructions': [str(root / 'AGENTS.md')], 'enabled_providers': ['voxforma'],
        'provider': {'voxforma': {'npm': '@ai-sdk/openai-compatible', 'name': 'VoxForma production gateway', 'options': {'baseURL': env['VOXFORMA_AGENT_URL'] + '/production/agent/' + batch_id + '/v1', 'apiKey': token}, 'models': {'production': {'name': 'Configured production AI', 'limit': {'context': 128000, 'output': 8192}, 'modalities': {'input': ['text'], 'output': ['text']}}}}},
        'permission': {'bash': 'allow', 'edit': 'allow', 'read': 'allow', 'external_directory': 'deny', 'question': 'deny', 'task': 'deny', 'webfetch': 'deny', 'websearch': 'deny'},
    })
    return env


def kill_tree(proc):
    import psutil
    try:
        parent = psutil.Process(proc.pid)
        children = parent.children(recursive=True)
        for child in reversed(children):
            with suppress(psutil.NoSuchProcess, psutil.AccessDenied):
                child.terminate()
        with suppress(psutil.NoSuchProcess):
            parent.terminate()
        _, alive = psutil.wait_procs(children + [parent], timeout=3)
        for child in alive:
            with suppress(psutil.NoSuchProcess, psutil.AccessDenied):
                child.kill()
    except psutil.NoSuchProcess:
        pass


async def execute(job, request, brand, sources):
    job_store.mark_running(job.id)
    proc = None
    try:
        root = await asyncio.to_thread(workspace.prepare, job.id, request, brand, sources)
        token = secrets.token_urlsafe(32)
        contexts[job.id] = {'token': token, 'root': root, 'brand': brand, 'sources': sources, 'job': job, 'request': request}
        ctx = contexts[job.id]
        if request.product_id:
            from production.classification import classify_sources
            ctx['product'] = brand
            if not request.conversation_id:
                await classify_sources(ctx, request, brand, sources)
        args = []
        if request.conversation_id:
            from production import conversations
            session = conversations.previous_session(request.conversation_id, job.id)
            if session:
                args = ['--session', session]
        prompt = ('Read the CURRENT working directory AGENTS.md, conversation.json and batch-inputs.json. '
                  'Answer the latest user message in context. Determine whether the user is discussing, organizing material or asking to produce videos. '
                  'Do not start editing merely because pending files exist. When asked to start, execute the FULL original workflow for the selected video type, '
                  'use real tools/visual verification, publish actual deliveries, and continue after individual failures. '
                  'Call the respond tool with your final answer and action (discuss, organize or produce).') if request.conversation_id else 'Read AGENTS.md and batch-inputs.json. Execute the entire configured workflow for every input now. Use the tools, original engine and visual verification; publish actual deliveries and continue after individual failures.'
        batch_store.log(job.id, 'Starting the full editing agent for ' + str(len(sources)) + ' inputs.', 'stage')
        proc = await spawn_subprocess(agent_runtime.binary('opencode'), 'run', '--format', 'json', '--agent', 'build', *args, prompt, cwd=str(root), env=child_env(job.id, root, token), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        async def event_lines():
            pending = b''
            while True:
                if getattr(proc, 'uses_sync_pipes', False):
                    chunk = await asyncio.to_thread(proc.stdout.read1, 65536)
                else:
                    chunk = await proc.stdout.read(65536)
                if not chunk:
                    break
                pending += chunk
                while b'\n' in pending:
                    line, pending = pending.split(b'\n', 1)
                    yield line
            if pending:
                yield pending
        async def read_events():
            async for raw in event_lines():
                line = raw.decode('utf-8', errors='replace').strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    sid = event.get('sessionID') or event.get('session_id')
                    if sid:
                        with db_conn() as db:
                            db.execute('UPDATE production_batches SET session_id=? WHERE id=?', (sid, job.id))
                    part = event.get('part', {})
                    message = part.get('text') or part.get('state', {}).get('title') or event.get('error', {}).get('message')
                    if message:
                        batch_store.log(job.id, message, event.get('type', 'agent'))
                        if request.conversation_id and event.get('type') == 'text':
                            from production import conversations
                            mid = uuid.uuid5(uuid.NAMESPACE_URL, job.id + ':' + str(part.get('id') or event.get('timestamp') or message)).hex
                            conversations.append(request.conversation_id, 'assistant', message, batch_id=job.id, message_id=mid)
                    state = part.get('state', {})
                    if event.get('type') == 'tool_use' and (state.get('error') or state.get('output')):
                        batch_store.log(job.id, str(state.get('error') or state['output'])[-10000:], 'tool_result')
                except (ValueError, TypeError, AttributeError):
                    batch_store.log(job.id, line, 'runtime')
            return await proc.wait()
        task = asyncio.create_task(read_events())
        cancelled = asyncio.create_task(job.cancel_event.wait())
        try:
            done, _ = await asyncio.wait([task, cancelled], timeout=12 * 3600, return_when=asyncio.FIRST_COMPLETED)
            if cancelled in done:
                raise asyncio.CancelledError()
            if task not in done:
                raise RuntimeError('Agent execution exceeded the batch time limit.')
            code = await task
            batch = batch_store.get_batch(job.id)
            action = ctx.get('turn_action', 'produce' if not request.conversation_id else None)
            if request.conversation_id and not action:
                raise RuntimeError('The agent did not finish its conversation turn with the respond tool. Review the execution log.')
            for item in batch['items']:
                if item['status'] == 'pending':
                    if action == 'produce':
                        item.update(status='failed', error='The agent did not publish a delivery for this input. Review its execution log.')
                    else:
                        item.update(status='skipped', error=None)
            batch_store.update_items(job.id, batch['items'])
            succeeded = sum(item['status'] == 'done' for item in batch['items'])
            if code or (action == 'produce' and not succeeded):
                raise RuntimeError('Agent batch did not complete. See per-input results and the execution log.')
            ledger = root / '\u53f0\u8d26.md'
            if ledger.is_file():
                saved = store.ROOT / ('product-state' if request.product_id else 'brand-state') / brand['id'] / ledger.name
                saved.parent.mkdir(parents=True, exist_ok=True)
                await asyncio.to_thread(shutil.copyfile, ledger, saved)
            job_store.mark_done(job.id)
        finally:
            cancelled.cancel()
            if not task.done():
                task.cancel()
            with suppress(asyncio.CancelledError):
                await cancelled
            with suppress(asyncio.CancelledError):
                await task
    except asyncio.CancelledError:
        job_store.mark_cancelled(job.id)
        if not job.is_cancelled:
            raise
    except Exception as exc:
        batch_store.log(job.id, str(exc), 'error')
        job_store.mark_failed(job.id, str(exc))
        if request.conversation_id:
            from production import conversations
            conversations.append(request.conversation_id, 'system', str(exc), batch_id=job.id)
        raise
    finally:
        if proc is not None and proc.returncode is None:
            await asyncio.to_thread(kill_tree, proc)
            await proc.wait()
        contexts.pop(job.id, None)
