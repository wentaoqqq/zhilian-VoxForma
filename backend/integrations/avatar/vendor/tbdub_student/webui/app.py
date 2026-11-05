#!/usr/bin/env python3
"""Tiny LAN UI for running the tested TBDub Student pipeline."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse
from unified_jobs import JobManager, QueueFullError

ROOT = Path(os.environ.get("OMNIVOICE_TBDUB_DIR", str(Path(__file__).resolve().parents[1])))
PYTHON = Path(os.environ.get("OMNIVOICE_TBDUB_PYTHON", str(ROOT/".venv/bin/python")))
from avatar_paths import assets_dir, smi_path
ASSETS = assets_dir()
OUT_ROOT = ROOT / "results" / "webui"
HOST = os.environ.get("TBDUB_WEB_HOST", "0.0.0.0")
PORT = int(os.environ.get("TBDUB_WEB_PORT", "8765"))
MANAGER = JobManager(ROOT, ASSETS, PYTHON)
JOBS = MANAGER.jobs
JOBS_LOCK = MANAGER.lock
ACTIVE_JOB: str | None = None
PREVIEW_LOCK = threading.Lock()
PREVIEW_CACHE = ROOT / "webui" / "preview_cache"

PAGE = (Path(__file__).parent / "index.html").read_text(encoding="utf-8")

MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024
UPLOAD_EXTENSIONS = {"video": {".mp4", ".mov", ".mkv"}, "audio": {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".oga", ".opus", ".aac", ".webm"},
                     "emotion": {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".oga", ".opus", ".aac", ".webm"}}
MEDIA_TYPES = {
    ".mp4": "video/mp4", ".mov": "video/quicktime", ".mkv": "video/x-matroska",
    ".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4",
    ".flac": "audio/flac", ".ogg": "audio/ogg", ".oga": "audio/ogg", ".opus": "audio/ogg", ".aac": "audio/aac", ".webm": "audio/webm",
}


def resolve_media(relative: str) -> Path:
    file = (ASSETS / relative).resolve()
    if not relative or ASSETS.resolve() not in file.parents:
        raise ValueError("素材路径无效")
    if file.suffix.lower() not in MEDIA_TYPES or not file.is_file():
        raise FileNotFoundError("素材不存在或格式不支持")
    return file


def compatible_preview(source: Path) -> Path:
    """Cache a small browser-compatible excerpt; never touch the inference input."""
    stat = source.stat()
    key = hashlib.sha256(f"preview-v1:{source}:{stat.st_size}:{stat.st_mtime_ns}".encode()).hexdigest()
    video = source.suffix.lower() in UPLOAD_EXTENSIONS["video"]
    destination = PREVIEW_CACHE / (key + (".mp4" if video else ".mp3"))
    if destination.is_file():
        return destination
    if not PREVIEW_LOCK.acquire(timeout=60):
        raise RuntimeError("预览转换繁忙，请稍后重试")
    temp = destination.with_name(key + f".{uuid.uuid4().hex}.partial" + destination.suffix)
    try:
        if destination.is_file():
            return destination
        PREVIEW_CACHE.mkdir(parents=True, exist_ok=True)
        command = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                   "-threads", "2", "-i", str(source), "-t", "20" if video else "60"]
        if video:
            command += ["-map", "0:v:0", "-map", "0:a:0?", "-filter_threads", "1", "-vf",
                        "scale=640:640:force_original_aspect_ratio=decrease:force_divisible_by=2,fps=25",
                        "-c:v", "libx264", "-threads", "2", "-preset", "ultrafast", "-crf", "27",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart"]
        else:
            command += ["-vn", "-c:a", "libmp3lame", "-b:a", "96k"]
        command.append(str(temp))
        result = subprocess.run(command, capture_output=True, text=True, timeout=90)
        if result.returncode:
            print(f"[preview] {source}: {result.stderr[-1500:]}", flush=True)
            raise RuntimeError("无法转换此素材的兼容预览")
        temp.replace(destination)
        return destination
    finally:
        temp.unlink(missing_ok=True)
        PREVIEW_LOCK.release()


def media_inventory() -> dict[str, list[dict[str, str]]]:
    groups: dict[str, list[dict[str, str]]] = {"videos": [], "audios": []}
    allowed = {"videos": {".mp4", ".mov", ".mkv"}, "audios": {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".oga", ".opus", ".aac", ".webm"}}
    for category, exts in allowed.items():
        for p in sorted(ASSETS.rglob("*")):
            if p.is_file() and p.suffix.lower() in exts:
                try:
                    rel = p.relative_to(ASSETS).as_posix()
                except ValueError:
                    continue
                groups[category].append({"id": rel, "name": rel})
    return groups


def completed_job(job_id: str) -> dict:
    """Keep finished result links usable after a UI service update/restart."""
    if not re.fullmatch(r"student_\d{8}_\d{6}_[0-9a-f]{6}", job_id):
        return {}
    folder = OUT_ROOT / job_id
    output = folder / f"{job_id}_tbdub.mp4"
    try:
        with (folder / "run.log").open("rb") as log:
            log.seek(0, os.SEEK_END)
            log.seek(max(0, log.tell() - 8192))
            tail = log.read().decode(errors="replace")
        size = output.stat().st_size
        if "\nRUN_EXIT=0\n" not in tail or not size:
            return {}
        return {"id": job_id, "status": "succeeded", "message": "生成完成。",
                "output": str(output), "size": f"{size / 1024 / 1024:.1f} MB"}
    except OSError:
        return {}


class Handler(BaseHTTPRequestHandler):
    server_version = "TBDubLAN/1.0"

    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {fmt % args}", flush=True)

    def send_json(self, code: int, data: dict) -> None:
        raw = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)

    def send_media(self, file: Path, disposition: str = "inline") -> None:
        """Stream one byte range so browser players can seek without loading the clip."""
        try:
            stream = file.open("rb")
        except OSError:
            self.send_json(404, {"error": "文件不存在或无法读取"})
            return
        with stream:
            size = os.fstat(stream.fileno()).st_size
            start, end = 0, size - 1
            byte_range = self.headers.get("Range")
            if byte_range:
                match = re.fullmatch(r"bytes=(\d*)-(\d*)", byte_range.strip())
                try:
                    if not match or not any(match.groups()) or size == 0:
                        raise ValueError()
                    left, right = match.groups()
                    if left:
                        start = int(left)
                        end = min(int(right), size - 1) if right else size - 1
                    else:
                        suffix = int(right)
                        if suffix <= 0:
                            raise ValueError()
                        start = max(0, size - suffix)
                    if start >= size or end < start:
                        raise ValueError()
                except ValueError:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
            length = max(0, end - start + 1)
            self.send_response(206 if byte_range else 200)
            self.send_header("Content-Type", MEDIA_TYPES.get(file.suffix.lower(), "application/octet-stream"))
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Disposition", f"{disposition}; filename*=UTF-8''{quote(file.name, safe='')}")
            if byte_range:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if self.command == "HEAD":
                return
            stream.seek(start)
            try:
                while length:
                    chunk = stream.read(min(256 * 1024, length))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    length -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass  # Changing the selected material aborts the previous media request.

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        global ACTIVE_JOB
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/":
            raw = PAGE.encode()
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(raw)
            return
        if path == "/api/runtime":
            self.send_json(200, {"root": str(ROOT.resolve()), "assets": str(ASSETS.resolve()), "protocol": 1}); return
        if path == "/api/files":
            self.send_json(200, media_inventory()); return
        if path == "/api/media":
            query = parse_qs(parsed.query)
            try:
                file = resolve_media(query.get("id", [""])[0])
                if query.get("preview", [""])[0] == "1":
                    file = compatible_preview(file)
            except ValueError as e:
                self.send_json(400, {"error": str(e)}); return
            except FileNotFoundError as e:
                self.send_json(404, {"error": str(e)}); return
            except Exception as e:
                self.send_json(503, {"error": str(e)}); return
            self.send_media(file)
            return
        if path == "/api/gpu":
            try:
                result = subprocess.run(
                    [smi_path(), "--query-gpu=memory.used,memory.free,memory.total", "--format=csv,noheader,nounits"],
                    check=True, capture_output=True, text=True, timeout=3,
                )
                used, free, total = (int(x.strip()) for x in result.stdout.splitlines()[0].split(","))
                self.send_json(200, {"used_mib": used, "free_mib": free, "total_mib": total,
                                     "used_gib": round(used / 1024, 1), "free_gib": round(free / 1024, 1),
                                     "total_gib": round(total / 1024, 1)})
            except Exception as e:
                self.send_json(503, {"error": str(e)})
            return
        parts = path.strip("/").split("/")
        if path == "/api/jobs":
            with JOBS_LOCK:
                running = sum(j["status"] == "running" for j in JOBS.values())
                waiting = sum(j["status"] == "queued" for j in JOBS.values())
                requested = parse_qs(parsed.query).get('request_id', [None])[0]
                matching = [j for j in JOBS.values() if requested is None or j.get('request_id') == requested]
                jobs = sorted(matching, key=lambda j: j.get("created", 0), reverse=True)[:30]
                jobs = [dict(j) for j in jobs]
            self.send_json(200, {"jobs": jobs, "queue": {"running": running, "waiting": waiting,
                           "capacity": MANAGER.capacity, "full": running + waiting >= MANAGER.capacity}}); return
        if len(parts) >= 3 and parts[:2] == ["api", "jobs"]:
            jid = parts[2]
            with JOBS_LOCK:
                job = dict(JOBS.get(jid, {}))
            if not job:
                job = completed_job(jid)
            if not job:
                self.send_json(404, {"error": "任务不存在"}); return
            if len(parts) == 4 and parts[3] in {"audio", "audio-download"} and job.get("audio_output"):
                file = Path(job["audio_output"])
                if not file.is_file():
                    self.send_json(404, {"error": "配音文件不存在"}); return
                self.send_media(file, "attachment" if parts[3] == "audio-download" else "inline"); return
            if len(parts) == 4 and parts[3] in {"video", "download"} and job.get("status") == "succeeded" and job.get("output"):
                file = Path(job["output"])
                disposition = "attachment" if parts[3] == "download" else "inline"
                self.send_media(file, disposition)
                return
            self.send_json(200, job); return
        self.send_json(404, {"error": "not found"})

    def do_POST(self):
        global ACTIVE_JOB
        parsed = urlparse(self.path)
        if parsed.path == "/api/upload":
            self.handle_upload(parsed)
            return
        if self.path != "/api/jobs":
            self.send_json(404, {"error": "not found"}); return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 65536:
                self.send_json(413, {"error": "提交内容过大或为空。"}); return
            data = json.loads(self.rfile.read(length))
            inventory = media_inventory()
            videos = {f["id"] for f in inventory["videos"]}
            audios = {f["id"] for f in inventory["audios"]}
            video_rel, audio_rel = data.get("video"), data.get("audio")
            mode = data.get("mode", "audio")
            audio_only = data.get("audio_only", False)
            if mode not in ("audio", "tts") or type(audio_only) is not bool or (audio_only and mode != "tts"):
                raise ValueError("无效任务类型。")
            fps = data.get("fps", 25)
            if type(fps) is not int or fps not in (25, 60):
                self.send_json(400, {"error": "成片帧率仅支持 25 或 60 fps。"}); return
            if not audio_only:
                if video_rel not in videos: raise ValueError("请选择有效形象视频。")
                resolve_media(video_rel)
            payload = {"video": video_rel if not audio_only else None, "fps": fps,
                       "mode": mode, "audio_only": audio_only}
            if data.get('character_id'):
                for key in ('character_id','character_name','character_revision'):
                    value=data.get(key)
                    if not isinstance(value,str) or not 1<=len(value)<=200:
                        raise ValueError('Invalid character snapshot')
                    payload[key]=value
            if mode == "tts":
                text = data.get("text", "")
                reference = data.get("reference")
                emotion = (data.get("emotion") or None) if data.get("emotion_enabled") is not False else None
                weight = data.get("emotion_weight", 1.0) if data.get("emotion_enabled") is not False else 1.0
                if not isinstance(text, str) or not 1 <= len(text.strip()) <= 5000:
                    raise ValueError("请输入 1～5000 字配音文案。")
                native = data.get('native_form')
                if native is not None and (data.get('tts_provider')!='voicestudio' or not isinstance(native,dict) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in native.items())):
                    raise ValueError('Invalid native VoiceStudio form')
                if reference or native is None:
                    if reference not in audios: raise ValueError("请选择音色参考音频。")
                    resolve_media(reference)
                if native is not None:
                    payload['native_form']=native
                    payload['voice_profile']=str(data.get('voice_profile') or '')
                if emotion:
                    if emotion not in audios: raise ValueError("情感参考音频无效。")
                    resolve_media(emotion)
                if type(weight) not in (int, float) or not 0 <= weight <= 1:
                    raise ValueError("情感强度必须在 0～1 之间。")
                payload.update(text=text.strip(), reference=reference, emotion=emotion, emotion_weight=weight)
            else:
                reuse = data.get("reuse_audio_job")
                if reuse:
                    previous = MANAGER.get(reuse)
                    audio = Path(previous.get("audio_output", "/nonexistent"))
                    if not previous or not audio.is_file() or audio.parent != OUT_ROOT / reuse:
                        raise ValueError("可复用的配音不存在。")
                    payload["saved_audio"] = str(audio)
                else:
                    if audio_rel not in audios: raise ValueError("请选择有效配音音频。")
                    resolve_media(audio_rel)
                    payload["audio"] = audio_rel
            provider=data.get('tts_provider','legacy_indextts')
            engine=data.get('tts_engine','indextts2')
            if provider not in ('legacy_indextts','voicestudio') or not isinstance(engine,str) or not re.fullmatch(r'[a-z0-9_-]{1,80}',engine) or (provider=='legacy_indextts' and engine!='indextts2'):
                raise ValueError('Invalid TTS provider or engine')
            if provider=='voicestudio' and data.get('emotion_enabled') is not False and data.get('emotion') and engine!='indextts2':
                raise ValueError('Emotion audio requires IndexTTS')
            if 'emotion_enabled' in data and type(data['emotion_enabled']) is not bool:
                raise ValueError('emotion_enabled must be boolean')
            if data.get('emotion_enabled') is False:payload['emotion']=None
            request_id=data.get('request_id')
            if request_id and (not isinstance(request_id,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{8,96}',request_id)):
                raise ValueError('Invalid request_id')
            payload.update(tts_provider=provider,tts_engine=engine,ref_text=str(data.get('ref_text',''))[:10000])
            if request_id:
                payload['request_id']=request_id
                payload['request_fingerprint']=hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
            if data.get('reuse_audio_job'):payload['source_job']=data['reuse_audio_job']
            job = MANAGER.submit(payload)
            self.send_json(202, {"id": job["id"], "status": job["status"]})
        except QueueFullError as e:
            self.send_json(429, {"error": str(e)})
        except Exception as e:
            self.send_json(400, {"error": str(e)})

    def handle_upload(self, parsed) -> None:
        query = parse_qs(parsed.query)
        kind = query.get("kind", [""])[0]
        if kind not in UPLOAD_EXTENSIONS:
            self.send_json(400, {"error": "上传类型必须是 video、audio 或 emotion。"}); return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_json(411, {"error": "缺少有效的 Content-Length。"}); return
        if length <= 0 or length > MAX_UPLOAD_BYTES:
            self.send_json(413, {"error": "文件为空或超过 2GiB 上传上限。"}); return

        raw_name = unquote(query.get("name", [""])[0]).replace("\\", "/").split("/")[-1].strip()
        filename = Path(raw_name).name[:180]
        suffix = Path(filename).suffix.lower()
        if not filename or suffix not in UPLOAD_EXTENSIONS[kind]:
            self.send_json(400, {"error": "文件类型不支持。视频支持 mp4/mov/mkv；音频支持 wav/mp3/m4a/flac/ogg/opus/aac/webm。"}); return

        folder = ASSETS / {"video": "形象", "audio": "音频", "emotion": "情绪参考"}[kind]
        folder.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(folder).free < length + 128 * 1024 * 1024:
            self.send_json(507, {"error": "服务器 D 盘空间不足，至少需要文件大小加 128MiB 余量。"}); return

        target = folder / filename
        if target.exists():
            stamp = time.strftime("%Y%m%d_%H%M%S")
            target = folder / f"{Path(filename).stem}_{stamp}_{uuid.uuid4().hex[:4]}{suffix}"
        temp = folder / f".upload_{uuid.uuid4().hex}.part"
        remaining = length
        try:
            with temp.open("xb") as dst:
                while remaining:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ConnectionError("上传未完成，连接提前关闭。")
                    dst.write(chunk)
                    remaining -= len(chunk)
            temp.replace(target)
        except Exception as e:
            temp.unlink(missing_ok=True)
            self.send_json(400, {"error": f"保存上传文件失败：{e}"}); return

        rel = target.relative_to(ASSETS).as_posix()
        self.send_json(201, {"id": rel, "name": rel, "bytes": length})


if __name__ == "__main__":
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    MANAGER.start()
    print(f"TBDub UI listening on {HOST}:{PORT}; assets={ASSETS}", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
