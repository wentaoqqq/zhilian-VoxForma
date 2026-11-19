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
from services import comfy_image, comfy_image_install as install


def test_image_jobs_preserve_reference_workflow_and_deliver_png(tmp_path, monkeypatch):
    queue = JobQueue(name="test-image")
    monkeypatch.setattr(image_generation, "queue", queue)
    monkeypatch.setattr(install, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(install, "installed", lambda: True)

    submitted = []

    async def generate(job_id, directory, graph, set_stage):
        assert directory.name == job_id
        reference = graph.get("470", {}).get("inputs", {}).get("image")
        if reference:
            assert reference == "reference.png"
            assert (directory / reference).read_bytes() == png.getvalue()
        submitted.append(graph)
        set_stage("generating")
        Image.new("RGB", (32, 32)).save(directory / "result.png")

    monkeypatch.setattr(comfy_image, "generate", generate)

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

    text, reference = submitted
    assert (text["456"]["inputs"]["width"], text["456"]["inputs"]["height"]) == (2048, 1152)
    assert text["458"]["inputs"]["steps"] == 40
    assert reference["452"]["inputs"]["images.image_1"] == ["470", 0]
    assert reference["458"]["inputs"]["latent_image"] == ["452", 2]


def test_install_reuses_verified_weights_and_activates_only_when_complete(tmp_path, monkeypatch):
    root = tmp_path / "managed"
    models = tmp_path / "existing"
    monkeypatch.setenv("VOXFORMA_COMFY_MODELS_DIR", str(models))
    monkeypatch.setattr(install, "engine_dir", lambda: root)
    monkeypatch.setattr(install, "supported", lambda: True)
    monkeypatch.setattr(install, "_job", None)
    monkeypatch.setattr(install, "_thread", None)
    name = "vae/model.safetensors"
    existing = models / name
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"fixture model")
    monkeypatch.setattr(install, "WEIGHTS", {name: (existing.stat().st_size, install._sha256(existing))})

    def runtime(path):
        assert not install.installed()
        python = path / ".venv/bin/python"
        python.parent.mkdir(parents=True)
        python.touch()
        entry = path / "ComfyUI/main.py"
        entry.parent.mkdir()
        entry.touch()

    monkeypatch.setattr(install, "_install_source", lambda path: None)
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



def test_comfy_child_is_reaped_before_gpu_release(tmp_path, monkeypatch):
    import asyncio
    import json
    from pathlib import Path

    import httpx

    events = []

    class Scheduler:
        def rpc(self, action, **kwargs):
            assert action == "clear_voice"
            events.append("clear-voice")

    class Child:
        killed = False

        def poll(self):
            return 0 if self.killed else None

        def kill(self):
            self.killed = True
            events.append("kill-child")

        def wait(self, timeout):
            assert self.killed
            events.append("reap-child")

    async def acquire(owner):
        assert owner.startswith("voicestudio:image:")
        events.append("acquire-gpu")
        return Scheduler(), {"token": "test"}, None

    async def release(handle):
        events.append("release-gpu")

    def spawn(args, **kwargs):
        assert args[args.index("--listen") + 1] == "127.0.0.1"
        base = Path(args[args.index("--base-directory") + 1])
        Image.new("RGB", (32, 32)).save(base / "output/result_00001_.png")
        return Child()

    def respond(request):
        if request.url.path == "/object_info/TextEncodeQwenImage21":
            return httpx.Response(200, json={"TextEncodeQwenImage21": {"name": "encoder"}})
        if request.url.path == "/prompt":
            assert json.loads(request.content)["prompt"]["458"]["inputs"]["steps"] == 40
            return httpx.Response(200, json={"prompt_id": "image-1"})
        assert request.url.path == "/history/image-1"
        return httpx.Response(200, json={
            "image-1": {"outputs": {"900": {"images": [{"subfolder": "", "filename": "result_00001_.png"}]}}},
        })

    http_client = httpx.AsyncClient
    monkeypatch.setattr(comfy_image, "engine_dir", lambda: tmp_path)
    monkeypatch.setattr(comfy_image, "spawn_owned", spawn)
    monkeypatch.setattr(comfy_image.avatar_gpu, "acquire", acquire)
    monkeypatch.setattr(comfy_image.avatar_gpu, "release", release)
    monkeypatch.setattr(comfy_image.httpx, "AsyncClient",
                        lambda **kwargs: http_client(transport=httpx.MockTransport(respond), **kwargs))
    graph = comfy_image.workflow("An architectural model", "1024x1024", None)
    asyncio.run(comfy_image.generate("job", tmp_path, graph, events.append))

    assert events == ["acquire-gpu", "starting", "clear-voice", "generating", "kill-child", "reap-child", "release-gpu"]
    assert (tmp_path / "result.png").is_file()
    assert not (tmp_path / "runtime").exists()
