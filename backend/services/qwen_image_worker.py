"""Isolated Diffusers worker for one Qwen Image 2.1 request."""
from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path


def run(models: Path, request: Path) -> None:
    import torch
    from diffusers import QwenImage21Pipeline
    from diffusers.hooks import apply_group_offloading
    from PIL import Image, ImageOps

    if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported(including_emulation=False):
        raise RuntimeError("The Qwen Image BF16 worker requires a CUDA GPU with native BF16 support.")

    started = time.monotonic()
    directory = request.parent
    task = json.loads(request.read_text(encoding="utf-8"))
    cuda = torch.device("cuda")
    torch.cuda.reset_peak_memory_stats()

    kwargs = {
        "prompt": task["prompt"],
        "num_inference_steps": 40,
        "true_cfg_scale": 1.0,
        "generator": torch.Generator(device="cpu").manual_seed(task["seed"]),
    }
    if task["reference"]:
        with Image.open(directory / task["reference"]) as reference:
            kwargs["image"] = ImageOps.exif_transpose(reference).convert("RGBA")
        width, height = kwargs["image"].size
        tiled_vae = max(width / height, height / width) > 2.25
        kwargs["output_resolution"] = 1024
    else:
        width, height = task["dimensions"]
        kwargs.update(width=width, height=height)
        tiled_vae = max(width, height) > 1536

    pipe = QwenImage21Pipeline.from_pretrained(
        str(models), torch_dtype=torch.bfloat16, local_files_only=True, use_safetensors=True,
    )
    # A BF16 text encoder cannot be staged on a 16 GiB card as a single unit.
    apply_group_offloading(pipe.text_encoder, onload_device=cuda, offload_type="leaf_level")
    pipe.transformer.enable_group_offload(
        onload_device=cuda, offload_type="block_level", num_blocks_per_group=4,
    )
    pipe.vae.to(cuda)
    if tiled_vae:
        pipe.vae.enable_tiling()

    loaded = time.monotonic()
    (directory / ".running").touch()
    image = pipe(**kwargs).images[0]
    staging = directory / "result.partial.png"
    image.save(staging, format="PNG")
    staging.replace(directory / "result.png")
    print(
        f"runtime_setup_seconds={loaded - started:.2f} "
        f"generation_seconds={time.monotonic() - loaded:.2f} "
        f"peak_vram_allocated_mib={torch.cuda.max_memory_allocated() / 1024**2:.0f} "
        f"peak_vram_reserved_mib={torch.cuda.max_memory_reserved() / 1024**2:.0f}",
        flush=True,
    )


def main() -> int:
    models, request = map(Path, sys.argv[1:])
    try:
        run(models, request)
    except Exception as exc:
        traceback.print_exc()
        message = str(exc)[:800] or type(exc).__name__
        (request.parent / "error.json").write_text(json.dumps({"message": message}), encoding="utf-8")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
