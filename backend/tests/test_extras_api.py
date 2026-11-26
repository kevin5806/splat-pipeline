"""Le funzioni aggiuntive dalle API, con il lavoratore (simulato) che esegue pipeline, mesh e notifiche."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from app import config, jobs, notify, store
from app.pipeline import Manager
from app.store import ProjectPaths


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "FAKE", True)
    monkeypatch.setattr(config, "FAKE_SPEED", 80.0)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(config, "EMBEDDED_WORKER", True)
    monkeypatch.setattr(config, "WORKER_POLL", 0.05)
    from app.main import app
    with TestClient(app) as c:
        yield c


def wait(fn, timeout=30.0):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.05)
    raise AssertionError("condizione non raggiunta")


def finished(client, name):
    client.post("/api/projects", json={"name": name})
    client.post(f"/api/projects/{name}/videos", files={"file": ("a.mp4", b"x" * 100, "video/mp4")})
    client.post(f"/api/projects/{name}/start")
    wait(lambda: client.get(f"/api/projects/{name}").json()["status"] == "done")


def test_history_notifications_mesh_and_disk(client, monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "send", lambda title, message, link="", c=None: sent.append((title, link)))
    assert client.put("/api/notify", json={"kind": "webhook", "url": "http://h/x", "panel_url": "http://p:1"}).status_code == 200
    assert client.put("/api/notify", json={"kind": "webhook", "url": "ftp://h/x"}).status_code == 422
    finished(client, "full")
    wait(lambda: sent)
    assert sent == [("full: completato", "http://p:1/p/full")]
    events = [e["event"] for e in client.get("/api/projects/full/history").json()]
    assert events[:2] == ["video_added", "queued"] and events[-1] == "done" and events.count("stage_done") == 11
    assert "reset" not in events

    assert client.post("/api/projects/full/mesh", json={"depth": 3}).status_code == 422
    assert client.post("/api/projects/full/mesh", json={}).status_code == 200
    job = wait(lambda: (j := client.get("/api/projects/full/jobs").json().get("mesh")) and j["status"] == "done" and j)
    assert job["result"]["triangles"] == 2000
    assert any(a["path"] == "mesh.ply" for a in client.get("/api/projects/full").json()["artifacts"])

    u = client.get("/api/projects/full/disk").json()
    assert u["parts"] and u["free_gb"] > 0
    assert client.post("/api/projects/full/disk/clean", json={"ids": ["boh"]}).status_code == 400


def test_view_settings_and_scale_export_through_the_api(client):
    finished(client, "view")
    assert client.post("/api/projects/view/scale-export").status_code == 400          # prima serve la scala
    assert client.put("/api/projects/view/view", json={"up": [0, 0, 0]}).status_code == 422
    r = client.put("/api/projects/view/view", json={"reference": {"a": [0, 0, 0], "b": [1, 0, 0], "meters": 2}})
    assert r.json()["view"]["scale"] == 2
    got = client.get("/api/projects/view/view").json()
    assert got["view"]["scale"] == 2 and "splat_clean.ply" in got["models"]
    assert client.post("/api/projects/view/crop", json={"source": "../../state.json", "min": [0, 0, 0], "max": [1, 1, 1]}).status_code == 422


def test_image_endpoints_reject_traversal(client):
    client.post("/api/projects", json={"name": "img"})
    for url in ("/api/projects/img/quality/test/..%2Fstate.json", "/api/projects/img/quality/altro/a.jpg",
                "/api/projects/img/preview/..%2F..%2Fstate.json", "/api/projects/img/frame/v1/..%2F..%2Fstate.json",
                "/api/projects/img/frame/..%2F/a.jpg"):
        assert client.get(url).status_code == 404, url
    assert client.get("/api/projects/img/quality").json() == {}
    assert client.get("/api/projects/img/preview").json() == {"steps": [], "real": False, "angles": []}
    assert client.get("/api/projects/img/map").status_code == 400


@pytest.mark.parametrize("auto_resume,expected", [(True, "queued"), (False, "failed")])
def test_a_job_interrupted_by_a_restart(tmp_path, monkeypatch, auto_resume, expected):
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path)
    monkeypatch.setattr(config, "AUTO_RESUME", auto_resume)
    store.create("jj")
    jobs.save("jj", "mesh", {"status": "running", "params": {}, "requested": 1})
    jobs.recover()                                       # senza lavoratore: nessuno lo esegue mentre controlliamo
    assert jobs.load("jj", "mesh")["status"] == expected


def test_mesh_command_uses_scale_and_orientation_only_for_the_same_model(client):
    from app import viewcfg
    client.post("/api/projects", json={"name": "mc"})
    export = ProjectPaths("mc").export
    export.mkdir(parents=True, exist_ok=True)
    (export / "splat_clean.ply").write_bytes(b"uno")
    viewcfg.save("mc", {"up": [0, 1, 0], "reference": {"a": [0, 0, 0], "b": [1, 0, 0], "meters": 3}})
    cmd = jobs.mesh_command("mc", {"metric": True, "level": True, "depth": 9})
    assert cmd[cmd.index("--scale") + 1] == "3.0" and cmd[cmd.index("--up") + 1] == "0.0,1.0,0.0"
    assert "--scale" not in jobs.mesh_command("mc", {"metric": False})
    (export / "splat_clean.ply").write_bytes(b"modello nuovo")
    assert "--scale" not in jobs.mesh_command("mc", {"metric": True, "level": True})


def test_a_separate_worker_publishes_its_system_figures(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    m = Manager()
    m._embedded = False
    snap = m._system_snapshot()
    assert snap and "mem_total_gb" in snap and m._system_snapshot() is snap       # non ricalcolato ad ogni giro
    m._embedded = True
    assert m._system_snapshot() is None
