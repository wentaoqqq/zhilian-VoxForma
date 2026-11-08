import asyncio
import json
from pathlib import Path
import shutil
import uuid
import zipfile

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from api.dependencies import require_admin
from core import job_store
from core.db import db_conn
from production import agent, agent_runtime, batch_store, store, workspace
from production.agent_gateway import router as gateway
from production.batch_models import BatchRequest, Brand

router = APIRouter()
router.include_router(gateway)


@router.get('/brands')
def brands():
    return batch_store.list_brands()


def validate_brand(brand):
    try:
        for ids, kinds in [(brand.outro_ids, {'video'}), (brand.material_ids, {'video', 'image'}), (brand.sticker_ids, {'image'})]:
            for asset_id in ids:
                if store.get_asset(asset_id).get('kind', 'video') not in kinds:
                    raise ValueError('A brand resource has the wrong media type.')
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post('/brands')
def create_brand(brand: Brand):
    validate_brand(brand)
    return batch_store.save_brand(brand)


@router.put('/brands/{brand_id}')
def update_brand(brand_id: str, brand: Brand):
    try:
        batch_store.get_brand(brand_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    validate_brand(brand)
    return batch_store.save_brand(brand, brand_id)


@router.delete('/brands/{brand_id}')
def delete_brand(brand_id: str):
    with db_conn() as db:
        db.execute('DELETE FROM production_brands WHERE id=?', (brand_id,))
    return {'deleted': brand_id}


@router.get('/agent/status')
def agent_status():
    return agent.status()


@router.post('/runtime/install', dependencies=[Depends(require_admin)])
async def install_runtime():
    async def run(job):
        job_store.mark_running(job.id)
        try:
            result = await asyncio.to_thread(agent_runtime.install)
            job_store.mark_done(job.id)
            return result
        except Exception as exc:
            job_store.mark_failed(job.id, str(exc))
            raise
    job_id = uuid.uuid4().hex
    job_store.create(job_id, type='production-runtime')
    await agent.queue.start()
    await agent.queue.submit('Install production agent', run, job_id=job_id)
    return {'job_id': job_id}


@router.post('/packs/{pack_id}', dependencies=[Depends(require_admin)])
async def import_pack(pack_id: str, request: Request):
    import re
    if not re.fullmatch(r'[a-z0-9-]{1,60}', pack_id):
        raise HTTPException(400, 'Invalid workflow pack ID.')
    staging = store.ROOT / 'incoming' / uuid.uuid4().hex
    staging.mkdir(parents=True, exist_ok=True)
    archive = staging / 'pack.zip'
    size = 0
    try:
        with archive.open('wb') as output:
            async for chunk in request.stream():
                size += len(chunk)
                if size > 512 * 1024**2:
                    raise HTTPException(413, 'Workflow pack must be at most 512 MiB.')
                await asyncio.to_thread(output.write, chunk)
        content = staging / 'content'
        with zipfile.ZipFile(archive) as package:
            if sum(i.file_size for i in package.infolist()) > 1024**3:
                raise HTTPException(413, 'Unpacked workflow exceeds 1 GiB.')
            for item in package.infolist():
                p = Path(item.filename.replace('\\', '/'))
                if p.is_absolute() or '..' in p.parts:
                    raise HTTPException(400, 'Workflow pack has an invalid path.')
            package.extractall(content)
        if not (content / '4_engine/tools/build-html.mjs').is_file():
            raise HTTPException(400, 'The original workflow engine is missing from this pack.')
        target = store.ROOT / 'packs' / pack_id
        if target.exists():
            raise HTTPException(409, 'Use a new pack ID for a new version; running batches retain their original pack.')
        target.parent.mkdir(parents=True, exist_ok=True)
        content.replace(target)
        return {'installed': pack_id}
    finally:
        shutil.rmtree(staging, ignore_errors=True)


@router.post('/batches')
async def create_batch(request: BatchRequest):
    try:
        return await agent.submit(request)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get('/batches')
def batches():
    with db_conn() as db:
        ids = db.execute('SELECT b.id FROM production_batches b JOIN jobs j ON j.id=b.id ORDER BY j.created_at DESC LIMIT 100').fetchall()
    return [batch_store.get_batch(row[0]) for row in ids]


@router.get('/batches/{batch_id}')
def get_batch(batch_id: str):
    try:
        return batch_store.get_batch(batch_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post('/batches/{batch_id}/cancel')
async def cancel_batch(batch_id: str):
    get_batch(batch_id)
    if await agent.queue.cancel(batch_id):
        job = agent.queue.get(batch_id)
        if job.state.value == 'cancelled':
            job_store.mark_cancelled(batch_id)
    return get_batch(batch_id)


@router.post('/batches/{batch_id}/retry')
async def retry_batch(batch_id: str):
    previous = get_batch(batch_id)
    if previous['status'] in ['pending', 'running']:
        raise HTTPException(409, 'This batch is still running.')
    failed = [i['source_id'] for i in previous['items'] if i['status'] != 'done']
    if not failed:
        raise HTTPException(400, 'Every input in this batch already succeeded.')
    data = {**previous['request']['options'], 'source_ids': failed, 'retry_of': batch_id, 'request_id': None}
    try:
        return await agent.submit(BatchRequest.model_validate(data), brand_snapshot=previous['request']['brand'])
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def delivery(batch_id, source_id):
    batch = get_batch(batch_id)
    item = next((i for i in batch['items'] if i['source_id'] == source_id and i['status'] == 'done'), None)
    if not item or not item['artifacts']:
        raise HTTPException(404, 'This input has not produced a completed delivery.')
    return item


@router.get('/batches/{batch_id}/items/{source_id}/files/{kind}')
def artifact(batch_id: str, source_id: str, kind: str):
    item = delivery(batch_id, source_id)
    if kind not in ['video', 'cover', 'publication', 'cover_title']:
        raise HTTPException(404, 'Unknown delivery artifact.')
    path = store.ROOT / 'deliveries' / batch_id / source_id / item['artifacts'][kind]
    return FileResponse(path)


@router.get('/batches/{batch_id}/download')
def download(batch_id: str):
    batch = get_batch(batch_id)
    completed = [i for i in batch['items'] if i['status'] == 'done' and i['artifacts']]
    if not completed:
        raise HTTPException(409, 'No completed videos are available yet.')
    root = store.ROOT / 'deliveries' / batch_id
    archive = root / (uuid.uuid4().hex + '.zip')
    suffixes = {'video': '_\u6210\u7247.mp4', 'cover': '_\u5c01\u9762.jpg', 'publication': '_\u53d1\u5e03\u6807\u9898.txt', 'cover_title': '_\u5c01\u9762\u56fe\u6807\u9898.md'}
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_STORED) as output:
        for item in completed:
            name = workspace.safe_name(Path(item['filename']).stem) + '-' + item['source_id'][:6]
            for kind, filename in item['artifacts'].items():
                output.write(root / item['source_id'] / filename, name + '/' + name + suffixes[kind])
    return FileResponse(archive, media_type='application/zip', filename='production-' + batch_id[:8] + '.zip', background=BackgroundTask(archive.unlink, missing_ok=True))
