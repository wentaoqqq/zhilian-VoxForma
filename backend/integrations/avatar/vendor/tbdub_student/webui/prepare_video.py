#!/usr/bin/env python3
"""Prepare a source video on TBDub's 25 fps timeline, up to the audio length.

TBDub indexes source frames as if they were 25 fps. Reading a 30/60 fps source
without this conversion changes the apparent speed of the source motion.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
from fractions import Fraction
from pathlib import Path


TARGET_FPS = 25
STUDENT_STRIDE = 72


def probe(path: Path, stream: str) -> dict:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", f"{stream}:0",
            "-show_entries", "stream=avg_frame_rate,duration:format=duration",
            "-of", "json", str(path),
        ],
        check=True, capture_output=True, text=True,
    )
    info = json.loads(result.stdout)
    if not info.get("streams"):
        raise ValueError(f"No {stream} stream in {path}")
    return {"stream": info["streams"][0], "format": info.get("format", {})}


def duration_seconds(info: dict) -> float:
    for value in (info["stream"].get("duration"), info["format"].get("duration")):
        try:
            seconds = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(seconds) and seconds > 0:
            return seconds
    raise ValueError("Media duration is unavailable or invalid")


def prepare(video: Path, audio: Path, output: Path) -> None:
    video_info = probe(video, "v")
    audio_info = probe(audio, "a")
    audio_seconds = duration_seconds(audio_info)
    source_fps = Fraction(video_info["stream"].get("avg_frame_rate", "0"))
    if source_fps <= 0:
        raise ValueError(f"Invalid source frame rate: {source_fps}")
    audio_frames = math.ceil(audio_seconds * TARGET_FPS)
    # The last 77-frame Student clip also reads frames beyond the audible end.
    # Keep that context moving forward when the source is long enough.
    max_frames = math.ceil(audio_frames / STUDENT_STRIDE) * STUDENT_STRIDE
    if max_frames < 1:
        raise ValueError("Audio has no usable frames")

    output.parent.mkdir(parents=True, exist_ok=True)
    print(
        f"[timebase] source_fps={float(source_fps):.6f}, "
        f"audio_seconds={audio_seconds:.3f}, audio_25fps_frames={audio_frames}, "
        f"max_25fps_frames={max_frames}",
        flush=True,
    )
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(video), "-map", "0:v:0", "-an",
            "-vf", f"setpts=PTS-STARTPTS,fps={TARGET_FPS}",
            "-frames:v", str(max_frames), "-fps_mode", "passthrough",
            "-c:v", "ffv1", "-level", "3", "-pix_fmt", "yuv420p",
            str(output),
        ],
        check=True,
    )
    result_info = probe(output, "v")
    result_fps = Fraction(result_info["stream"].get("avg_frame_rate", "0"))
    if result_fps != TARGET_FPS:
        raise RuntimeError(f"Prepared video has unexpected fps: {result_fps}")
    print(
        f"[timebase] prepared={output}, fps={result_fps}, "
        f"duration={duration_seconds(result_info):.3f}s, "
        f"size_mib={output.stat().st_size / 1048576:.1f}",
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.video, args.audio, args.output)


if __name__ == "__main__":
    main()
