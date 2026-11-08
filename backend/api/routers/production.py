"""Product workflow: library, queue, previews and four-file deliveries."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
from pathlib import Path
import re
import time
import uuid
import zipfile
from PIL import Image

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from api.dependencies import require_consumer
from core import job_store
from core.db import db_conn
from core.job_queue import Job, JobQueue
from production import store
from production.models import ProductPlan
from production.plugins import get_plugin, list_plugins
from production.render import probe, render_product
from services.srt_parser import parse_srt

queue = JobQueue("production-cpu", concurrency=1)


@asynccontextmanager
async def lifespan(_app):
    yield
    await queue.stop()
    from production import agent
    await agent.queue.stop()


router = APIRouter(prefix="/production", tags=["production"], dependencies=[Depends(require_consumer)], lifespan=lifespan)


def asset_or_404(asset_id: str) -> dict:
    try:
        data = store.get_asset(asset_id)
        if not store.asset_path(asset_id).is_file():
            raise ValueError("Media file is missing. Upload it again.")
        return data
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


def run_or_404(run_id: str) -> dict:
    try:
        return store.get_run(run_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/assets")
def list_assets():
    with db_conn() as conn:
        rows = conn.execute("SELECT * FROM media_assets ORDER BY created_at DESC").fetchall()
    return [{"id": r["id"], "filename": r["filename"], "created_at": r["created_at"], **json.loads(r["metadata_json"])} for r in rows]


@router.post("/assets")
async def upload_asset(request: Request, filename: str):
    suffix = Path(filename).suffix.lower()
    if suffix not in {'.mp4', '.mov', '.mkv', '.webm', '.avi', '.m4v', '.wav', '.mp3', '.m4a', '.flac', '.ogg', '.png', '.jpg', '.jpeg', '.webp', '.txt'}:
        raise HTTPException(400, 'Upload a video, audio file, image or UTF-8 text script.')
    incoming = store.ROOT / "incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    temp = incoming / (uuid.uuid4().hex + Path(filename).suffix.lower())
    digest, size = hashlib.sha256(), 0
    try:
        with temp.open("wb") as output:
            async for chunk in request.stream():
                size += len(chunk)
                if size > 2 * 1024**3:
                    raise HTTPException(413, "Video must be at most 2 GiB.")
                digest.update(chunk)
                await asyncio.to_thread(output.write, chunk)
        if not size:
            raise HTTPException(400, "The uploaded file is empty.")
        if suffix == '.txt':
            text = temp.read_text(encoding='utf-8-sig')
            if not text.strip():
                raise ValueError('The script is empty.')
            metadata = {'kind': 'text', 'duration': 0, 'width': 0, 'height': 0, 'has_audio': False}
        elif suffix in {'.png', '.jpg', '.jpeg', '.webp'}:
            with Image.open(temp) as image:
                metadata = {'kind': 'image', 'duration': 0, 'width': image.width, 'height': image.height, 'has_audio': False}
                image.verify()
        else:
            metadata = await probe(temp, video_required=False)
        metadata["bytes"] = size
        asset_id = digest.hexdigest()
        dest = store.asset_path(asset_id)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.is_file():
            temp.replace(dest)
        with db_conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO media_assets(id,sha256,filename,metadata_json,created_at) VALUES (?,?,?,?,?)",
                (asset_id, asset_id, filename.replace("\\", "/").rsplit("/", 1)[-1][:255], json.dumps(metadata), time.time()),
            )
        return store.get_asset(asset_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        temp.unlink(missing_ok=True)


@router.get("/assets/{asset_id}/preview")
def asset_preview(asset_id: str):
    data = asset_or_404(asset_id)
    # FileResponse supplies range requests for video seeking, including over LAN.
    suffix = Path(data["filename"]).suffix.lower()
    import mimetypes
    media_type = mimetypes.guess_type(data['filename'])[0] or 'application/octet-stream'
    return FileResponse(store.asset_path(asset_id), media_type=media_type)


class SubtitleText(BaseModel):
    text: str = Field(max_length=2_000_000)


@router.post("/subtitles/parse")
def parse_subtitles(req: SubtitleText):
    parsed = parse_srt(req.text, preserve_overlaps=True)
    if not parsed.segments or parsed.skipped_cues or parsed.dropped_overlaps:
        raise HTTPException(400, "The subtitle file has empty or malformed cues. Correct it before submitting.")
    return [{"start": s["start"], "end": s["end"], "text": s["text"]} for s in parsed.segments]


def validate_plan(plan: ProductPlan) -> None:
    try:
        get_plugin(plan.plugin_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    source = asset_or_404(plan.source_id)
    asset_or_404(plan.outro_id)
    if plan.cover_time >= source["duration"]:
        raise HTTPException(400, "Cover frame is outside the source video.")
    if any(c.end > source["duration"] + 0.04 for c in plan.captions):
        raise HTTPException(400, "A subtitle extends past the source video.")


@router.get("/plugins")
def plugins():
    return [{key: p[key] for key in ("id", "version", "name_key")} for p in list_plugins()]


@router.get("/ai/status")
def ai_status():
    # Read configuration only. Merely opening the workspace never calls a model.
    from production import agent
    return agent.status()


@router.post("/ai/plan")
async def ai_plan():
    raise HTTPException(410, 'The text-only planner was replaced by the full production agent. Use /production/batches.')


async def execute(job: Job, plan: ProductPlan):
    job_store.mark_running(job.id)
    try:
        result = await render_product(
            plan, store.asset_path(plan.source_id), store.asset_path(plan.outro_id),
            store.ROOT / "runs" / job.id, job,
            lambda name, status: store.set_step(job.id, name, status),
        )
        if job.is_cancelled:
            raise asyncio.CancelledError()
        with db_conn() as conn:
            conn.execute("UPDATE production_runs SET artifacts_json=? WHERE id=?", (json.dumps(result), job.id))
            conn.execute("UPDATE jobs SET status='done', updated_at=?, finished_at=? WHERE id=?", (time.time(), time.time(), job.id))
        return result
    except asyncio.CancelledError:
        store.finish_active_steps(job.id, "cancelled")
        job_store.mark_cancelled(job.id)
        if job.is_cancelled:
            return None
        raise
    except Exception as exc:
        store.finish_active_steps(job.id, "failed")
        job_store.mark_failed(job.id, str(exc))
        raise


@router.post("/runs")
async def create_run(plan: ProductPlan):
    validate_plan(plan)
    queue.purge_finished()
    run_id = uuid.uuid4().hex
    now = time.time()
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO jobs(id,type,status,created_at,updated_at,meta_json) VALUES (?,?,'pending',?,?,?)",
            (run_id, "production", now, now, json.dumps({"title": plan.title, "plugin": get_plugin(plan.plugin_id)})),
        )
        conn.execute("INSERT INTO production_runs(id,plan_json) VALUES (?,?)", (run_id, plan.model_dump_json()))
    await queue.start()
    await queue.submit(plan.title, lambda job: execute(job, plan), job_id=run_id)
    return store.get_run(run_id)


@router.get("/runs")
def list_runs():
    with db_conn() as conn:
        rows = conn.execute("SELECT r.id FROM production_runs r JOIN jobs j ON j.id=r.id ORDER BY j.created_at DESC LIMIT 100").fetchall()
    return [store.get_run(r["id"]) for r in rows]


@router.get("/runs/{run_id}")
def get_run(run_id: str):
    return run_or_404(run_id)


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str):
    run_or_404(run_id)
    if await queue.cancel(run_id):
        if queue.get(run_id).state.value == "cancelled":
            job_store.mark_cancelled(run_id)
    return run_or_404(run_id)


@router.post("/runs/{run_id}/retry")
async def retry_run(run_id: str):
    previous = run_or_404(run_id)
    if previous["status"] in {"pending", "running"}:
        raise HTTPException(409, "This production task is still active.")
    return await create_run(ProductPlan.model_validate(previous["plan"]))


def download_stem(plan: dict) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", plan["title"]).strip(" .")[:60] or "product"


@router.get("/runs/{run_id}/files/{kind}")
def artifact(run_id: str, kind: str, download: bool = False):
    data = run_or_404(run_id)
    name = data["artifacts"].get(kind)
    if data["status"] != "done" or kind not in {"video", "cover", "publication", "cover_title"} or not name:
        raise HTTPException(404, "The requested output is not ready.")
    path = store.ROOT / "runs" / run_id / name
    if not path.is_file():
        raise HTTPException(404, "The output file is missing. Retry this task.")
    return FileResponse(path, filename=download_stem(data["plan"]) + "_" + name if download else None)


@router.get("/runs/{run_id}/download")
def download_bundle(run_id: str):
    data = run_or_404(run_id)
    if data["status"] != "done" or len(data["artifacts"]) != 4:
        raise HTTPException(409, "The four-file delivery is not ready.")
    work = store.ROOT / "runs" / run_id
    stem = download_stem(data["plan"])
    # ZIP_STORED avoids recompressing video; creates a small-memory stream of files.
    bundle = work / (uuid.uuid4().hex + ".zip")
    suffixes = {"video": "_\u6210\u7247.mp4", "cover": "_\u5c01\u9762.jpg", "publication": "_\u53d1\u5e03\u6807\u9898.txt", "cover_title": "_\u5c01\u9762\u56fe\u6807\u9898.md"}
    try:
        with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_STORED) as output:
            for kind, name in data["artifacts"].items():
                output.write(work / name, stem + suffixes[kind])
    except OSError as exc:
        bundle.unlink(missing_ok=True)
        raise HTTPException(409, "A delivery file is missing. Retry this task.") from exc
    from starlette.background import BackgroundTask
    return FileResponse(bundle, media_type="application/zip", filename=stem + ".zip", background=BackgroundTask(bundle.unlink, missing_ok=True))


from api.routers.production_batches import router as batch_router
router.include_router(batch_router)
