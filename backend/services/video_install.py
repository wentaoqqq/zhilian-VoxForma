"""Explicit, resumable installation of the bundled, modified TBDub Student.

No network/GPU work at import or status time. A candidate is activated only
after source, isolated Python, exact model hashes and runtime probes pass.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from core.config import DATA_DIR, get_engines_dir

VENDOR = Path(__file__).resolve().parents[1] / "integrations/avatar/vendor/tbdub_student"
RECIPE = "student-wsl-v1"
_lock = threading.RLock()
_thread = None
_cancel = threading.Event()


def manifest():
    return json.loads((VENDOR / "install-manifest.json").read_text())


def state_dir():
    return Path(DATA_DIR) / "video-install"


def candidate():
    return Path(get_engines_dir()) / "tbdub-student" / "versions" / RECIPE


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    os.replace(tmp, path)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def valid(path, spec, *, checksum=True):
    path = Path(path)
    return (path.is_file() and path.stat().st_size == spec["size"]
            and (not checksum or digest(path) == spec["sha256"]))


def active_install():
    try:
        data = json.loads((state_dir() / "active.json").read_text())
        root = Path(data["root"])
        marker = json.loads((root / ".tbdub-install.json").read_text())
        if marker["recipe"] == data["recipe"] and (root / ".venv/bin/python").is_file():
            return root
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def supported():
    if sys.platform != "linux" or platform.machine().lower() not in ("x86_64", "amd64"):
        return False, "TBDub Student requires x86-64 Linux/WSL and an NVIDIA GPU."
    smi = shutil.which("nvidia-smi") or "/usr/lib/wsl/lib/nvidia-smi"
    if not Path(smi).is_file():
        return False, "NVIDIA driver was not found in Linux/WSL."
    return True, ""


def _install_running():
    if _thread and _thread.is_alive():
        return True
    path = state_dir()/'install.lock'
    if not path.exists() or sys.platform != 'linux':
        return False
    import fcntl
    with path.open('a') as f:
        try:
            fcntl.flock(f,fcntl.LOCK_EX | fcntl.LOCK_NB)
            return False
        except BlockingIOError:
            return True


def status():
    from services import video_backend
    with _lock:
        try:
            job = json.loads((state_dir() / "job.json").read_text())
        except (OSError, ValueError):
            job = None
        if job and job["state"] == "running" and not _install_running():
            job.update(state="interrupted", error="Install interrupted; retry to resume.")
            atomic_json(state_dir() / "job.json", job)
    ok, reason = supported()
    return {"installed": video_backend.list_backends()[0]["available"],
            "supported": ok, "reason": reason, "job": job,
            "download_bytes": sum(s["size"] for s in manifest()["files"]),
            "install_dir": str(candidate())}


def _redact(value):
    text = re.sub(r"(?i)(https?://)[^\s/@]+:[^\s/@]+@", r"\1[redacted]@", str(value))
    text = re.sub(r"(?i)([?&](?:token|key|signature|credential|policy|x-amz-[\w-]+)=)[^\s&]+", r"\1[redacted]", text)
    text = re.sub(r"hf_[A-Za-z0-9]{12,}", "[redacted]", text)
    return text


class Cancelled(RuntimeError):
    pass


def _cancel_requested():
    return _cancel.is_set() or (state_dir()/'cancel-requested').exists()


class Install:
    def __init__(self, root, *, activate=True, with_rife=False):
        self.root = Path(root)
        self.activate = activate
        self.with_rife = with_rife
        self.job = {"state": "running", "step": "preflight", "log": [],
                    "started_at": time.time(), "error": None, "progress": None}
        self.last_write = 0.0

    def update(self, *, force=True, **fields):
        with _lock:
            self.job.update(fields)
            if force or time.monotonic() - self.last_write > .7:
                atomic_json(state_dir() / "job.json", self.job)
                self.last_write = time.monotonic()

    def log(self, message):
        self.job["log"] = (self.job["log"] + [_redact(message).strip()])[-100:]
        self.update(force=False)

    def check(self):
        if _cancel_requested():
            raise Cancelled("Install cancelled; downloaded files are kept for retry.")

    def run_command(self, argv, *, timeout=3600, env=None):
        from core.contained_subprocess import spawn_owned
        self.check()
        self.log("Running " + " ".join(str(x) for x in argv[:5]))
        proc = spawn_owned([str(x) for x in argv], stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                           errors="replace", env=env, cwd=str(self.root))
        def drain():
            for line in proc.stdout:
                self.log(line)
        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        deadline = time.monotonic() + timeout
        try:
            while proc.poll() is None:
                self.check()
                if time.monotonic() > deadline:
                    raise RuntimeError("Installation command timed out; retry to resume.")
                time.sleep(.2)
            reader.join(5)
            if proc.returncode:
                raise RuntimeError("Installation command failed (exit " + str(proc.returncode) + "). See install log.")
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)
            reader.join(2)

    def download(self, spec):
        """Pinned URL + resumable range transfer; never publish unverified data."""
        import httpx
        from services import endpoint_race
        from services.hf_auth import token_for_endpoint
        from services.token_resolver import resolve
        destination = self.root / "checkpoints" / spec["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.check()
        if valid(destination, spec):
            self.log("Verified existing " + spec["path"])
            return destination
        partial = destination.with_name(destination.name + ".partial")
        # Already-downloaded weights may be reused from the active install.
        # They are hashed first, and are NEVER overwritten or moved.
        from services import video_backend
        previous = video_backend.root() / "checkpoints" / spec["path"]
        if previous != destination and valid(previous, spec):
            partial.unlink(missing_ok=True)
            try:
                os.link(previous, partial)
            except OSError:
                shutil.copyfile(previous, partial)
            os.replace(partial, destination)
            self.log("Reused checksum-verified " + spec["path"])
            return destination
        if valid(partial, spec):
            os.replace(partial,destination)
            return destination
        if spec['size'] >= 32 * 1024**2:
            return self.download_parallel(spec, destination, partial)
        for attempt in range(4):
            self.check()
            if partial.exists() and partial.stat().st_size >= spec["size"]:
                if valid(partial, spec):
                    os.replace(partial, destination)
                    return destination
                partial.unlink()
            offset = partial.stat().st_size if partial.exists() else 0
            headers = {"Accept-Encoding": "identity"}
            if offset:
                headers["Range"] = f"bytes={offset}-"
            endpoint = endpoint_race.effective_endpoint() or "https://huggingface.co"
            if "repo" in spec:
                url = endpoint.rstrip("/") + "/" + spec["repo"] + "/resolve/" + spec["revision"] + "/" + spec["filename"]
                record = resolve()
                token = token_for_endpoint(endpoint, getattr(record, "token", None))
                if token:
                    headers["Authorization"] = "Bearer " + token
            else:
                url = spec["url"]
            try:
                with httpx.stream("GET", url, headers=headers, follow_redirects=True,
                                  timeout=httpx.Timeout(90, connect=20)) as response:
                    response.raise_for_status()
                    if response.status_code == 206:
                        content_range = response.headers.get("content-range", "")
                        if not content_range.startswith(f"bytes {offset}-") or not content_range.endswith("/" + str(spec["size"])):
                            raise RuntimeError("Server returned an invalid download range.")
                    elif response.status_code == 200:
                        offset = 0
                    else:
                        raise RuntimeError("Unexpected download response.")
                    mode = "ab" if offset else "wb"
                    start = time.monotonic()
                    transferred = 0
                    with partial.open(mode) as f:
                        for chunk in response.iter_bytes(1024 * 1024):
                            self.check()
                            if offset + transferred + len(chunk) > spec["size"]:
                                raise RuntimeError("Downloaded file exceeds the pinned size.")
                            f.write(chunk)
                            transferred += len(chunk)
                            self.update(force=False, progress={"filename": spec["path"],
                                "downloaded": offset + transferred, "total": spec["size"],
                                "bytes_per_second": round(transferred / max(.1, time.monotonic() - start))})
                if not valid(partial, spec):
                    if partial.stat().st_size == spec["size"]:
                        partial.unlink()
                    raise RuntimeError("Model size or SHA-256 did not match the pinned manifest.")
                os.replace(partial, destination)
                self.log("SHA-256 verified " + spec["path"])
                return destination
            except Cancelled:
                raise
            except Exception as exc:
                self.log(type(exc).__name__ + ": " + _redact(exc))
                if attempt == 3:
                    raise RuntimeError("Download failed for " + spec["path"] + "; retry resumes incomplete files.") from exc
                if "repo" in spec:
                    endpoint_race.reselect_after_failure(spec["repo"], str(exc))
                time.sleep(1)
        raise AssertionError("unreachable")

    def download_parallel(self, spec, destination, partial):
        """Reuse the app's segmented downloader, including its auth boundary."""
        import asyncio
        from services import endpoint_race, segmented_download as sd
        from services.hf_auth import token_for_endpoint
        from services.token_resolver import resolve
        # Migrate a contiguous partial from the single-stream implementation.
        # Segment completion, never preallocated file size, drives progress.
        part = Path(str(partial)+'.part')
        if partial.exists() and not part.exists():
            prefix = min(partial.stat().st_size, spec['size'])
            os.replace(partial, part)
            with part.open('r+b') as f:f.truncate(spec['size'])
            done = {s for s in sd._plan_segments(spec['size'],8) if s[1] < prefix}
            sd._save_done(str(part), spec['size'], done)
        for attempt in range(4):
            self.check()
            endpoint = endpoint_race.effective_endpoint() or 'https://huggingface.co'
            url = endpoint.rstrip('/')+'/'+spec['repo']+'/resolve/'+spec['revision']+'/'+spec['filename']
            record = resolve()
            token = token_for_endpoint(endpoint,getattr(record,'token',None))
            done = sd._load_done(str(part), spec['size'])
            downloaded = sum(end-start+1 for start,end in done)
            started = time.monotonic();received = 0
            def progress(count):
                nonlocal received
                received += count
                self.update(force=False, progress={'filename':spec['path'],
                    'downloaded':min(spec['size'],downloaded+received), 'total':spec['size'],
                    'bytes_per_second':round(received/max(.1,time.monotonic()-started))})
            try:
                asyncio.run(sd.segmented_download(url,str(partial),token=token,
                    expected_size=spec['size'],expected_etag=spec['sha256'],num_connections=8,
                    on_bytes=progress,cancel_check=_cancel_requested,timeout=90))
                self.check()
                os.replace(partial,destination)
                self.log('SHA-256 verified '+spec['path'])
                return destination
            except sd.DownloadCancelled as exc:
                raise Cancelled('Install paused; downloaded segments are kept.') from exc
            except Exception as exc:
                self.log(type(exc).__name__+': '+_redact(exc))
                if attempt == 3:
                    raise RuntimeError('Download failed for '+spec['path']+'; retry resumes incomplete segments.') from exc
                endpoint_race.reselect_after_failure(spec['repo'],str(exc))
                time.sleep(1)
        raise AssertionError('unreachable')

    def run(self):
        import fcntl
        from services import media_tools, sidecar_install
        self.root.mkdir(parents=True, exist_ok=True)
        state_dir().mkdir(parents=True, exist_ok=True)
        with (state_dir() / "install.lock").open("a") as lockfile:
            try:
                fcntl.flock(lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("Another process is already installing TBDub Student.")
            try:
                self.update()
                ok, reason = supported()
                if not ok:
                    raise RuntimeError(reason)
                uv = sidecar_install._locate_uv()
                if not uv:
                    raise RuntimeError("uv was not found; install the VoiceStudio runtime first.")
                if shutil.disk_usage(self.root).free < 30 * 1024**3:
                    raise RuntimeError("At least 30 GiB of free space is needed for this install.")
                # Use the app's media-tool installer, rather than apt/sudo.
                if not media_tools.status()["ready"]:
                    result = media_tools.acquire_bundled(wait=True)
                    if not media_tools.status()["ready"]:
                        raise RuntimeError("FFmpeg installation failed: " + str(result.get("error", "")))
                bindir = self.root / "bin"
                bindir.mkdir(exist_ok=True)
                for tool in ("ffmpeg", "ffprobe"):
                    target = media_tools._resolve(tool)
                    link = bindir / tool
                    if not link.exists():
                        link.symlink_to(target)
                self.update(step="source")
                source_hashes = {}
                for source in VENDOR.rglob("*"):
                    if not source.is_file() or "__pycache__" in source.parts:
                        continue
                    relative = source.relative_to(VENDOR)
                    dest = self.root / relative
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, dest)
                    source_hashes[str(relative)] = digest(dest)
                self.update(step="environment")
                env = sidecar_install.uv_subprocess_env(self.root.parent)
                env["PYTHONNOUSERSITE"] = "1"
                # uv may discover the parent app project without this isolation.
                env.pop("VIRTUAL_ENV", None)
                python = self.root / ".venv/bin/python"
                if not python.is_file():
                    self.run_command([uv, "venv", "--python", "3.10", self.root / ".venv"], timeout=300, env=env)
                deps_key = digest(self.root / "requirements.txt")
                deps_marker = self.root / ".dependencies-verified"
                if not deps_marker.exists() or deps_marker.read_text().strip() != deps_key:
                    self.run_command([uv, "pip", "install", "--python", python,
                        "torch==2.9.0", "torchvision==0.24.0", "torchaudio==2.9.0",
                        "--index-url", "https://download.pytorch.org/whl/cu128"], env=env)
                    self.run_command([uv, "pip", "install", "--python", python, "-r", self.root / "requirements.txt"], env=env)
                self.update(step="models")
                files = [s for s in manifest()["files"] if self.with_rife or s.get("role") != "rife"]
                verified = {}
                for spec in files:
                    p = self.download(spec)
                    verified[spec["path"]] = {"sha256": spec["sha256"], "size": p.stat().st_size,
                                               "mtime_ns": p.stat().st_mtime_ns}
                self.update(step="verify", progress=None)
                probe_env = dict(env, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                                 PYTHONPATH=str(self.root), PATH=str(bindir) + os.pathsep + env.get("PATH", ""))
                self.run_command([python, "-c", "import torch,cv2,mediapipe,transformers,safetensors,torchaudio; from diffsynth.pipelines.tbdub import TBDubPipeline; from transformers import HubertModel; assert torch.__version__.startswith('2.9.0'); assert cv2.__version__.startswith('4.10.'); assert mediapipe.__version__=='0.10.21'; assert torch.cuda.is_available(); print(torch.cuda.get_device_name(0))"], timeout=180, env=probe_env)
                self.run_command([python, self.root / "inference.py", "--help"], timeout=180, env=probe_env)
                self.check()
                deps_marker.write_text(deps_key)
                atomic_json(self.root / ".tbdub-install.json", {"recipe": RECIPE,
                    "verified_at": time.time(), "files": verified, "source_hashes": source_hashes})
                if self.activate:
                    self.update(step="activate")
                    # Activation is one atomic pointer write, never in-place replacement.
                    atomic_json(state_dir() / "active.json", {"root": str(self.root), "recipe": RECIPE})
                self.update(state="succeeded", step="complete", finished_at=time.time(), progress=None)
            except Exception as exc:
                self.update(state="cancelled" if isinstance(exc, Cancelled) else "failed",
                            error=_redact(exc), finished_at=time.time())
                raise


    def addon(self):
        """RIFE is optional and does not replace the installed Student runtime."""
        import fcntl
        state_dir().mkdir(parents=True, exist_ok=True)
        with (state_dir()/"install.lock").open("a") as lockfile:
            fcntl.flock(lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                self.update(step="models")
                spec = next(s for s in manifest()["files"] if s["role"] == "rife")
                path = self.download(spec)
                folder = VENDOR/"webui/rife_v425"
                shutil.copytree(folder, self.root/"webui/rife_v425", dirs_exist_ok=True)
                shutil.copyfile(VENDOR/"webui/interpolate_25_to_30.py", self.root/"webui/interpolate_25_to_30.py")
                marker = self.root/".tbdub-install.json"
                if marker.exists():
                    data = json.loads(marker.read_text())
                    data['files'][spec['path']] = dict(sha256=spec['sha256'], size=path.stat().st_size, mtime_ns=path.stat().st_mtime_ns)
                    atomic_json(marker, data)
                self.update(state="succeeded", step="complete", finished_at=time.time(), progress=None)
            except Exception as exc:
                self.update(state="cancelled" if isinstance(exc, Cancelled) else "failed", error=_redact(exc), finished_at=time.time())
                raise


def start_install(component="student"):
    global _thread
    ok, reason = supported()
    if not ok:
        raise ValueError(reason)
    with _lock:
        if _install_running():
            return {"status": "already_running"}
        from services import video_backend
        backend = video_backend.list_backends()[0]
        if component == "rife" and not backend['available']:
            raise ValueError("Install TBDub Student before installing the optional 60 fps component.")
        if component == "student" and backend["available"]:
            return {"status": "already_installed"}
        if component == "rife" and 60 in backend['output_fps']:
            return {"status": "already_installed"}
        _cancel.clear()
        (state_dir()/'cancel-requested').unlink(missing_ok=True)
        task = Install(video_backend.root() if component == 'rife' else candidate())
        def run():
            try:
                task.addon() if component == 'rife' else task.run()
            except Exception as exc:
                # Preflight filesystem failures can happen before run() enters
                # its persistent-job context. Surface those failures as well.
                if task.job['state'] == 'running' and not isinstance(exc, BlockingIOError) and not str(exc).startswith('Another process'):
                    task.update(state='failed',error=_redact(exc),finished_at=time.time())
        _thread = threading.Thread(target=run, daemon=True, name="tbdub-install")
        _thread.start()
        return {"status": "started"}


def cancel_install():
    _cancel.set()
    state_dir().mkdir(parents=True,exist_ok=True)
    (state_dir()/'cancel-requested').touch()
    return {"status": "cancelling"}
