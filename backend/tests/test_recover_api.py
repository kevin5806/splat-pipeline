"""Il giro completo dal sito, in modalità simulata: blocco, conferma, richiesta di un video, video di supporto."""
import time

import pytest
from fastapi.testclient import TestClient

from app import config


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "FAKE", True)
    monkeypatch.setattr(config, "FAKE_SPEED", 60.0)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(config, "EMBEDDED_WORKER", True)
    monkeypatch.setattr(config, "WORKER_POLL", 0.05)
    monkeypatch.setenv("SPLAT_FAKE_WEAK", "1")
    from app.main import app
    with TestClient(app) as c:
        yield c


def wait(client, name, status, timeout=30.0):
    end = time.time() + timeout
    while time.time() < end:
        p = client.get(f"/api/projects/{name}").json()
        if p["status"] == status:
            return p
        assert p["status"] != "failed", p["error"]
        time.sleep(0.05)
    raise AssertionError(f"stato {status} non raggiunto: {p['status']} {p['current_stage']}")


def stage(p, sid):
    return next(s for s in p["stages"] if s["id"] == sid)


def test_confirm_then_new_video_then_done(client):
    client.post("/api/projects", json={"name": "weak"})
    client.post("/api/projects/weak/videos", files={"file": ("a.mp4", b"x" * 100, "video/mp4")})
    early = client.post("/api/projects/weak/support-videos", files={"file": ("s.mp4", b"x", "video/mp4")})
    assert early.status_code == 400                                     # senza camere ricostruite non ha senso
    client.post("/api/projects/weak/start")
    p = wait(client, "weak", "blocked")
    assert stage(p, "recover")["metrics"]["decision"] == "approve"

    assert client.post("/api/projects/weak/recover/approve").status_code == 200
    time.sleep(0.2)
    p = wait(client, "weak", "blocked")
    assert stage(p, "recover")["metrics"]["decision"] == "new_video"
    assert client.post("/api/projects/weak/recover/approve").status_code == 400

    r = client.post("/api/projects/weak/support-videos", files={"file": ("s.mp4", b"x" * 50, "video/mp4")}, data={"device": "1"})
    assert r.status_code == 200, r.text
    p = r.json()
    assert [v["support"] for v in p["videos"]] == [False, True]
    assert stage(p, "refine")["status"] == "done" and stage(p, "recover")["status"] == "pending"   # il lavoro fatto resta
    client.post("/api/projects/weak/start")
    p = wait(client, "weak", "done")
    assert p["estimate"]["stages"]["recover"]["basis"] == "ipotesi"


def test_continue_without_recovery(client):
    client.post("/api/projects", json={"name": "skip"})
    client.post("/api/projects/skip/videos", files={"file": ("a.mp4", b"x" * 100, "video/mp4")})
    client.post("/api/projects/skip/start")
    wait(client, "skip", "blocked")
    client.post("/api/projects/skip/continue")
    p = wait(client, "skip", "done")
    assert stage(p, "recover")["metrics"]["forced"] is True


def test_thumbnails_stay_inside_their_folder(client):
    client.post("/api/projects", json={"name": "th"})
    assert client.get("/api/projects/th/thumb/..%2F..%2Fstate.json").status_code == 404
    assert client.get("/api/projects/th/thumb/missing.jpg").status_code == 404
