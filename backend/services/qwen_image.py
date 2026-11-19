"""Run one local Qwen Image task under the shared GPU lease."""
from __future__ import annotations

import asyncio
import json
import os
import secrets
from pathlib import Path

from core.contained_subprocess import spawn_owned
from services import avatar_gpu
from services.qwen_image_install import engine_dir, write_json

SIZES = {
    "1024x1024": (1024, 1024),
    "1024x576": (1024, 576),
    "576x1024": (576, 1024),
    "2048x2048": (2048, 2048),
    "2048x1152": (2048, 1152),
    "1152x2048": (1152, 2048),
    "reference": None,
}
WORKER = Path(__file__).with_name("qwen_image_worker.py").resolve()


async def _run_worker(directory: Path, payload: dict, set_stage) -> None:
    root = engine_dir()
    request = directory / "request.json"
    ready = directory / ".running"
    error = directory / "error.json"
    output = directory / "result.png"
    write_json(request, payload)

    env = {**os.environ, "PYTHONNOUSERSITE": "1", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
    env.pop("PYTHONPATH", None)
    env.pop("VIRTUAL_ENV", None)
    process = None
    started = False
    try:
        with (directory / "diffusers.log").open("wb") as log:
            process = spawn_owned([
                str(root / ".venv/bin/python"), "-u", str(WORKER), str(root / "models"), str(request),
            ], cwd=str(root), env=env, stdout=log, stderr=log)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 3600
        while process.poll() is None:
            if not started and ready.is_file():
                set_stage("generating")
                started = True
            if loop.time() >= deadline:
                raise RuntimeError("Image generation exceeded one hour.")
            await asyncio.sleep(0.5)
        if process.returncode != 0 or not output.is_file():
            if error.is_file():
                detail = json.loads(error.read_text(encoding="utf-8"))["message"]
                raise RuntimeError(detail)
            raise RuntimeError(f"Qwen image worker exited with code {process.returncode}. See diffusers.log.")
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
            await asyncio.to_thread(process.wait, 15)
        for path in (request, ready, error, directory / "result.partial.png"):
            path.unlink(missing_ok=True)


async def generate(job_id: str, directory: Path, *, prompt: str, size: str, references: list[str], mode: str, set_stage) -> None:
    dimensions = SIZES[size]
    if mode == "image-to-image":
        prompt = f"Create a new image using the input images as visual references. Description of the new image: {prompt}"
    elif mode == "edit" and len(references) > 1:
        prompt = f"Edit the first image using the other images as visual references. Editing instruction: {prompt}"
    payload = {"prompt": prompt, "references": references, "dimensions": dimensions, "seed": secrets.randbits(53)}
    handle = await avatar_gpu.acquire(f"voicestudio:image:{job_id}")
    try:
        set_stage("starting")
        if handle is not None:
            client, lease, _ = handle
            await asyncio.to_thread(client.rpc, "clear_voice", token=lease["token"], timeout=150)
        await _run_worker(directory, payload, set_stage)
    finally:
        await avatar_gpu.release(handle)
