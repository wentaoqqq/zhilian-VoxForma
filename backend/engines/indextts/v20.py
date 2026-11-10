"""An independently selectable IndexTTS 2.0 using an existing local install."""
from __future__ import annotations

import os
import asyncio
import math
from pathlib import Path

from engines.indextts import IndexTTS2Backend


def repo_dir() -> Path | None:
    value = os.environ.get("OMNIVOICE_INDEXTTS20_DIR", "").strip()
    return Path(value).expanduser() if value else None


def python_path(repo: Path) -> Path:
    return repo / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


class IndexTTS20Backend(IndexTTS2Backend):
    """Reuse the speech protocol and GPU lifecycle; isolate code and weights."""

    id = "indextts20"
    display_name = "IndexTTS 2.0 (emotion-controlled cloning)"

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        repo = repo_dir()
        if repo is None:
            return False, "Set OMNIVOICE_INDEXTTS20_DIR to your existing IndexTTS 2.0 install."
        required = [python_path(repo), repo / "indextts/infer_v2.py"]
        required += [repo / "checkpoints" / name for name in ("config.yaml", "gpt.pth", "s2mel.pth")]
        if not all(path.is_file() for path in required):
            return False, "IndexTTS 2.0 needs its own .venv, infer_v2.py and checkpoints."
        return True, "ok"

    @classmethod
    def configured_identity(cls) -> str | None:
        repo = repo_dir()
        return str(repo.resolve()) if repo else None

    @classmethod
    def venv_python(cls) -> Path:
        available, reason = cls.is_available()
        if not available:
            raise RuntimeError(reason)
        return python_path(repo_dir())

    @classmethod
    def sidecar_script(cls) -> Path:
        return Path(__file__).with_name("main_v20.py")

    @property
    def supported_languages(self) -> list[str]:
        return ["zh", "en"]

    def generate(self, text: str, **kw):
        # The installed 2.0 API sends unknown kwargs into transformers.generate;
        # unlike 2.5 it has no native duration/rate argument. Stretch the result
        # with the existing pitch-preserving audio helper instead.
        duration = kw.pop('duration', None)
        factor = kw.pop('duration_factor', None)
        speed = kw.pop('speed', 1.0)
        audio = super().generate(text, **kw)
        samples = int(audio.shape[-1])
        if duration is not None:
            target = float(duration) * self.sample_rate
        elif factor is not None:
            target = samples * float(factor)
        else:
            rate = float(speed) if speed is not None else 1.0
            target = samples / rate if math.isfinite(rate) and rate > 0 else samples
        if not math.isfinite(target) or target <= 0 or round(target) == samples:
            return audio
        from services.ffmpeg_utils import _pitch_preserving_stretch
        return asyncio.run(_pitch_preserving_stretch(audio, round(target), self.sample_rate))
