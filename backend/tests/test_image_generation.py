import hashlib
import io
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from api.dependencies import require_admin, require_consumer
from api.routers import image_generation
from core.job_queue import JobQueue
from services import qwen_image, qwen_image_install as install


def test_image_jobs_pass_input_to_worker_and_deliver_png(tmp_path, monkeypatch):
    queue = JobQueue(name="test-image")
    monkeypatch.setattr(image_generation, "queue", queue)
    monkeypatch.setattr(install, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(install, "installed", lambda: True)

    submitted = []

    async def generate(job_id, directory, *, prompt, size, reference, set_stage):
        assert directory.name == job_id
        if reference:
            assert reference == "reference.png"
            assert (directory / reference).read_bytes() == png.getvalue()
        submitted.append((prompt, size, reference))
        set_stage("generating")
        Image.new("RGB", (32, 32)).save(directory / "result.png")

    monkeypatch.setattr(qwen_image, "generate", generate)

    @asynccontextmanager
    async def lifespan(app):
        yield
        await queue.stop()

    app = FastAPI(lifespan=lifespan)
    app.include_router(image_generation.router)
    app.dependency_overrides[require_consumer] = lambda: None
    app.dependency_overrides[require_admin] = lambda: None
    png = io.BytesIO()
    Image.new("RGB", (32, 32)).save(png, "PNG")

    with TestClient(app) as client:
        data = {"prompt": "An architectural model", "model": install.MODEL_ID, "size": "2048x1152"}
        first = client.post("/image-generation/jobs", data=data)
        assert first.status_code == 202, first.text
        second = client.post(
            "/image-generation/jobs",
            data={**data, "size": "reference"},
            files={"reference": ("model.png", png.getvalue(), "image/png")},
        )
        assert second.status_code == 202, second.text
        rejected = client.post(
            "/image-generation/jobs", data={**data, "size": "reference"},
            files={"reference": ("broken.png", b"not an image", "image/png")},
        )
        assert rejected.status_code == 422
        for _ in range(100):
            jobs = client.get("/image-generation/jobs").json()["jobs"]
            if all(job["state"] == "done" for job in jobs):
                break
            time.sleep(0.01)
        assert len(jobs) == 2 and all(job["state"] == "done" for job in jobs)
        response = client.get(f"/image-generation/jobs/{second.json()['id']}/image")
        assert response.status_code == 200 and response.headers["content-type"] == "image/png"
        assert Image.open(io.BytesIO(response.content)).size == (32, 32)
        assert not (tmp_path / "jobs" / second.json()["id"] / "reference.png").exists()

    assert submitted == [
        ("An architectural model", "2048x1152", None),
        ("An architectural model", "reference", "reference.png"),
    ]


def test_install_reuses_verified_weights_and_activates_only_when_complete(tmp_path, monkeypatch):
    root = tmp_path / "managed"
    models = tmp_path / "existing"
    monkeypatch.setenv("VOXFORMA_QWEN_MODELS_DIR", str(models))
    monkeypatch.setattr(install, "engine_dir", lambda: root)
    monkeypatch.setattr(install, "supported", lambda: True)
    monkeypatch.setattr(install, "_job", None)
    monkeypatch.setattr(install, "_thread", None)
    name = "vae/model.safetensors"
    existing = models / name
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"fixture model")
    monkeypatch.setattr(install, "WEIGHTS", {name: (existing.stat().st_size, hashlib.sha256(existing.read_bytes()).hexdigest())})

    def runtime(path):
        assert not install.installed()
        python = path / ".venv/bin/python"
        python.parent.mkdir(parents=True)
        python.touch()

    monkeypatch.setattr(install, "_runtime", runtime)

    if os.name == "nt":
        # The opt-in installer is Linux-only. Emulate its exclusive lock in this
        # Windows unit test to exercise verification, reuse and activation.
        from types import SimpleNamespace
        import sys

        monkeypatch.setitem(sys.modules, "fcntl", SimpleNamespace(
            LOCK_EX=2, LOCK_NB=4, flock=lambda *args: None,
        ))
    assert install.start_install()["state"] == "running"
    install._thread.join(5)
    assert install.status(with_job=True)["job"]["state"] == "done"
    assert install.installed()
    assert existing.read_bytes() == (root / "models" / name).read_bytes()


def test_diffusers_child_exits_before_gpu_release(tmp_path, monkeypatch):
    import asyncio
    import json
    from pathlib import Path

    events = []

    class Scheduler:
        def rpc(self, action, **kwargs):
            assert action == "clear_voice"
            events.append("clear-voice")

    class Child:
        returncode = 0
        polls = 0

        def poll(self):
            self.polls += 1
            return None if self.polls == 1 else 0

        def wait(self, timeout):
            assert self.polls >= 2
            events.append("reap-child")

    async def acquire(owner):
        assert owner.startswith("voicestudio:image:")
        events.append("acquire-gpu")
        return Scheduler(), {"token": "test"}, None

    async def release(handle):
        events.append("release-gpu")

    def spawn(args, **kwargs):
        assert args[2].endswith("qwen_image_worker.py")
        assert kwargs["env"]["HF_HUB_OFFLINE"] == "1"
        request = Path(args[-1])
        payload = json.loads(request.read_text(encoding="utf-8"))
        assert payload["dimensions"] == [1024, 1024]
        assert payload["reference"] is None
        (tmp_path / ".running").touch()
        Image.new("RGBA", (32, 32)).save(tmp_path / "result.png")
        return Child()

    monkeypatch.setattr(qwen_image, "engine_dir", lambda: tmp_path)
    monkeypatch.setattr(qwen_image, "spawn_owned", spawn)
    monkeypatch.setattr(qwen_image.avatar_gpu, "acquire", acquire)
    monkeypatch.setattr(qwen_image.avatar_gpu, "release", release)
    asyncio.run(qwen_image.generate(
        "job", tmp_path, prompt="An architectural model",
        size="1024x1024", reference=None, set_stage=events.append,
    ))

    assert events == ["acquire-gpu", "starting", "clear-voice", "generating", "reap-child", "release-gpu"]
    assert (tmp_path / "result.png").is_file()
    assert not (tmp_path / "request.json").exists()
    assert not (tmp_path / ".running").exists()


def test_worker_preserves_reference_alpha_and_passes_fixed_size_for_text(tmp_path, monkeypatch):
    import json
    import sys
    from types import SimpleNamespace

    from services import qwen_image_worker

    class Pipeline:
        text_encoder = object()
        transformer = SimpleNamespace(enable_group_offload=lambda **kwargs: options.update(transformer=kwargs))
        vae = SimpleNamespace(to=lambda device: None, enable_tiling=lambda: options.update(tiling=True))

        @classmethod
        def from_pretrained(cls, model, **kwargs):
            assert model == str(tmp_path / "models")
            assert kwargs["local_files_only"] and kwargs["use_safetensors"]
            return cls()

        def __call__(self, **kwargs):
            options.update(kwargs)
            return SimpleNamespace(images=[Image.new("RGBA", (32, 32), (0, 0, 0, 12))])

    options = {}
    fake_cuda = SimpleNamespace(
        is_available=lambda: True, is_bf16_supported=lambda **kwargs: True,
        reset_peak_memory_stats=lambda: None, max_memory_allocated=lambda: 0,
        max_memory_reserved=lambda: 0,
    )
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(
        bfloat16="bf16", device=str, cuda=fake_cuda,
        Generator=lambda **kwargs: SimpleNamespace(manual_seed=lambda seed: seed),
    ))
    monkeypatch.setitem(sys.modules, "diffusers", SimpleNamespace(QwenImage21Pipeline=Pipeline))
    monkeypatch.setitem(sys.modules, "diffusers.hooks", SimpleNamespace(
        apply_group_offloading=lambda component, **kwargs: options.update(encoder=kwargs),
    ))

    request = tmp_path / "request.json"
    task = {"prompt": "Change the color", "seed": 42, "reference": None, "dimensions": [2048, 1152]}
    request.write_text(json.dumps(task), encoding="utf-8")
    qwen_image_worker.run(tmp_path / "models", request)
    assert (options["width"], options["height"]) == (2048, 1152)
    assert options["tiling"] is True

    options.clear()
    Image.new("RGBA", (64, 48), (0, 0, 0, 20)).save(tmp_path / "reference.png")
    request.write_text(json.dumps({**task, "reference": "reference.png", "dimensions": None}), encoding="utf-8")
    qwen_image_worker.run(tmp_path / "models", request)
    assert options["image"].getpixel((0, 0))[3] == 20
    assert options["output_resolution"] == 1024 and "width" not in options
    assert options["num_inference_steps"] == 40
    assert options["encoder"]["offload_type"] == "leaf_level"
    assert options["transformer"]["offload_type"] == "block_level"
    with Image.open(tmp_path / "result.png") as result:
        assert result.getpixel((0, 0))[3] == 12
