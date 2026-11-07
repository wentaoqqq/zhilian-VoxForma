"""Real FFmpeg acceptance of the product workflow, without speech/model imports."""
import asyncio
import io
import json
from pathlib import Path
import shutil
import subprocess
import time
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
import pytest

from api.dependencies import require_consumer
from api.routers import production as api
from core import db, job_store
from core.job_queue import JobQueue
from production import store
from production.models import ProductPlan
from production.render import ass_text, write_ass


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "studio.db"))
    monkeypatch.setattr(store, "ROOT", tmp_path / "media")
    monkeypatch.setattr(api, "queue", JobQueue("test-production"))
    db.ensure_schema()
    app = FastAPI()
    app.dependency_overrides[require_consumer] = lambda: None
    app.include_router(api.router)
    with TestClient(app) as client:
        yield client


def ffmpeg(*args):
    tool = shutil.which("ffmpeg")
    if not tool or not shutil.which("ffprobe"):
        pytest.skip("Real-media acceptance requires FFmpeg and FFprobe")
    return subprocess.run([tool, "-nostdin", "-v", "error", "-y", *map(str, args)], capture_output=True, check=True).stdout


def upload(client, path):
    response = client.post("/production/assets", params={"filename": path.name}, content=path.read_bytes())
    assert response.status_code == 200, response.text
    return response.json()


def plan(source, outro, **kwargs):
    return {"source_id": source["id"], "outro_id": outro["id"], "title": "Real product", "cover_title": "Complete product", "publication_title": "A product demonstration", **kwargs}


def wait(client, run_id):
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        result = client.get("/production/runs/" + run_id).json()
        if result["status"] not in {"pending", "running"}:
            return result
        time.sleep(0.1)
    pytest.fail("Production did not finish within 150 seconds")


def test_product_delivery_real_video(client, tmp_path):
    source, tail = tmp_path / "source.mp4", tmp_path / "tail.mp4"
    ffmpeg("-f", "lavfi", "-i", "testsrc2=s=640x360:r=60:d=1.2", "-f", "lavfi", "-i", "sine=frequency=440:duration=1.2", "-c:v", "libx264", "-c:a", "aac", "-shortest", source)
    ffmpeg("-f", "lavfi", "-i", "color=c=blue:s=360x640:r=25:d=0.8", "-c:v", "libx264", tail)
    a, b = upload(client, source), upload(client, tail)
    # A repeated upload must not replace a file that is already being previewed.
    with store.asset_path(a["id"]).open("rb"):
        assert upload(client, source)["id"] == a["id"]
    assert len(client.get("/production/assets").json()) == 2
    preview = client.get(f"/production/assets/{a['id']}/preview", headers={"Range": "bytes=0-99"})
    assert preview.status_code == 206 and len(preview.content) == 100
    # Preserve real user timings; do not distribute by character count.
    srt = client.post("/production/subtitles/parse", json={"text": "1\n00:00:00,100 --> 00:00:00,700\nTimed caption\n"}).json()
    response = client.post("/production/runs", json=plan(a, b, subtitle_mode="provided", captions=srt))
    assert response.status_code == 200, response.text
    run = wait(client, response.json()["id"])
    assert run["status"] == "done", run
    assert set(run["artifacts"]) == {"video", "cover", "publication", "cover_title"}
    assert set(run["steps"].values()) == {"done"}
    work = store.ROOT / "runs" / run["id"]
    info = json.loads(subprocess.check_output([shutil.which("ffprobe"), "-v", "error", "-show_streams", "-of", "json", str(work / "video.mp4")]))
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"], video["avg_frame_rate"]) == (1080, 1920, "30/1")
    assert abs(float(video["duration"]) - 2) < 0.12
    assert Image.open(work / "cover.jpg").size == (1080, 1440)
    assert "0:00:00.10,0:00:00.70,Subtitle" in (work / "product.ass").read_text(encoding="utf-8")
    # The unvoiced outro remains a clean blue frame without product packaging.
    image = Image.open(io.BytesIO(ffmpeg("-ss", "1.8", "-i", work / "video.mp4", "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-")))
    r, g, blue = image.resize((1, 1)).getpixel((0, 0))
    assert blue > 240 and r < 10 and g < 10
    # Landscape source is padded rather than stretched to portrait.
    image = Image.open(io.BytesIO(ffmpeg("-ss", "0.8", "-i", work / "video.mp4", "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-")))
    assert max(image.getpixel((5, 500))) < 12
    delivery = client.get(f"/production/runs/{run['id']}/download")
    assert delivery.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(delivery.content))
    assert len(archive.namelist()) == 4
    assert sorted(Path(n).suffix for n in archive.namelist()) == [".jpg", ".md", ".mp4", ".txt"]
    assert len(client.get("/production/runs").json()) == 1


def test_silent_short_video_and_embedded_subtitles(client, tmp_path):
    source, tail = tmp_path / "silent.mp4", tmp_path / "tail.mp4"
    ffmpeg("-f", "lavfi", "-i", "color=c=red:s=240x240:r=30:d=0.4", "-c:v", "libx264", source)
    tail.write_bytes(source.read_bytes())
    srt = tmp_path / "caption.srt"
    srt.write_text("1\n00:00:00,050 --> 00:00:00,300\nEmbedded subtitle\n", encoding="utf-8")
    soft = tmp_path / "soft.mp4"
    ffmpeg("-i", source, "-i", srt, "-map", "0:v", "-map", "1:s", "-c:v", "copy", "-c:s", "mov_text", soft)
    a, b = upload(client, soft), upload(client, tail)
    response = client.post("/production/runs", json=plan(a, b, brand_text="Should not appear after the short title"))
    run = wait(client, response.json()["id"])
    assert run["status"] == "done", run
    ass = (store.ROOT / "runs" / run["id"] / "product.ass").read_text(encoding="utf-8")
    assert "Embedded subtitle" in ass and ",Brand,," not in ass


def test_preflight_and_subtitle_validation(client, tmp_path):
    bad = client.post("/production/assets?filename=broken.mp4", content=b"invalid")
    assert bad.status_code == 400
    assert not list((store.ROOT / "incoming").iterdir())
    assert client.post("/production/subtitles/parse", json={"text": "no timestamps"}).status_code == 400
    overlap = client.post("/production/subtitles/parse", json={"text": "1\n00:00:00,000 --> 00:00:01,000\nFirst\n\n2\n00:00:00,700 --> 00:00:01,200\nSecond\n"})
    assert overlap.status_code == 200 and overlap.json()[1]["start"] == 0.7
    assert client.post("/production/runs", json=plan({"id": "missing"}, {"id": "missing"})).status_code == 404
    with pytest.raises(ValueError):
        ProductPlan(**plan({"id": "x"}, {"id": "y"}, captions=[{"start": 1, "end": 0.5, "text": "bad"}]))
    with pytest.raises(ValueError):
        ProductPlan(**plan({"id": "x"}, {"id": "y"}, cover_time=float("nan")))
    with pytest.raises(ValueError):
        store.asset_path("../" + "a" * 61)


def test_cancel_queued_and_running_jobs(client, tmp_path, monkeypatch):
    source = tmp_path / "one.mp4"
    ffmpeg("-f", "lavfi", "-i", "color=c=red:s=64x64:d=0.2", "-c:v", "libx264", source)
    a = upload(client, source)
    async def blocked(plan, source, outro, work, job, step):
        await job.cancel_event.wait()
        raise asyncio.CancelledError()
    monkeypatch.setattr(api, "render_product", blocked)
    first = client.post("/production/runs", json=plan(a, a)).json()
    second = client.post("/production/runs", json=plan(a, a)).json()
    assert client.post(f"/production/runs/{second['id']}/cancel").json()["status"] == "cancelled"
    client.post(f"/production/runs/{first['id']}/cancel")
    assert wait(client, first["id"])["status"] == "cancelled"
    assert client.get(f"/production/runs/{first['id']}/files/video").status_code == 404
    async def next_task(*args):
        raise RuntimeError("The next task ran after cancellation")
    monkeypatch.setattr(api, "render_product", next_task)
    third = client.post("/production/runs", json=plan(a, a)).json()
    result = wait(client, third["id"])
    assert result["status"] == "failed" and "next task ran" in result["error"]


def test_restart_records_and_retry_keep_the_original_plan(client, tmp_path, monkeypatch):
    source = tmp_path / "one.mp4"
    ffmpeg("-f", "lavfi", "-i", "color=c=red:s=64x64:d=0.2", "-c:v", "libx264", source)
    a = upload(client, source)
    original = ProductPlan(**plan(a, a))
    job_store.create("interrupted", type="production")
    with db.db_conn() as conn:
        conn.execute("INSERT INTO production_runs(id,plan_json) VALUES (?,?)", ("interrupted", original.model_dump_json()))
    job_store.sweep_orphans_on_startup()
    assert client.get("/production/runs/interrupted").json()["status"] == "failed"
    async def fail(*args):
        raise RuntimeError("A test rendering failure")
    monkeypatch.setattr(api, "render_product", fail)
    retry = client.post("/production/runs/interrupted/retry").json()
    assert retry["id"] != "interrupted" and retry["plan"] == original.model_dump()
    assert wait(client, retry["id"])["status"] == "failed"


def test_ass_text_is_literal(tmp_path):
    path = tmp_path / "title.ass"
    write_ass(path, [(0, 1, "Title", r"{\pos(0,0)}customer")])
    assert r"{\pos(0,0)}" not in path.read_text(encoding="utf-8")
    assert ass_text("Rotating round table", 12) == r"Rotating\Nround table"


def test_migration_preserves_existing_data(tmp_path):
    import importlib
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    import sqlalchemy as sa
    migration = importlib.import_module("migrations.versions.0018_media_production")
    engine = sa.create_engine("sqlite:///" + str(tmp_path / "upgrade.db"))
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE voice_profiles(id TEXT PRIMARY KEY, name TEXT)"))
        conn.execute(sa.text("INSERT INTO voice_profiles VALUES ('one','Existing voice')"))
        conn.execute(sa.text("CREATE TABLE jobs(id TEXT PRIMARY KEY)"))
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
            migration.upgrade()  # base schema can already contain the new tables
        assert conn.execute(sa.text("SELECT name FROM voice_profiles")).scalar() == "Existing voice"
        assert {"media_assets", "production_runs"} <= set(sa.inspect(conn).get_table_names())


@pytest.fixture
def local_ai(monkeypatch):
    """Real SDK -> local HTTP responder; never contacts a paid model provider."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    from types import SimpleNamespace
    from openai import OpenAI
    from services.llm_backend import OpenAICompatBackend
    from services.llm_providers import Provider
    from production import planner

    state = {"answer": {"explanation": "Suggested product packaging", "changes": {"title": "Round table", "cover_title": "Product demonstration", "publication_title": "See the complete table"}, "missing_inputs": []}, "requests": []}
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            state["requests"].append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            body = json.dumps({"id": "test-completion", "object": "chat.completion", "created": 1, "model": "local-test", "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(state["answer"])}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    backend = OpenAICompatBackend(provider=Provider("local-test", "Local test", "http://127.0.0.1", "test-model", local=True))
    sdk = OpenAI(api_key="local-test-only", base_url=f"http://127.0.0.1:{server.server_port}/v1", max_retries=0)
    backend._client = sdk
    monkeypatch.setattr(planner.llm_skills, "skill_backend", lambda _id: backend)
    monkeypatch.setattr(api.llm_skills, "resolve_skill", lambda _id: SimpleNamespace(ready=True))
    yield state
    sdk.close()
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def test_ai_plan_review_and_submit_real_http_and_render(client, tmp_path, local_ai):
    source = tmp_path / "table.mp4"
    ffmpeg("-f", "lavfi", "-i", "color=c=green:s=320x180:r=60:d=0.4", "-c:v", "libx264", source)
    a = upload(client, source)
    draft = {**plan(a, a), "plugin_id": "roundtable"}
    request = {"draft": draft, "instruction": "This product is an electric round table. Keep the existing video and describe the whole table.", "submit": False}
    preview = client.post("/production/ai/plan", json=request)
    assert preview.status_code == 200, preview.text
    assert preview.json()["ready"] and preview.json()["run"] is None
    assert client.get("/production/runs").json() == []
    assert preview.json()["plan"]["source_id"] == a["id"]
    assert preview.json()["plan"]["title"] == "Round table"
    provider_context = json.loads(local_ai["requests"][0]["messages"][1]["content"])
    assert provider_context["source"]["filename"] == "table.mp4"
    assert "path" not in provider_context["source"]
    assert "electric rotating dining tables" in local_ai["requests"][0]["messages"][0]["content"]
    request["submit"] = True
    submitted = client.post("/production/ai/plan", json=request)
    assert submitted.status_code == 200, submitted.text
    run = wait(client, submitted.json()["run"]["id"])
    assert run["status"] == "done", run
    assert run["plan"] == submitted.json()["plan"]
    assert client.get(f"/production/runs/{run['id']}/download").status_code == 200
    # Changing AI settings later must not change an already submitted plan.
    local_ai["answer"]["changes"]["title"] = "A later AI idea"
    assert client.get(f"/production/runs/{run['id']}").json()["plan"]["title"] == "Round table"


@pytest.mark.parametrize("changes,missing,code", [
    ({"source_id": "different"}, [], 422),
    ({"title": "Warranty lasts 99 years"}, [], 422),
    ({"cover_time": 900}, [], 200),
    ({}, ["Please describe the actual product."], 200),
])
def test_ai_invalid_or_incomplete_plans_never_enqueue(client, tmp_path, local_ai, changes, missing, code):
    source = tmp_path / "table.mp4"
    ffmpeg("-f", "lavfi", "-i", "color=c=red:s=64x64:d=0.2", "-c:v", "libx264", source)
    a = upload(client, source)
    local_ai["answer"] = {"explanation": "Review the requested changes", "changes": changes, "missing_inputs": missing}
    response = client.post("/production/ai/plan", json={"draft": plan(a, a), "instruction": "Use the supplied material", "submit": True})
    assert response.status_code == code, response.text
    if code == 200:
        assert not response.json()["ready"] and response.json()["run"] is None and response.json()["issues"]
    assert client.get("/production/runs").json() == []


def test_ai_provider_off_is_explicit_and_opening_the_page_does_not_call_ai(client, monkeypatch):
    from services import llm_skills
    monkeypatch.setattr(llm_skills, "describe", lambda _id: {"ready": False, "reason": "disabled"})
    response = client.get("/production/ai/status")
    assert response.json() == {"ready": False, "reason": "disabled"}
    assert {p["id"] for p in client.get("/production/plugins").json()} == {"general", "roundtable"}
