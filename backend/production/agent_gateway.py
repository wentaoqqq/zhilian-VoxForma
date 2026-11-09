"""Job-scoped model/tools gateway. Provider credentials stay in the application."""
import asyncio
import base64
import io
import json
import hashlib
from pathlib import Path
import secrets
import shutil
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from PIL import Image, ImageDraw
import httpx

from production import agent, batch_store, store
from production.render import command, probe
from services import llm_skills
from core.db import db_conn

router = APIRouter()


@router.post('/agent/{batch_id}/classify')
async def classify_inputs(batch_id: str, request: Request):
    ctx = context(batch_id, request)
    if not ctx.get('product'):
        raise HTTPException(400, 'Choose a product before classifying material.')
    from production.classification import classify_sources
    from production import product_store
    failed, classified = [], []
    inputs = {s['id'] for s in ctx['sources']}
    sources = list(ctx['sources'])
    sources.extend(product_store.get_asset(ctx['product']['id'], aid) for aid in ctx['product']['material_ids'] if aid not in inputs)
    for source in sources:
        try:
            await classify_sources(ctx, ctx['request'], ctx['product'], [source])
            classified.append(source['id'])
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failed.append({'source_id': source['id'], 'error': str(exc)})
            if source['id'] in inputs:
                batch_store.update_item(batch_id, source['id'], {'status': 'failed', 'error': str(exc)[:4000]})
            batch_store.log(batch_id, source['filename'] + ': classification failed: ' + str(exc), 'error')
    return {'classified': classified, 'failed': failed,
            'inputs': [product_store.get_asset(ctx['product']['id'], sid) for sid in classified]}


@router.post('/agent/{batch_id}/select')
async def select_inputs(batch_id: str, request: Request):
    """The agent resolves revision requests without requiring another upload."""
    ctx = context(batch_id, request)
    if not ctx.get('product'):
        raise HTTPException(400, 'Input selection requires a product conversation.')
    from production import product_store, workspace
    from production.batch_models import WORKFLOW_DIRS
    from production.classification import classify_sources
    data = await request.json()
    ids = data.get('source_ids')
    if not isinstance(ids, list) or not ids or len(ids) > 1000 or any(not isinstance(i, str) for i in ids):
        raise HTTPException(400, 'Select product input IDs from the conversation history.')
    options = ctx['request']
    sources = []
    try:
        for aid in dict.fromkeys(ids):
            asset = product_store.get_asset(options.product_id, aid)
            if asset.get('video_type_id') != options.video_type_id or asset.get('role') != 'input':
                raise ValueError('Inputs must belong to this product and video type.')
            if aid not in {a['id'] for a in ctx['sources']}:
                sources.append(asset)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    root = ctx['root']
    input_file = root / 'batch-inputs.json'
    inputs = json.loads(input_file.read_text(encoding='utf-8'))
    for asset in sources:
        name = asset['id'][:10] + '-' + workspace.safe_name(asset['filename'])
        target = root / WORKFLOW_DIRS[options.workflow] / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            try:
                target.symlink_to(store.asset_path(asset['id']))
            except OSError:
                await asyncio.to_thread(shutil.copyfile, store.asset_path(asset['id']), target)
        inputs['inputs'].append({'id': asset['id'], 'file': str(target.relative_to(root)).replace('\\', '/'), 'filename': asset['filename'], 'metadata': asset})
    input_file.write_text(json.dumps(inputs, ensure_ascii=False, indent=2), encoding='utf-8')
    with db_conn() as db:
        row = db.execute('SELECT request_json,items_json FROM production_batches WHERE id=?', (batch_id,)).fetchone()
        snapshot, items = json.loads(row[0]), json.loads(row[1])
        for asset in sources:
            snapshot['options']['source_ids'].append(asset['id'])
            snapshot['sources'].append(asset)
            items.append({'source_id': asset['id'], 'filename': asset['filename'], 'status': 'pending', 'artifacts': {}, 'error': None})
        db.execute('UPDATE production_batches SET request_json=?,items_json=? WHERE id=?', (json.dumps(snapshot, ensure_ascii=False), json.dumps(items, ensure_ascii=False), batch_id))
    options.source_ids.extend(a['id'] for a in sources)
    ctx['sources'].extend(sources)
    await classify_sources(ctx, options, ctx['product'], sources)
    return {'selected': [a['id'] for a in ctx['sources']]}


@router.post('/agent/{batch_id}/respond')
async def respond(batch_id: str, request: Request):
    ctx = context(batch_id, request)
    data = await request.json()
    batch = batch_store.get_batch(batch_id)
    cid = batch['request']['options'].get('conversation_id')
    text, action = data.get('text'), data.get('action')
    if not cid or not isinstance(text, str) or not text.strip() or len(text) > 40000 or action not in {'discuss', 'organize', 'produce'}:
        raise HTTPException(400, 'Finish a conversation with an answer and a valid action.')
    # A reply cannot relabel an attempted render as a discussion to hide failure.
    ctx['turn_action'] = 'produce' if ctx.get('production_started') else action
    with db_conn() as db:
        row = db.execute('SELECT request_json FROM production_batches WHERE id=?', (batch_id,)).fetchone()
        snapshot = json.loads(row[0])
        snapshot['turn_action'] = ctx['turn_action']
        db.execute('UPDATE production_batches SET request_json=? WHERE id=?', (json.dumps(snapshot, ensure_ascii=False), batch_id))
    from production import conversations
    import uuid
    conversations.append(cid, 'assistant', text, batch_id=batch_id,
                         message_id=uuid.uuid5(uuid.NAMESPACE_URL, batch_id + ':final').hex)
    return {'saved': True, 'action': ctx['turn_action']}


def context(batch_id, request):
    ctx = agent.contexts.get(batch_id)
    supplied = request.headers.get('Authorization', '').removeprefix('Bearer ')
    if not ctx or not secrets.compare_digest(supplied, ctx['token']):
        raise HTTPException(403, 'This production-agent session has ended or is not authorized.')
    if ctx['job'].is_cancelled:
        raise HTTPException(409, 'This batch was cancelled.')
    return ctx


def work_path(ctx, value, *, exists=True):
    root = ctx['root'].absolute()
    p = Path(value)
    if not p.is_absolute():
        p = root / p
    # Staged material files can be symlinks; only their workspace names are
    # accepted. Tools never accept an arbitrary host path from a model.
    if '..' in p.parts or not p.absolute().is_relative_to(root):
        raise HTTPException(400, 'Tool paths must be inside this batch workspace.')
    if exists and not p.is_file():
        raise HTTPException(400, 'The tool input file does not exist.')
    return p


def content(response):
    text = response.choices[0].message.content
    return text or ''


def parse_json(text):
    value = text.strip()
    if value.startswith('```'):
        value = value.split('\n', 1)[1].rsplit('```', 1)[0]
    return json.loads(value)


def vision(images, prompt):
    selected = llm_skills.resolve_skill_client('media_vision')
    if not selected:
        raise HTTPException(503, 'Configure a vision-capable provider for media vision in AI settings.')
    parts = [{'type': 'text', 'text': prompt}]
    for path in images:
        with Image.open(path) as image:
            image = image.convert('RGB')
            image.thumbnail((1800, 1800))
            output = io.BytesIO()
            image.save(output, format='JPEG', quality=88)
        parts.append({'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + base64.b64encode(output.getvalue()).decode()}})
    return content(selected.client.chat.completions.create(model=selected.model, messages=[{'role': 'system', 'content': 'You inspect actual video frames and covers for a production editing agent. Describe only visible evidence. Follow the requested output format.'}, {'role': 'user', 'content': parts}], timeout=120))


async def contact_sheet(ctx, video, dest):
    info = await probe(video)
    images, times = [], []
    dest.parent.mkdir(parents=True, exist_ok=True)
    for index in range(9):
        timestamp = max(0, min(info['duration'] - 0.08, info['duration'] * index / 9))
        frame = dest.with_name(dest.stem + f'-{index}.jpg')
        await command(['-ss', str(timestamp), '-i', str(video), '-frames:v', '1', '-vf', 'scale=480:-2', str(frame)], ctx['job'])
        with Image.open(frame) as image:
            tile = Image.new('RGB', (480, 320), '#111111')
            thumbnail = image.convert('RGB'); thumbnail.thumbnail((480, 285))
            tile.paste(thumbnail, ((480 - thumbnail.width) // 2, 28 + (285 - thumbnail.height) // 2))
            ImageDraw.Draw(tile).text((12, 8), f'{index + 1} : {timestamp:.3f}s', fill='white')
            images.append(tile)
        times.append(timestamp)
    sheet = Image.new('RGB', (1440, 960))
    for index, image in enumerate(images):
        sheet.paste(image, ((index % 3) * 480, (index // 3) * 320))
    sheet.save(dest, quality=90)
    return info, times


@router.post('/agent/{batch_id}/v1/chat/completions')
async def completion(batch_id: str, request: Request):
    context(batch_id, request)
    selected = llm_skills.resolve_skill_client('media_production')
    if not selected:
        raise HTTPException(503, 'Production planning provider is not configured.')
    body = await request.json()
    body['model'] = selected.model
    body['stream'] = True
    body['timeout'] = 180
    response = await asyncio.to_thread(selected.client.chat.completions.create, **body)
    marker = object()
    async def stream():
        try:
            while True:
                chunk = await asyncio.to_thread(next, response, marker)
                if chunk is marker:
                    break
                data = chunk.model_dump_json(exclude_none=True) if hasattr(chunk, 'model_dump_json') else json.dumps(chunk)
                yield 'data: ' + data + '\n\n'
            yield 'data: [DONE]\n\n'
        finally:
            if hasattr(response, 'close'):
                await asyncio.to_thread(response.close)
    return StreamingResponse(stream(), media_type='text/event-stream')


@router.post('/agent/{batch_id}/vision')
async def inspect_image(batch_id: str, request: Request):
    ctx = context(batch_id, request)
    data = await request.json()
    image = work_path(ctx, data['image'])
    batch_store.log(batch_id, 'Vision: ' + image.name, 'vision')
    result = await asyncio.to_thread(vision, [image], str(data['prompt'])[:20000])
    return {'analysis': result}


@router.post('/agent/{batch_id}/analyze')
async def analyze(batch_id: str, request: Request):
    ctx = context(batch_id, request); data = await request.json()
    source = work_path(ctx, data['input']); output = work_path(ctx, data['output'], exists=False)
    info, times = await contact_sheet(ctx, source, output.with_suffix('.jpg'))
    batch_store.log(batch_id, 'Analyzing actual frames: ' + source.name, 'vision')
    prompt = 'Analyze these nine time-labelled frames for the editing workflow. Return JSON with subject, scene_summary, has_burned_subtitles, visible_text, visible_watermarks (locations), best_cover_time (one actual labelled time), framing_notes, usable_evidence and uncertain_claims. No invented product properties. Media metadata: ' + json.dumps(info)
    analysis = parse_json(await asyncio.to_thread(vision, [output.with_suffix('.jpg')], prompt))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({'metadata': info, 'sample_times': times, 'analysis': analysis}, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'file': str(output), 'metadata': info, 'sample_times': times, 'analysis': analysis}


@router.post('/agent/{batch_id}/tts')
async def tts(batch_id: str, request: Request):
    ctx = context(batch_id, request); data = await request.json()
    source = work_path(ctx, data['input']); output = work_path(ctx, data['output'], exists=False)
    brand = ctx['brand']
    if not brand.get('voice_profile'):
        raise HTTPException(400, 'Choose a saved Studio voice for this product.')
    batch_store.log(batch_id, 'Generating and saving the complete narration: ' + source.name, 'tts')
    form = {'text': source.read_text(encoding='utf-8-sig'), 'profile_id': brand['voice_profile'], 'stream': 'false'}
    if brand.get('tts_engine'):
        form['engine'] = brand['tts_engine']
    base = agent.child_env(batch_id, ctx['root'], ctx['token'])['VOXFORMA_AGENT_URL']
    async with httpx.AsyncClient(timeout=21600, trust_env=False) as client:
        async with client.stream('POST', base + '/generate', data=form) as response:
            response.raise_for_status()
            if 'audio/' not in response.headers.get('content-type', ''):
                raise HTTPException(502, 'The speech endpoint did not return a finished audio file.')
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open('wb') as dest:
                async for chunk in response.aiter_bytes():
                    dest.write(chunk)
    return {'file': str(output), 'saved': True}


@router.post('/agent/{batch_id}/audit')
async def audit(batch_id: str, request: Request):
    ctx = context(batch_id, request); data = await request.json()
    ctx['production_started'] = True
    if data['source_id'] not in {s['id'] for s in ctx['sources']}:
        raise HTTPException(400, 'This input does not belong to the current batch.')
    video, cover = work_path(ctx, data['video']), work_path(ctx, data['cover'])
    info = await probe(video)
    rate = info['frame_rate'].split('/')
    fps = float(rate[0]) / float(rate[1]) if len(rate) == 2 and float(rate[1]) else 0
    if (info['width'], info['height']) != (1080, 1920) or abs(fps - 30) > 0.01:
        raise HTTPException(400, 'Output must be 1080x1920/30fps.')
    with Image.open(cover) as image:
        if image.size != (1080, 1440):
            raise HTTPException(400, 'Cover must be 1080x1440.')
    await command(['-xerror', '-i', str(video), '-f', 'null', '-'], ctx['job'])
    records = ctx['root'] / '.voxforma/audits'; records.mkdir(exist_ok=True)
    sheet = records / (data['source_id'] + '.jpg')
    await contact_sheet(ctx, video, sheet)
    batch_store.log(batch_id, 'Inspecting finished video and cover: ' + video.name, 'audit')
    prompt = 'Review the ACTUAL finished video contact sheet and separate cover against the product/workflow. Check text readability, obvious overlays blocking the subject, framing, subtitle collisions, blank/unrelated scenes, visible residual watermarks and cover composition. Return JSON {"passed":true/false,"issues":[specific visible issues]}. Do not claim to have listened to audio. Product context: ' + json.dumps(ctx['brand'], ensure_ascii=False)
    result = parse_json(await asyncio.to_thread(vision, [sheet, cover], prompt))
    if not isinstance(result.get('passed'), bool) or not isinstance(result.get('issues'), list):
        raise HTTPException(502, 'Vision audit returned an invalid result.')
    result.update(video=str(video), cover=str(cover), video_bytes=video.stat().st_size, cover_bytes=cover.stat().st_size)
    (records / (data['source_id'] + '.json')).write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    return result


@router.post('/agent/{batch_id}/publish')
async def publish(batch_id: str, request: Request):
    ctx = context(batch_id, request); data = await request.json()
    ctx['production_started'] = True
    batch = batch_store.get_batch(batch_id)
    item = next((i for i in batch['items'] if i['source_id'] == data['source_id']), None)
    if item is None:
        raise HTTPException(400, 'This input does not belong to the current batch.')
    if item['status'] == 'done':
        return {'published': True, 'source_id': item['source_id'], 'existing': True}
    if ctx.get('product'):
        from production import product_store
        classified = product_store.get_asset(ctx['product']['id'], item['source_id'])
        if classified.get('classification_status') not in {'classified', 'unclassified'}:
            raise HTTPException(400, 'Classify this input before editing and delivery.')
    paths = {kind: work_path(ctx, data[kind]) for kind in ['video', 'cover', 'publication', 'cover_title']}
    check_file = ctx['root'] / '.voxforma/audits' / (data['source_id'] + '.json')
    if not check_file.is_file():
        raise HTTPException(400, 'Inspect the actual finished video and cover with the audit tool first.')
    check = json.loads(check_file.read_text(encoding='utf-8'))
    if not check.get('passed') or check['video'] != str(paths['video']) or check['cover'] != str(paths['cover']) or check['video_bytes'] != paths['video'].stat().st_size or check['cover_bytes'] != paths['cover'].stat().st_size:
        raise HTTPException(400, 'The current output has not passed visual inspection. Repair and audit it again.')
    if not all(paths[k].read_text(encoding='utf-8-sig').strip() for k in ['publication', 'cover_title']):
        raise HTTPException(400, 'Publication and cover titles must not be empty.')
    # Copy to immutable application-owned delivery paths before marking success.
    target = store.ROOT / 'deliveries' / batch_id / item['source_id']
    target.mkdir(parents=True, exist_ok=True)
    names = {'video': 'video.mp4', 'cover': 'cover.jpg', 'publication': 'publication.txt', 'cover_title': 'cover-title.md'}
    for kind, source in paths.items():
        await asyncio.to_thread(shutil.copyfile, source, target / names[kind])
    item.update(status='done', artifacts=names, error=None)
    with db_conn() as db:
        allowed = set(ctx['brand']['material_ids'])
        for used in data.get('used', []):
            if used not in allowed:
                raise HTTPException(400, 'A used material ID is not in this product library.')
            if batch['request']['options']['workflow'] != 'product':
                db.execute('INSERT OR IGNORE INTO production_asset_usage(batch_id,source_id,asset_id) VALUES (?,?,?)', (batch_id, item['source_id'], used))
        latest = json.loads(db.execute('SELECT items_json FROM production_batches WHERE id=?', (batch_id,)).fetchone()[0])
        current = next(i for i in latest if i['source_id'] == item['source_id'])
        current.update(status='done', artifacts=names, error=None)
        db.execute('UPDATE production_batches SET items_json=? WHERE id=?', (json.dumps(latest, ensure_ascii=False), batch_id))
    batch_store.log(batch_id, 'Published: ' + item['filename'], 'delivery')
    return {'published': True, 'source_id': item['source_id']}


@router.post('/agent/{batch_id}/fail')
async def fail(batch_id: str, request: Request):
    context(batch_id, request); data = await request.json()
    batch = batch_store.get_batch(batch_id)
    item = next((i for i in batch['items'] if i['source_id'] == data['source_id']), None)
    if item is None:
        raise HTTPException(400, 'Unknown input.')
    if item['status'] == 'done':
        raise HTTPException(409, 'A published input cannot be changed to failed.')
    item.update(status='failed', error=str(data['reason'])[:4000])
    batch_store.update_items(batch_id, batch['items'])
    batch_store.log(batch_id, item['filename'] + ': ' + item['error'], 'error')
    return {'recorded': True}


@router.post('/agent/{batch_id}/ingest')
async def ingest(batch_id: str, request: Request):
    ctx = context(batch_id, request); data = await request.json()
    batch = batch_store.get_batch(batch_id)
    item = next((i for i in batch['items'] if i['source_id'] == data['source_id']), None)
    if item is None or (batch['request']['options']['workflow'] != 'intake' and not ctx.get('product')):
        raise HTTPException(400, 'This source is not an intake task.')
    index = json.loads(work_path(ctx, data['index']).read_text(encoding='utf-8'))
    if not index.get('items'):
        raise HTTPException(400, 'No segmented/tagged material was produced.')
    ids = []
    for clip in index['items']:
        source = work_path(ctx, clip['file'])
        if source.suffix.lower() in {'.png', '.jpg', '.jpeg', '.webp'}:
            with Image.open(source) as image:
                image.verify()
                metadata = {'kind': 'image', 'width': image.width, 'height': image.height, 'duration': 0}
        else:
            metadata = await probe(source)
        digest = hashlib.sha256()
        with source.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
        asset_id = digest.hexdigest()
        metadata.update(bytes=source.stat().st_size)
        if not ctx.get('product'):
            metadata.update(scene=str(clip.get('scene', '')), description=str(clip.get('description', '')), tags=list(clip.get('trigger_keywords', [])))
        target = store.asset_path(asset_id); target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            await asyncio.to_thread(shutil.copyfile, source, target)
        with db_conn() as db:
            db.execute('INSERT INTO media_assets(id,sha256,filename,metadata_json,created_at) VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET metadata_json=excluded.metadata_json', (asset_id, asset_id, source.name, json.dumps(metadata, ensure_ascii=False), time.time()))
        ids.append(asset_id)
        if ctx.get('product'):
            from production import product_store
            pid = ctx['product']['id']
            parent = product_store.get_asset(pid, item['source_id'])
            category = clip.get('category_id', parent.get('category_id'))
            product_store.attach_asset(pid, asset_id, category_id=category, role='material')
            product_store.classify_asset(pid, asset_id, category, description=str(clip.get('description', '')), tags=list(clip.get('trigger_keywords', [])))
    if ctx.get('product'):
        ctx['brand']['material_ids'] = list(dict.fromkeys(ctx['brand']['material_ids'] + ids))
        (ctx['root'] / 'product.json').write_text(json.dumps(ctx['brand'], ensure_ascii=False, indent=2), encoding='utf-8')
        if batch['request']['options']['workflow'] == 'intake':
            batch_store.update_item(batch_id, item['source_id'], {'status': 'done', 'artifacts': {}, 'error': None, 'material_ids': ids})
        else:
            batch_store.update_item(batch_id, item['source_id'], {'material_ids': ids})
    else:
        brand = batch_store.get_brand(batch['request']['options']['brand_id'])
        from production.batch_models import Brand
        brand['material_ids'] = list(dict.fromkeys(brand['material_ids'] + ids))
        batch_store.save_brand(Brand.model_validate(brand), brand['id'])
        batch_store.update_item(batch_id, item['source_id'], {'status': 'done', 'artifacts': {}, 'error': None, 'material_ids': ids})
    batch_store.log(batch_id, 'Indexed ' + str(len(ids)) + ' material clips from ' + item['filename'], 'intake')
    return {'indexed': ids}
