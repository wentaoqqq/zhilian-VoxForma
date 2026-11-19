"""Image generation queue, PNG delivery and administrator-only installation."""
from __future__ import annotations

import asyncio
import io
import json
import time
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from PIL import Image, UnidentifiedImageError

from api.dependencies import require_admin, require_consumer
from core.job_queue import Job, JobQueue
from services import comfy_image, comfy_image_install as install

router = APIRouter(prefix="/image-generation", tags=["image-generation"])
queue = JobQueue(name="image-generation")
MAX_REFERENCE = 20 * 1024**2
MAX_QUEUED = 16
FORMATS = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}


def _job_dir(id: str) -> Path:
    return install.state_dir() / "jobs" / id


@router.get("/models", dependencies=[Depends(require_consumer)])
def available_models() -> dict:
    return install.status()


@router.get("/install", dependencies=[Depends(require_admin)])
def installation_status() -> dict:
    return install.status(with_job=True)


@router.post("/install", status_code=202, dependencies=[Depends(require_admin)])
def start_install() -> dict:
    try:
        return install.start_install()
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


def _reference_name(content: bytes) -> str:
    try:
        with Image.open(io.BytesIO(content), formats=list(FORMATS)) as image:
            if image.width * image.height > 24_000_000:
                raise HTTPException(422, "The reference image exceeds 24 megapixels.")
            name = f"reference.{FORMATS[image.format]}"
            image.verify()
            return name
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError, ValueError) as exc:
        raise HTTPException(422, "Upload a valid PNG, JPEG or WebP reference image.") from exc


@router.post("/jobs", status_code=202, dependencies=[Depends(require_consumer)])
async def create_job(
    prompt: str = Form(min_length=1, max_length=8000),
    model: str = Form(),
    size: str = Form(),
    reference: UploadFile | None = File(default=None),
) -> dict:
    prompt = prompt.strip()
    if not prompt or model != install.MODEL_ID or size not in comfy_image.SIZES:
        raise HTTPException(422, "Choose a valid model and size and enter a prompt.")
    if (size == "reference") != (reference is not None):
        raise HTTPException(422, "Choose the reference size when uploading an image.")
    if not install.installed():
        raise HTTPException(409, "Install the image generation module in Settings first.")
    if len(queue.list_jobs()) >= MAX_QUEUED:
        raise HTTPException(429, "The image queue is full. Wait for a task to finish.")
    content = None
    reference_name = None
    if reference is not None:
        try:
            content = await reference.read(MAX_REFERENCE + 1)
        finally:
            await reference.close()
        if len(content) > MAX_REFERENCE:
            raise HTTPException(413, "The reference image exceeds 20 MiB.")
        reference_name = await asyncio.to_thread(_reference_name, content)
    if len(queue.list_jobs()) >= MAX_QUEUED:
        raise HTTPException(429, "The image queue is full. Wait for a task to finish.")

    identifier = uuid4().hex
    directory = _job_dir(identifier)
    record = {
        "id": identifier, "prompt": prompt, "model": model, "size": size,
        "created_at": time.time(), "state": "queued", "error": None,
    }
    record_path = directory / "job.json"
    directory.mkdir(parents=True)
    if reference_name is not None:
        (directory / reference_name).write_bytes(content)
    install.write_json(record_path, record)
    graph = comfy_image.workflow(prompt, size, reference_name)

    async def run(_: Job) -> None:
        def stage(state: str, error: str | None = None) -> None:
            install.write_json(record_path, {**record, "state": state, "error": error})

        try:
            stage("waiting")
            await comfy_image.generate(identifier, directory, graph, stage)
        except asyncio.CancelledError:
            stage("interrupted")
            raise
        except Exception as exc:
            stage("failed", str(exc)[:1000])
            raise
        else:
            stage("done")
        finally:
            if reference_name is not None:
                (directory / reference_name).unlink(missing_ok=True)

    queue.purge_finished()
    await queue.start()
    await queue.submit("Generate image", run, job_id=identifier)
    return record


def _list_jobs(active: set[str]) -> dict:
    paths = sorted((install.state_dir() / "jobs").glob("*/job.json"),
                   key=lambda path: path.parent.stat().st_mtime, reverse=True)
    results = []
    # Always include active tasks even if enough completed jobs are newer.
    for path in paths:
        if len(results) >= 30 and path.parent.name not in active:
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if record["state"] in {"queued", "waiting", "starting", "generating"} and record["id"] not in active:
            # The worker may have submitted the job after the active snapshot.
            if queue.get(record["id"]) is None:
                record["state"] = "interrupted"
        results.append(record)
    return {"jobs": sorted(results, key=lambda record: record["created_at"], reverse=True)}


@router.get("/jobs", dependencies=[Depends(require_consumer)])
async def list_jobs() -> dict:
    active_ids = {job.id for job in queue.list_jobs()}
    return await asyncio.to_thread(_list_jobs, active_ids)


@router.get("/jobs/{id}/image", dependencies=[Depends(require_consumer)])
def image_result(id: UUID) -> FileResponse:
    path = _job_dir(id.hex) / "result.png"
    if not path.is_file():
        raise HTTPException(404, "Image result not found.")
    return FileResponse(path, media_type="image/png")
