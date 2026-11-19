"""Click-to-install managed ComfyUI and the Qwen Image 2.1 model pack."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import platform
import shutil
import sys
import tarfile
import threading
import time
from pathlib import Path

import httpx

from core.config import DATA_DIR, get_engines_dir
from core.contained_subprocess import spawn_owned

COMFY_REV = "73c9bad4d21e7addbe1d13bc92eee0f1431b017d"
SOURCE_HASH = "a0804bec55fe4e82e88c048a671084e01eb2f71971bed6d884319e17b12bcf97"
MODEL_ID = "qwen-image-2.1-bf16"
MODEL_NAME = "Qwen Image 2.1 BF16"
MODEL_REPO = "Comfy-Org/Qwen-Image-2.1"
MODEL_REV = "aacf31a6d48e707f352ecba714cee0680da3479b"
WEIGHTS = {
    "diffusion_models/qwen_image_2.1_bf16.safetensors": (
        14230280616, "89f4158d066cc33906a199fca85634f766892dd78f49b6698dabf187ac86c4bc",
    ),
    "text_encoders/qwen3vl_8b_bf16.safetensors": (
        17534334616, "68bdc82bc1b66851162ae656225e7e2068166b603db19bd5d5a3b90eb12669a9",
    ),
    "vae/qwen_image_2.1_vae_bf16.safetensors": (
        675509688, "bb21f7473051e1ac368515dd3f2e15cd44d7a11748ee8823e1ddca3e4876b7c9",
    ),
}

_log = logging.getLogger(__name__)
_lock = threading.Lock()
_thread: threading.Thread | None = None
_job: dict | None = None


def engine_dir() -> Path:
    return Path(get_engines_dir()).resolve() / "comfy-image" / "qwen-image-2.1-v1"


def state_dir() -> Path:
    return Path(DATA_DIR).resolve() / "image-generation"


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def supported() -> bool:
    return (
        sys.platform == "linux"
        and platform.machine().lower() in {"x86_64", "amd64"}
        and bool(shutil.which("nvidia-smi") or Path("/usr/lib/wsl/lib/nvidia-smi").is_file())
    )


def installed() -> bool:
    root = engine_dir()
    try:
        return (
            (root / ".installed").read_text().strip() == f"{COMFY_REV}:{MODEL_REV}"
            and (root / "ComfyUI/main.py").is_file()
            and (root / ".venv/bin/python").is_file()
            and all((root / "models" / name).stat().st_size == size for name, (size, _) in WEIGHTS.items())
        )
    except OSError:
        return False


def status(*, with_job: bool = False) -> dict:
    result = {
        "supported": supported(),
        "installed": installed(),
        "models": [{"id": MODEL_ID, "label": MODEL_NAME}],
        "weight_bytes": sum(size for size, _ in WEIGHTS.values()),
    }
    if with_job:
        with _lock:
            result["job"] = dict(_job) if _job else None
    return result


def _update(**fields) -> None:
    with _lock:
        assert _job is not None
        _job.update(fields)


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(8 * 1024**2), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _command(root: Path, args: list[str], env: dict, timeout: int) -> None:
    log_path = root / "install.log"
    with log_path.open("ab") as log:
        process = spawn_owned(args, cwd=str(root), env=env, stdout=log, stderr=log)
        try:
            if process.wait(timeout=timeout):
                raise RuntimeError(f"{Path(args[0]).name} exited with code {process.returncode}. See {log_path}.")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=15)


def _install_source(root: Path) -> None:
    destination = root / "ComfyUI"
    if (destination / "main.py").is_file() and (destination / ".source-revision").is_file():
        if (destination / ".source-revision").read_text().strip() == COMFY_REV:
            return
        raise RuntimeError("Unexpected ComfyUI source revision in the managed install.")
    archive = root / "source.tar.gz"
    if not archive.is_file() or _sha256(archive) != SOURCE_HASH:
        url = f"https://codeload.github.com/Comfy-Org/ComfyUI/tar.gz/{COMFY_REV}"
        with httpx.stream("GET", url, follow_redirects=True, timeout=60) as response:
            response.raise_for_status()
            with archive.open("wb") as output:
                for chunk in response.iter_bytes(1024**2):
                    output.write(chunk)
        if _sha256(archive) != SOURCE_HASH:
            raise RuntimeError("ComfyUI source checksum did not match.")
    with tarfile.open(archive) as download:
        download.extractall(root, filter="data")
    extracted = root / f"ComfyUI-{COMFY_REV}"
    (extracted / ".source-revision").write_text(COMFY_REV)
    extracted.rename(destination)
    archive.unlink()


def _runtime(root: Path) -> None:
    from services import sidecar_install

    marker = root / ".runtime-revision"
    python = root / ".venv/bin/python"
    if python.is_file() and marker.is_file() and marker.read_text().strip() == COMFY_REV:
        return
    uv = sidecar_install._locate_uv()
    if not uv:
        raise RuntimeError("uv was not found. Install the VoiceStudio runtime first.")
    env = sidecar_install.uv_subprocess_env(root.parent)
    env.pop("VIRTUAL_ENV", None)
    env.pop("PYTHONPATH", None)
    env["PYTHONNOUSERSITE"] = "1"
    if not python.is_file():
        _command(root, [uv, "venv", "--python", "3.12", str(root / ".venv")], env, 600)
    install = [uv, "pip", "install", "--python", str(python)]
    _command(root, install + [
        "torch==2.10.0", "torchvision==0.25.0", "torchaudio==2.10.0",
        "--index-url", "https://download.pytorch.org/whl/cu130",
    ], env, 3600)
    _command(root, install + ["-r", str(root / "ComfyUI/requirements.txt"), "transformers==5.19.0"], env, 3600)
    _command(root, [str(python), "-c", "import torch; assert torch.version.cuda == '13.0'"], env, 90)
    _command(root, [str(python), str(root / "ComfyUI/main.py"), "--help"], env, 90)
    marker.write_text(COMFY_REV)


def _existing_models() -> Path:
    configured = os.environ.get("VOXFORMA_COMFY_MODELS_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / "minimax-h3/ComfyUI/models"


def _install_model(root: Path, name: str, size: int, checksum: str) -> None:
    from services import endpoint_race, segmented_download

    target = root / "models" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and target.stat().st_size == size and _sha256(target) == checksum:
        return
    existing = _existing_models() / name
    if existing.is_file() and target.resolve() != existing.resolve():
        if existing.stat().st_size == size and _sha256(existing) == checksum:
            staging = target.with_suffix(".reuse")
            staging.unlink(missing_ok=True)
            try:
                os.link(existing.resolve(), staging)
            except OSError:
                shutil.copyfile(existing, staging)
            staging.replace(target)
            return

    endpoints = dict.fromkeys(
        (endpoint_race.effective_endpoint() or "https://huggingface.co", "https://huggingface.co")
    )
    for endpoint in endpoints:
        # Only public weights are needed. Do not send account credentials to mirrors.
        url = f"{endpoint.rstrip('/')}/{MODEL_REPO}/resolve/{MODEL_REV}/{name}"
        progress = {"downloaded": 0, "updated": 0.0}

        def on_bytes(received: int) -> None:
            progress["downloaded"] += received
            if time.monotonic() - progress["updated"] >= 0.5:
                _update(received_bytes=min(size, progress["downloaded"]))
                progress["updated"] = time.monotonic()

        try:
            asyncio.run(segmented_download.segmented_download(
                url, str(target), expected_size=size, expected_etag=checksum,
                num_connections=8, on_bytes=on_bytes, timeout=90,
            ))
            return
        except Exception as exc:
            if isinstance(exc, ValueError) and "sha256 mismatch" in str(exc):
                target.with_name(target.name + ".part").unlink(missing_ok=True)
            _log.warning("Image model download failed for %s (%s)", name, type(exc).__name__)
    raise RuntimeError(f"Download failed for {name}. Retry to resume.")


def _run_install() -> None:
    try:
        root = engine_dir()
        root.mkdir(parents=True, exist_ok=True)
        import fcntl

        with (root / "install.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _update(step="source")
            _install_source(root)
            _update(step="runtime")
            _runtime(root)
            for name, (size, checksum) in WEIGHTS.items():
                _update(step="weights", filename=Path(name).name, received_bytes=0, file_bytes=size)
                _install_model(root, name, size, checksum)
            (root / ".installed").write_text(f"{COMFY_REV}:{MODEL_REV}")
        _update(state="done", step=None, filename=None)
    except Exception as exc:
        _update(state="failed", error=str(exc))


def start_install() -> dict:
    global _job, _thread
    if not supported():
        raise ValueError("ComfyUI requires x86-64 Linux/WSL with an NVIDIA driver.")
    with _lock:
        if installed():
            return {"state": "done"}
        if _thread and _thread.is_alive():
            return {"state": "running"}
        _job = {"state": "running", "step": "source", "filename": None, "error": None}
        _thread = threading.Thread(target=_run_install, daemon=True, name="comfy-image-install")
        _thread.start()
        return {"state": "running"}
