"""Bounded-memory FFmpeg product rendering; no model or GPU is loaded here."""
from __future__ import annotations

import asyncio
import contextlib
import json
import math
import os
from pathlib import Path
import textwrap

from core.job_queue import Job
from production.models import Caption, ProductPlan
from services.ffmpeg_utils import find_ffmpeg, find_ffprobe, run_ffmpeg, spawn_subprocess
from services.srt_parser import parse_srt

WIDTH, HEIGHT, FPS = 1080, 1920, 30


async def probe(path: Path, *, video_required: bool = True) -> dict:
    tool = find_ffprobe()
    if not tool:
        raise ValueError("FFprobe is missing. Install media tools in Settings.")
    proc = await spawn_subprocess(
        tool, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), 60)
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
    if proc.returncode:
        raise ValueError("Cannot read this video. Upload a decodable video file.")
    raw = json.loads(out)
    video = next((s for s in raw.get("streams", []) if s["codec_type"] == "video" and not s.get("disposition", {}).get("attached_pic")), None)
    if video is None and (video_required or not any(s['codec_type'] == 'audio' for s in raw.get('streams', []))):
        raise ValueError("The file has no video stream.")
    duration = float((video or {}).get("duration") or raw.get("format", {}).get("duration", 0))
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("The video has no valid duration.")
    return {
        "duration": duration, "width": int((video or {}).get("width", 0)), "height": int((video or {}).get("height", 0)),
        "frame_rate": (video or {}).get("avg_frame_rate", "0/1"), "kind": 'video' if video else 'audio',
        "has_audio": any(s["codec_type"] == "audio" for s in raw["streams"]),
        "has_subtitles": any(s["codec_type"] == "subtitle" for s in raw["streams"]),
    }


async def command(args: list[str], job: Job) -> None:
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise ValueError("FFmpeg is missing. Install media tools in Settings.")
    if job.is_cancelled:
        raise asyncio.CancelledError()
    task = asyncio.create_task(run_ffmpeg(
        [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *args],
        timeout=24 * 3600, job_id=job.id,
    ))
    cancelled = asyncio.create_task(job.cancel_event.wait())
    try:
        done, _ = await asyncio.wait((task, cancelled), return_when=asyncio.FIRST_COMPLETED)
        if cancelled in done:
            raise asyncio.CancelledError()
        code, _, err = await task
        if code:
            raise RuntimeError("FFmpeg: " + err.decode("utf-8", errors="replace")[-2000:])
    finally:
        for pending in (task, cancelled):
            if not pending.done():
                pending.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pending


def ass_time(seconds: float) -> str:
    ticks = round(seconds * 100)
    hours, ticks = divmod(ticks, 360000)
    minutes, ticks = divmod(ticks, 6000)
    whole, fraction = divmod(ticks, 100)
    return f"{hours}:{minutes:02}:{whole:02}.{fraction:02}"


def ass_text(text: str, wrap: int) -> str:
    # Display user text literally rather than interpreting ASS override tags.
    text = text.replace("\\", "\uff3c").replace("{", "\uff5b").replace("}", "\uff5d")
    lines = []
    for line in text.splitlines():
        if any(c.isspace() for c in line):
            lines.extend(textwrap.wrap(line, width=wrap, break_long_words=False, break_on_hyphens=False))
        else:
            lines.extend(line[i:i + wrap] for i in range(0, len(line), wrap))
    return "\\N".join(lines)


def write_ass(path: Path, events: list[tuple[float, float, str, str]], *, cover: bool = False) -> None:
    height = 1440 if cover else HEIGHT
    font = "Microsoft YaHei" if os.name == "nt" else "Noto Sans CJK SC"
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {WIDTH}
PlayResY: {height}
WrapStyle: 0
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Title,{font},72,&H00FFFFFF,&H00FFFFFF,&H00202020,&H80000000,-1,0,0,0,100,100,1,0,1,4,1,8,75,75,150,1
Style: Brand,{font},42,&H00FFFFFF,&H00FFFFFF,&H00202020,&H80000000,0,0,0,0,100,100,0,0,1,3,1,8,75,75,80,1
Style: Subtitle,{font},52,&H00FFFFFF,&H00FFFFFF,&H00202020,&H80000000,0,0,0,0,100,100,0,0,1,3,1,2,70,70,220,1
Style: Cover,{font},88,&H00FFFFFF,&H00FFFFFF,&H00202020,&H80000000,-1,0,0,0,100,100,1,0,1,4,1,2,75,75,160,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    rows = [f"Dialogue: 0,{ass_time(start)},{ass_time(end)},{style},,0,0,0,,{ass_text(text, 12 if style in ('Title', 'Cover') else 20)}" for start, end, style, text in events if end > start]
    path.write_text(header + "\n".join(rows) + "\n", encoding="utf-8")


def ass_filter(path: Path) -> str:
    normalized = str(path.resolve())
    if os.name == "nt":
        normalized = normalized.replace("\\", "/")
    escaped = normalized.replace("\\", "\\\\").replace(":", "\\:").replace("'", "'" + "\\" * 3 + "''")
    return f"ass=filename='{escaped}'"


def normalize_filter(width: int = WIDTH, height: int = HEIGHT) -> str:
    return f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={FPS},format=yuv420p"


async def captions_for(plan: ProductPlan, source: Path, info: dict, work: Path, job: Job) -> list[Caption]:
    if plan.subtitle_mode == "provided":
        return plan.captions
    if plan.subtitle_mode != "keep" or not info["has_subtitles"]:
        return []
    srt = work / "embedded.srt"
    # Decode a real subtitle stream before normalization drops the container track.
    # Bitmap subtitles need a supplied text transcript rather than silent loss.
    await command(["-i", str(source), "-map", "0:s:0", str(srt)], job)
    parsed = parse_srt(srt.read_text(encoding="utf-8-sig"), preserve_overlaps=True)
    if not parsed.segments:
        raise ValueError("Embedded subtitles could not be read. Supply an SRT file.")
    return [Caption(start=s["start"], end=s["end"], text=s["text"]) for s in parsed.segments]


async def normalize(source: Path, dest: Path, info: dict, job: Job, overlay: Path | None = None) -> None:
    args = ["-i", str(source)]
    if not info["has_audio"]:
        args += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    vf = normalize_filter()
    if overlay:
        vf += "," + ass_filter(overlay)
    args += [
        "-map", "0:v:0", "-map", "0:a:0" if info["has_audio"] else "1:a:0",
        "-vf", vf, "-af", "aresample=48000,apad", "-t", str(info["duration"]),
        "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-threads", "2", "-filter_threads", "2",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-video_track_timescale", "15360",
        "-movflags", "+faststart", str(dest),
    ]
    await command(args, job)


async def render_product(plan: ProductPlan, source: Path, outro: Path, work: Path, job: Job, step) -> dict[str, str]:
    work.mkdir(parents=True, exist_ok=True)
    step("inspect", "running")
    source_info, outro_info = await asyncio.gather(probe(source), probe(outro))
    duration = source_info["duration"]
    if plan.cover_time >= duration:
        raise ValueError("Cover frame is outside the source video.")
    if any(c.start >= duration or c.end > duration + 0.04 for c in plan.captions):
        raise ValueError("A subtitle is outside the source video.")
    captions = await captions_for(plan, source, source_info, work, job)
    events = [(0, min(5, duration), "Title", plan.title)]
    if plan.brand_text and duration > 5:
        events.append((5, duration, "Brand", plan.brand_text))
    events.extend((c.start, min(c.end, duration), "Subtitle", c.text) for c in captions)
    overlay = work / "product.ass"
    write_ass(overlay, events)
    step("inspect", "done")

    step("render", "running")
    base, tail = work / "body.mp4", work / "outro.mp4"
    await normalize(source, base, source_info, job, overlay)
    await normalize(outro, tail, outro_info, job)
    # Both inputs now share codecs, timebase, frame rate, dimensions and audio.
    concat = work / "concat.txt"
    concat.write_text("file 'body.mp4'\nfile 'outro.mp4'\n", encoding="utf-8")
    video = work / "video.mp4"
    await command(["-f", "concat", "-safe", "1", "-i", str(concat), "-c", "copy", "-movflags", "+faststart", str(video)], job)
    step("render", "done")

    step("cover", "running")
    cover_ass = work / "cover.ass"
    write_ass(cover_ass, [(0, 86400, "Cover", plan.cover_title)], cover=True)
    cover = work / "cover.jpg"
    # Select from the untouched source, not the title/subtitle overlay.
    await command(["-ss", str(plan.cover_time), "-i", str(source), "-vf", normalize_filter(1080, 1440) + "," + ass_filter(cover_ass), "-frames:v", "1", "-q:v", "2", "-threads", "2", str(cover)], job)
    if not cover.is_file() or not cover.stat().st_size:
        raise ValueError("No frame at the chosen cover time. Select an earlier time.")
    (work / "publication.txt").write_text(plan.publication_title.strip() + "\n", encoding="utf-8")
    (work / "cover-title.md").write_text("# " + plan.cover_title.strip() + "\n", encoding="utf-8")
    step("cover", "done")

    step("verify", "running")
    result = await probe(video)
    expected = source_info["duration"] + outro_info["duration"]
    rate = result["frame_rate"].split("/")
    fps = float(rate[0]) / float(rate[1]) if len(rate) == 2 and float(rate[1]) else 0
    if (result["width"], result["height"]) != (WIDTH, HEIGHT) or abs(result["duration"] - expected) > 0.2 or abs(fps - FPS) > 0.01:
        raise RuntimeError("Output size or duration verification failed.")
    # Decode verification catches an incomplete/corrupt output before delivery.
    await command(["-xerror", "-i", str(video), "-f", "null", "-"], job)
    step("verify", "done")
    return {"video": "video.mp4", "cover": "cover.jpg", "publication": "publication.txt", "cover_title": "cover-title.md"}
