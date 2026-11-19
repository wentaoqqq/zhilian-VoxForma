"""Click-to-install the isolated Diffusers runtime and official Qwen weights."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import platform
import shutil
import sys
import threading
import time
from pathlib import Path

import httpx

from core.config import DATA_DIR, get_engines_dir
from core.contained_subprocess import spawn_owned

MANIFEST = json.loads(Path(__file__).with_name("qwen_image_manifest.json").read_text(encoding="utf-8"))
MODEL_ID = "qwen-image-2.1-bf16"
MODEL_NAME = "Qwen Image 2.1 BF16"
MODEL_REPO = MANIFEST["repo"]
MODEL_REV = MANIFEST["revision"]
WEIGHTS = MANIFEST["files"]
DIFFUSERS_VERSION = "0.43.0"
RUNTIME_REV = f"diffusers-{DIFFUSERS_VERSION}-cu130-v1"

_log = logging.getLogger(__name__)
_lock = threading.Lock()
_thread: threading.Thread | None = None
_job: dict | None = None


def engine_dir() -> Path:
    return Path(get_engines_dir()).resolve() / "qwen-image" / "qwen-image-2.1-v1"


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
            (root / ".installed").read_text().strip() == f"{RUNTIME_REV}:{MODEL_REV}"
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


def file_matches(path: Path, size: int, checksum: str) -> bool:
    if not path.is_file() or path.stat().st_size != size:
        return False
    if len(checksum) == 40:
        # Regular files use a Git blob hash. The weight shards use LFS SHA-256.
        digest = hashlib.sha1(f"blob {size}\0".encode(), usedforsecurity=False)
    else:
        digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024**2), b""):
            digest.update(chunk)
    return digest.hexdigest() == checksum


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


def _runtime(root: Path) -> None:
    from services import sidecar_install

    marker = root / ".runtime-revision"
    python = root / ".venv/bin/python"
    if python.is_file() and marker.is_file() and marker.read_text().strip() == RUNTIME_REV:
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
        "torch==2.10.0+cu130", "torchvision==0.25.0+cu130",
        "--index-url", "https://download.pytorch.org/whl/cu130",
    ], env, 3600)
    _command(root, install + [
        f"diffusers=={DIFFUSERS_VERSION}", "transformers==5.19.0", "accelerate==1.14.0",
        "pillow>=13,<14", "jinja2",
    ], env, 3600)
    _command(root, [
        str(python), "-c",
        "import torch; from diffusers import QwenImage21Pipeline; "
        "from diffusers.hooks import apply_group_offloading; "
        "from transformers import Qwen3VLProcessor; assert torch.version.cuda == '13.0'",
    ], env, 90)
    marker.write_text(RUNTIME_REV)


def _existing_models() -> Path:
    configured = os.environ.get("VOXFORMA_QWEN_MODELS_DIR")
    if configured:
        return Path(configured).expanduser()
    cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")).expanduser()
    hf_home = Path(os.environ.get("HF_HOME", cache / "huggingface")).expanduser()
    hub_cache = Path(os.environ.get("HF_HUB_CACHE") or os.environ.get("HUGGINGFACE_HUB_CACHE")
                     or hf_home / "hub").expanduser()
    return hub_cache / "models--Qwen--Qwen-Image-2.1" / "snapshots" / MODEL_REV


def _download_metadata(url: str, target: Path, size: int, checksum: str) -> None:
    staging = target.with_suffix(".download")
    with httpx.stream("GET", url, timeout=60, follow_redirects=True) as response:
        response.raise_for_status()
        with staging.open("wb") as output:
            received = 0
            for chunk in response.iter_bytes(1024**2):
                received += len(chunk)
                if received > size:
                    raise ValueError("Model metadata exceeded its pinned size.")
                output.write(chunk)
    if not file_matches(staging, size, checksum):
        raise ValueError("Model metadata did not match its pinned hash.")
    staging.replace(target)


def _install_model(root: Path, name: str, size: int, checksum: str) -> None:
    from services import endpoint_race, segmented_download

    target = root / "models" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    if file_matches(target, size, checksum):
        return
    existing = _existing_models() / name
    if target.resolve() != existing.resolve() and file_matches(existing, size, checksum):
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
        # This pack is public. No account credentials are needed for downloads.
        url = f"{endpoint.rstrip('/')}/{MODEL_REPO}/resolve/{MODEL_REV}/{name}"
        received = 0
        updated = 0.0

        def on_bytes(count: int) -> None:
            nonlocal received, updated
            received += count
            now = time.monotonic()
            if now - updated >= 0.5:
                _update(received_bytes=min(size, received))
                updated = now

        try:
            if len(checksum) == 40:
                _download_metadata(url, target, size, checksum)
            else:
                asyncio.run(segmented_download.segmented_download(
                    url, str(target), expected_size=size, expected_etag=checksum,
                    num_connections=8, on_bytes=on_bytes, timeout=90,
                ))
            return
        except Exception as exc:
            if isinstance(exc, ValueError) and "sha256 mismatch" in str(exc):
                part = str(target) + ".part"
                Path(part).unlink(missing_ok=True)
                Path(segmented_download._manifest_path(part)).unlink(missing_ok=True)
            _log.warning("Image model download failed for %s (%s)", name, type(exc).__name__)
    raise RuntimeError(f"Download failed for {name}. Retry to resume.")


def _run_install() -> None:
    try:
        root = engine_dir()
        root.mkdir(parents=True, exist_ok=True)
        import fcntl

        with (root / "install.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _runtime(root)
            for name, (size, checksum) in WEIGHTS.items():
                _update(step="weights", filename=name, received_bytes=0, file_bytes=size)
                _install_model(root, name, size, checksum)
            (root / ".installed").write_text(f"{RUNTIME_REV}:{MODEL_REV}")
        _update(state="done", step=None, filename=None)
    except Exception as exc:
        _update(state="failed", error=str(exc))


def start_install() -> dict:
    global _job, _thread
    if not supported():
        raise ValueError("Image generation requires x86-64 Linux/WSL with an NVIDIA driver.")
    with _lock:
        if installed():
            return {"state": "done"}
        if _thread and _thread.is_alive():
            return {"state": "running"}
        _job = {"state": "running", "step": "runtime", "filename": None, "error": None}
        _thread = threading.Thread(target=_run_install, daemon=True, name="qwen-image-install")
        _thread.start()
        return {"state": "running"}
