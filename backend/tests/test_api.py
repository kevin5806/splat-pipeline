"""Prova l'intera pipeline in modalità simulata, attraverso l'API HTTP."""
import os
import time

import pytest
from fastapi.testclient import TestClient

from app import store
from app.main import app


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def _wait(client, name, states, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        p = client.get(f"/api/projects/{name}").json()
        if p["status"] in states:
            return p
        time.sleep(0.1)
    raise AssertionError(f"timeout: stato {p['status']}, fase {p['current_stage']}")


def _new(client, name, **settings):
    r = client.post("/api/projects", json={"name": name, "settings": settings or None})
    assert r.status_code == 201, r.text
    r = client.post(f"/api/projects/{name}/video", files={"file": ("giro.mp4", b"\x00" * 2048, "video/mp4")})
    assert r.status_code == 200, r.text


def test_full_run(client):
    _new(client, "ok1")
    assert client.post("/api/projects/ok1/start").status_code == 200
    p = _wait(client, "ok1", {"done", "failed", "blocked"})
    assert p["status"] == "done", p
    assert all(s["status"] == "done" for s in p["stages"])
    assert next(s for s in p["stages"] if s["id"] == "test")["metrics"]["psnr"] == 20.9
    assert any(a["path"] == "splat_clean.ply" for a in p["artifacts"])
    assert client.get("/api/projects/ok1/files/splat_clean.ply").status_code == 200
    assert client.get("/api/projects/ok1/log/probe").status_code == 200


def test_gate_blocks_then_user_can_continue(client):
    os.environ["SPLAT_FAKE_GATE_FAIL"] = "1"
    try:
        _new(client, "gate1")
        client.post("/api/projects/gate1/start")
        p = _wait(client, "gate1", {"blocked", "done", "failed"})
        assert p["status"] == "blocked"
        assert [s["status"] for s in p["stages"] if s["id"] == "test"] == ["blocked"]
        assert [s["status"] for s in p["stages"] if s["id"] == "train"] == ["pending"]   # il training NON è partito
    finally:
        os.environ.pop("SPLAT_FAKE_GATE_FAIL")
    assert client.post("/api/projects/gate1/continue").status_code == 200
    p = _wait(client, "gate1", {"done", "failed", "blocked"})
    assert p["status"] == "done"
    test_stage = next(s for s in p["stages"] if s["id"] == "test")
    assert test_stage["metrics"]["forced"] is True


def test_stop_then_resume_skips_finished_stages(client, monkeypatch):
    monkeypatch.setattr("app.config.FAKE_SPEED", 0.5)       # più lento, per poter fermare a metà
    monkeypatch.setattr("app.fake.config.FAKE_SPEED", 0.5)
    _new(client, "stop1")
    client.post("/api/projects/stop1/start")
    end = time.time() + 20
    while time.time() < end:
        p = client.get("/api/projects/stop1").json()
        if p["stages"][1]["status"] == "done":
            break
        time.sleep(0.05)
    client.post("/api/projects/stop1/stop")
    p = client.get("/api/projects/stop1").json()
    assert p["status"] == "stopped" and not p["active"]
    done_before = [s["id"] for s in p["stages"] if s["status"] == "done"]
    assert "probe" in done_before
    monkeypatch.setattr("app.fake.config.FAKE_SPEED", 200)
    client.post("/api/projects/stop1/start")
    p = _wait(client, "stop1", {"done", "failed", "blocked"})
    assert p["status"] == "done"


def test_recover_marks_interrupted_and_auto_resumes(client, monkeypatch):
    _new(client, "rec1")
    p = store.load("rec1")
    p.status = "running"
    p.stages[0].status = "done"
    p.stages[1].status = "running"
    store.save(p)
    monkeypatch.setattr("app.config.AUTO_RESUME", False)
    import asyncio
    from app.main import manager
    asyncio.run(manager.recover())
    p = client.get("/api/projects/rec1").json()
    assert p["status"] == "interrupted" and p["stages"][1]["status"] == "interrupted"


def test_validation_and_errors(client):
    assert client.post("/api/projects", json={"name": "../evil"}).status_code == 422
    assert client.get("/api/projects/..%2Fevil").status_code == 404
    client.post("/api/projects", json={"name": "dup1"})
    assert client.post("/api/projects", json={"name": "dup1"}).status_code == 409
    assert client.post("/api/projects/dup1/start").status_code == 400          # niente video
    bad = client.post("/api/projects/dup1/video", files={"file": ("x.exe", b"MZ", "application/octet-stream")})
    assert bad.status_code == 415
    assert client.get("/api/projects/dup1/files/../state.json").status_code in (404, 400)
    assert client.get("/api/projects/dup1/files/%2e%2e/state.json").status_code == 404
    assert client.post("/api/projects/dup1/reset/inesistente").status_code == 400


def test_token_guard(client, monkeypatch):
    monkeypatch.setattr("app.config.PANEL_TOKEN", "segreto")
    assert client.get("/api/projects").status_code == 401
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/projects", headers={"X-Token": "segreto"}).status_code == 200


def test_viewer_folder_is_served(client):
    from pathlib import Path
    from app import config
    root = Path(config.STATIC_DIR)
    (root / "viewer" / "lib").mkdir(parents=True, exist_ok=True)
    (root / "viewer" / "index.html").write_text("<html>viewer</html>", encoding="utf-8")
    (root / "viewer" / "lib" / "a.js").write_text("export const x = 1;", encoding="utf-8")
    (root / "index.html").write_text("<html>pannello</html>", encoding="utf-8")
    r = client.get("/viewer/")
    assert r.status_code == 200 and "viewer" in r.text
    r = client.get("/viewer", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/viewer/"
    r = client.get("/viewer?src=%2Fapi%2Fx&name=a", follow_redirects=False)
    assert r.headers["location"] == "/viewer/?src=%2Fapi%2Fx&name=a"
    assert client.get("/viewer/lib/a.js").status_code == 200
    assert client.get("/").headers["cache-control"] == "no-cache"                # la pagina si ricontrolla sempre
    assert client.get("/p/qualunque").headers["cache-control"] == "no-cache"
    (root / "_app" / "immutable").mkdir(parents=True, exist_ok=True)
    (root / "_app" / "immutable" / "x.abc.js").write_text("1", encoding="utf-8")
    assert "immutable" in client.get("/_app/immutable/x.abc.js").headers["cache-control"]
    assert "pannello" in client.get("/p/qualunque").text                 # le rotte del pannello restano
    assert client.get("/viewer/../index.html").status_code in (200, 404)  # niente uscite dalla cartella statica


def test_system_endpoint(client):
    j = client.get("/api/system").json()
    assert j["fake"] is True and len(j["stages"]) == 11
