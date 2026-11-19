"""Run one Qwen Image workflow in an owned, loopback-only ComfyUI process."""
from __future__ import annotations

import asyncio
import os
import secrets
import shutil
import socket
from pathlib import Path

import httpx

from core.contained_subprocess import spawn_owned
from services import avatar_gpu
from services.comfy_image_install import engine_dir

SIZES = {
    "1024x1024": (1024, 1024),
    "1024x576": (1024, 576),
    "576x1024": (576, 1024),
    "2048x2048": (2048, 2048),
    "2048x1152": (2048, 1152),
    "1152x2048": (1152, 2048),
    "reference": None,
}
OUTPUT_NODE = "900"


def workflow(prompt: str, size: str, reference: str | None) -> dict:
    def node(class_type: str, **inputs) -> dict:
        return {"class_type": class_type, "inputs": inputs}

    dimensions = SIZES[size]
    graph = {
        "451": node("UNETLoader", unet_name="qwen_image_2.1_bf16.safetensors", weight_dtype="default"),
        "453": node("CLIPLoader", clip_name="qwen3vl_8b_bf16.safetensors", type="qwen_image", device="default"),
        "454": node("VAELoader", vae_name="qwen_image_2.1_vae_bf16.safetensors"),
        "452": node("TextEncodeQwenImage21", clip=["453", 0], prompt=prompt,
                    negative_prompt="", resolution=2048 if dimensions and max(dimensions) == 2048 else 1024),
        "480": node("QwenImage21Cache", model=["451", 0], device="auto", dtype="default"),
        "458": node("KSampler", model=["480", 0], positive=["452", 0], negative=["452", 1],
                    latent_image=["456", 0], seed=secrets.randbits(53), steps=40, cfg=1.0,
                    sampler_name="euler", scheduler="simple", denoise=1.0),
        "457": node("VAEDecode", samples=["458", 0], vae=["454", 0]),
        OUTPUT_NODE: node("SaveImage", images=["457", 0], filename_prefix="result"),
    }
    if reference:
        graph["470"] = node("LoadImage", image=reference)
        graph["452"]["inputs"].update({"images.image_1": ["470", 0], "vae": ["454", 0]})
        graph["458"]["inputs"]["latent_image"] = ["452", 2]
    elif dimensions:
        graph["456"] = node("EmptyLatentImage", width=dimensions[0], height=dimensions[1], batch_size=1)
    else:
        raise ValueError("The reference size requires an input image.")
    return graph


async def _ready(client: httpx.AsyncClient, process) -> None:
    deadline = asyncio.get_running_loop().time() + 180
    while asyncio.get_running_loop().time() < deadline:
        if process.poll() is not None:
            raise RuntimeError("ComfyUI exited during startup. See the task's ComfyUI log.")
        try:
            response = await client.get("/object_info/TextEncodeQwenImage21", timeout=2)
            if response.is_success and response.json().get("TextEncodeQwenImage21"):
                return
        except httpx.RequestError:
            pass
        await asyncio.sleep(1)
    raise RuntimeError("ComfyUI did not start within 180 seconds.")


async def _result(client: httpx.AsyncClient, process, graph: dict) -> dict:
    submitted = await client.post("/prompt", json={"prompt": graph})
    if not submitted.is_success:
        detail = submitted.json()
        raise RuntimeError(f"ComfyUI rejected the workflow: {str(detail.get('node_errors') or detail.get('error'))[:800]}")
    prompt_id = submitted.json()["prompt_id"]
    deadline = asyncio.get_running_loop().time() + 3600
    while asyncio.get_running_loop().time() < deadline:
        if process.poll() is not None:
            raise RuntimeError("ComfyUI exited while generating. See the task's ComfyUI log.")
        response = await client.get(f"/history/{prompt_id}")
        response.raise_for_status()
        record = response.json().get(prompt_id)
        if record:
            outputs = record.get("outputs", {}).get(OUTPUT_NODE, {}).get("images", [])
            if outputs:
                return outputs[0]
            messages = record.get("status", {}).get("messages", [])
            for event, payload in messages:
                if event == "execution_error":
                    raise RuntimeError(str(payload.get("exception_message", "ComfyUI execution failed."))[:800])
            raise RuntimeError("ComfyUI finished without saving an image.")
        await asyncio.sleep(2)
    raise RuntimeError("Image generation exceeded one hour.")


async def _generate(directory: Path, graph: dict, set_stage) -> None:
    root = engine_dir()
    runtime = directory / "runtime"
    for folder in ("input", "output", "user"):
        (runtime / folder).mkdir(parents=True, exist_ok=True)
    reference = graph.get("470", {}).get("inputs", {}).get("image")
    if reference:
        shutil.move(directory / reference, runtime / "input" / reference)

    with socket.socket() as port_probe:
        port_probe.bind(("127.0.0.1", 0))
        port = str(port_probe.getsockname()[1])

    env = {**os.environ, "PYTHONNOUSERSITE": "1", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
    env.pop("PYTHONPATH", None)
    env.pop("VIRTUAL_ENV", None)
    process = None
    try:
        with (directory / "comfy.log").open("wb") as log:
            process = spawn_owned([
                str(root / ".venv/bin/python"), "-u", str(root / "ComfyUI/main.py"),
                "--listen", "127.0.0.1", "--port", port,
                "--base-directory", str(runtime), "--models-directory", str(root / "models"),
                "--disable-all-custom-nodes", "--disable-api-nodes", "--disable-metadata",
                "--enable-dynamic-vram", "--reserve-vram", "1",
            ], cwd=str(root / "ComfyUI"), env=env, stdout=log, stderr=log)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=30, trust_env=False) as client:
            await _ready(client, process)
            set_stage("generating")
            result = await _result(client, process, graph)
            output_dir = (runtime / "output").resolve()
            source = (output_dir / result["subfolder"] / result["filename"]).resolve()
            if not source.is_relative_to(output_dir) or not source.is_file() or source.suffix != ".png":
                raise RuntimeError("ComfyUI returned an invalid image result.")
            source.replace(directory / "result.png")
    finally:
        # Stop the child before surrendering the shared GPU lease.
        if process is not None:
            if process.poll() is None:
                process.kill()
            await asyncio.to_thread(process.wait, 15)
        shutil.rmtree(runtime, ignore_errors=True)


async def generate(job_id: str, directory: Path, graph: dict, set_stage) -> None:
    handle = await avatar_gpu.acquire(f"voicestudio:image:{job_id}")
    try:
        set_stage("starting")
        if handle is not None:
            client, lease, _ = handle
            await asyncio.to_thread(client.rpc, "clear_voice", token=lease["token"], timeout=150)
        await _generate(directory, graph, set_stage)
    finally:
        await avatar_gpu.release(handle)
