#!/usr/bin/env python3
"""Convert a finished 25 fps TBDub video to 30 or 60 fps with RIFE v4.25.

The lip-sync model and its 50 Hz HuBERT features stay on their trained 25 fps
timeline. This separate process runs only after Student exits and releases VRAM.
Frames are decoded and encoded incrementally; no complete video is held in RAM.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
from fractions import Fraction
from pathlib import Path

import cv2
import numpy as np
import torch
import torch.nn.functional as F

from rife_v425.IFNet_HDv3_v4_25 import IFNet


SOURCE_FPS = 25
DEFAULT_WEIGHTS = Path(__file__).resolve().parents[1] / "checkpoints/rife-v4.25/flownet.pkl"


def probe(path: Path) -> dict:
    raw = subprocess.check_output(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height,avg_frame_rate,nb_frames,duration",
            "-of", "json", str(path),
        ],
        text=True,
    )
    streams = json.loads(raw).get("streams", [])
    if not streams:
        raise ValueError(f"No video stream: {path}")
    return streams[0]


def load_rife(weights: Path) -> IFNet:
    if not torch.cuda.is_available():
        raise RuntimeError("RIFE needs CUDA; Student has already exited before this stage.")
    if not weights.is_file():
        raise FileNotFoundError(f"RIFE weights missing: {weights}")
    raw = torch.load(weights, map_location="cpu", weights_only=True)
    state = {
        key.removeprefix("module."): value
        for key, value in raw.items()
        if not key.removeprefix("module.").startswith(("teacher.", "caltime."))
    }
    model = IFNet(scale=1, ensemble=False)
    missing, unexpected = model.load_state_dict(state, strict=False)
    if missing:
        raise RuntimeError(f"RIFE checkpoint does not match v4.25: missing {missing[:8]}")
    if unexpected:
        print(f"[RIFE] ignored checkpoint keys: {unexpected[:8]}", flush=True)
    model.eval().to("cuda", dtype=torch.float32)
    torch.backends.cudnn.benchmark = True
    return model


def to_tensor(frame_bgr: np.ndarray, pad: tuple[int, int, int, int]) -> torch.Tensor:
    frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    tensor = torch.from_numpy(frame_rgb.transpose(2, 0, 1).copy()).unsqueeze(0)
    return F.pad(tensor.to(device="cuda", dtype=torch.float32).div_(255.0), pad)


@torch.inference_mode()
def convert(source: Path, destination: Path, weights: Path, target_fps: int) -> None:
    if target_fps not in (30, 60):
        raise ValueError("RIFE target fps must be 30 or 60")
    info = probe(source)
    fps = Fraction(info["avg_frame_rate"])
    if fps != SOURCE_FPS:
        raise ValueError(f"Expected a native {SOURCE_FPS} fps Student output, got {fps}")
    width, height = int(info["width"]), int(info["height"])
    source_frames = int(info["nb_frames"])
    target_frames = (source_frames * target_fps + SOURCE_FPS // 2) // SOURCE_FPS
    if source_frames < 1 or target_frames < 1:
        raise ValueError("Empty source video")

    model = load_rife(weights)
    padded_width = math.ceil(width / 64) * 64
    padded_height = math.ceil(height / 64) * 64
    padding = (0, padded_width - width, 0, padded_height - height)
    flow_div = torch.tensor(
        [(padded_width - 1) / 2, (padded_height - 1) / 2],
        dtype=torch.float32, device="cuda",
    )
    horizontal = torch.linspace(-1, 1, padded_width, device="cuda")
    vertical = torch.linspace(-1, 1, padded_height, device="cuda")
    grid = torch.cat(
        [
            horizontal.view(1, 1, 1, padded_width).expand(1, 1, padded_height, -1),
            vertical.view(1, 1, padded_height, 1).expand(1, 1, -1, padded_width),
        ],
        dim=1,
    )
    timestep = torch.empty((1, 1, padded_height, padded_width), device="cuda")

    cap = cv2.VideoCapture(str(source))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot decode {source}")
    ok, current = cap.read()
    if not ok:
        raise RuntimeError(f"No readable frames in {source}")
    ok, following = cap.read()
    if not ok:
        following = current
    current_tensor = to_tensor(current, padding)
    following_tensor = to_tensor(following, padding)
    current_feature = model.encode(current_tensor)
    following_feature = model.encode(following_tensor)

    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(destination.stem + f".{target_fps}fps.partial.mp4")
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s:v", f"{width}x{height}",
        "-r", str(target_fps), "-i", "pipe:0", "-i", str(source),
        "-map", "0:v:0", "-map", "1:a:0?", "-c:v", "libx264",
        "-crf", "10", "-preset", "medium", "-pix_fmt", "yuv420p",
        "-x264-params", "colorprim=bt709:transfer=bt709:colormatrix=bt709",
        "-c:a", "copy", "-movflags", "+faststart", str(temp),
    ]
    encoder = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    current_index = 0
    try:
        for output_index in range(target_frames):
            position = output_index * SOURCE_FPS
            base = position // target_fps
            remainder = position % target_fps
            while current_index < base:
                current, current_tensor, current_feature = (
                    following, following_tensor, following_feature
                )
                current_index += 1
                if current_index < source_frames - 1:
                    ok, following = cap.read()
                    if not ok:
                        raise RuntimeError(f"Source ended at frame {current_index + 1}/{source_frames}")
                    following_tensor = to_tensor(following, padding)
                    following_feature = model.encode(following_tensor)
                else:
                    following, following_tensor, following_feature = (
                        current, current_tensor, current_feature
                    )

            if remainder == 0 or current_index >= source_frames - 1:
                rgb = cv2.cvtColor(current, cv2.COLOR_BGR2RGB)
            else:
                timestep.fill_(remainder / target_fps)
                interpolated = model(
                    current_tensor, following_tensor, timestep, flow_div, grid,
                    current_feature, following_feature,
                )
                rgb = (
                    interpolated[:, :, :height, :width].clamp_(0, 1)
                    .mul_(255).byte().squeeze(0).permute(1, 2, 0).cpu().numpy()
                )
            encoder.stdin.write(np.ascontiguousarray(rgb).tobytes())
            if output_index and output_index % (target_fps * 10) == 0:
                print(f"[{target_fps}fps] {output_index}/{target_frames} frames", flush=True)

        encoder.stdin.close()
        error = encoder.stderr.read().decode(errors="replace")
        if encoder.wait() != 0:
            raise RuntimeError(f"{target_fps} fps encoder failed: {error[-2000:]}")
        out_info = probe(temp)
        if Fraction(out_info["avg_frame_rate"]) != target_fps:
            raise RuntimeError(f"Unexpected output frame rate: {out_info['avg_frame_rate']}")
        if int(out_info["nb_frames"]) != target_frames:
            raise RuntimeError(f"Unexpected output frame count: {out_info['nb_frames']} != {target_frames}")
        os.replace(temp, destination)
        print(
            f"[{target_fps}fps] saved {destination}: {source_frames} -> {target_frames} frames, "
            f"duration {target_frames / target_fps:.3f}s",
            flush=True,
        )
    except Exception:
        if encoder.poll() is None:
            encoder.terminate()
            encoder.wait()
        temp.unlink(missing_ok=True)
        raise
    finally:
        cap.release()
        if encoder.stderr:
            encoder.stderr.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fps", type=int, choices=(30, 60), default=30)
    parser.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS)
    args = parser.parse_args()
    convert(args.input.resolve(), args.output.resolve(), args.weights.resolve(), args.fps)


if __name__ == "__main__":
    main()
