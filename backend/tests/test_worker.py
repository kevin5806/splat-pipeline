"""Lavoratore separato dal sito: ripresa dopo riavvii, coda, viewer e marcatori di ripresa."""
import asyncio
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config, control, store
from app.main import app
from app.models import Project, Settings, Video
from app.pipeline import Manager
from app.stages import _features_signature, reset_markers
from app.store import ProjectPaths


class Worker:
    """Un lavoratore in un thread a parte, come un container separato dal sito."""

    def __init__(self):
        self.mgr = Manager()
        self.ready = threading.Event()
        self.loop = None
        self.task = None

    def _run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.task = self.loop.create_task(self.mgr.worker_loop(embedded=False))
        self.ready.set()
        try:
            self.loop.run_until_complete(self.task)
        except asyncio.CancelledError:
            pass
        finally:
            self.loop.close()

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        assert self.ready.wait(5)
        return self

    def stop(self):
        self.loop.call_soon_threadsafe(self.task.cancel)
        self.thread.join(20)
        assert not self.thread.is_alive()


def _wait(fn, timeout=20.0, msg="condizione non raggiunta"):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.05)
    raise AssertionError(msg)


def _new(client, name):
    assert client.post("/api/projects", json={"name": name}).status_code == 201
    assert client.post(f"/api/projects/{name}/videos", files={"file": ("a.mp4", b"\x00" * 1024, "video/mp4")}).status_code == 200


def _get(client, name):
    return client.get(f"/api/projects/{name}").json()


def _stage(p, sid):
    return next(s for s in p["stages"] if s["id"] == sid)


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    """Ogni test ha la sua cartella dati: un lavoratore riprende QUALSIASI progetto interrotto che trova, quindi i
    progetti lasciati da altri test lo disturberebbero."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path / "projects")
    (tmp_path / "projects").mkdir()
    yield tmp_path


@pytest.fixture()
def separate_worker(monkeypatch):
    """Sito senza lavoratore incorporato + lavoratore a parte: la configurazione di produzione."""
    monkeypatch.setattr(config, "EMBEDDED_WORKER", False)
    monkeypatch.setattr(config, "FAKE_SPEED", 0.5)         # lento: il lavoro dura abbastanza da poterlo interrompere
    monkeypatch.setattr(config, "AUTO_RESUME", True)
    workers = []
    yield workers
    for w in workers:
        if w.thread.is_alive():
            w.stop()


def test_site_restart_does_not_interrupt_a_running_job(separate_worker, monkeypatch):
    w = Worker().start()
    separate_worker.append(w)
    with TestClient(app) as site1:
        _new(site1, "w-site")
        site1.post("/api/projects/w-site/start")
        _wait(lambda: _get(site1, "w-site")["current_stage"] in ("frames", "features", "matching"), msg="il lavoro non è partito")
    # il sito si "aggiorna": è stato chiuso del tutto. Il lavoratore e il lavoro non se ne accorgono.
    time.sleep(1.0)
    monkeypatch.setattr(config, "FAKE_SPEED", 200)
    with TestClient(app) as site2:
        p = _get(site2, "w-site")
        assert p["status"] in ("running", "done"), p["status"]          # non è "interrupted"
        assert all(s["status"] != "interrupted" for s in p["stages"])
        p = _wait(lambda: _get(site2, "w-site") if _get(site2, "w-site")["status"] == "done" else None, msg="non è finito")
        assert all(s["status"] == "done" for s in p["stages"])


def test_worker_restart_resumes_and_keeps_finished_stages(separate_worker, monkeypatch):
    w1 = Worker().start()
    separate_worker.append(w1)
    with TestClient(app) as site:
        _new(site, "w-resume")
        site.post("/api/projects/w-resume/start")
        _wait(lambda: _stage(_get(site, "w-resume"), "frames")["status"] in ("running", "done"))
        _wait(lambda: _stage(_get(site, "w-resume"), "probe")["status"] == "done")
        probe_ended = _stage(_get(site, "w-resume"), "probe")["ended"]
        w1.stop()                                                        # spegnimento ordinato del lavoratore
        p = _get(site, "w-resume")
        assert p["status"] == "interrupted"
        assert any(s["status"] == "interrupted" for s in p["stages"])
        assert not control.worker_status()["alive"]
        # il lavoratore riparte (AUTO_RESUME): il progetto torna in coda da solo e continua
        monkeypatch.setattr(config, "FAKE_SPEED", 200)
        w2 = Worker().start()
        separate_worker.append(w2)
        p = _wait(lambda: _get(site, "w-resume") if _get(site, "w-resume")["status"] == "done" else None, msg="non è ripartito")
        assert _stage(p, "probe")["ended"] == probe_ended               # la fase finita NON è stata rifatta
        assert all(s["status"] == "done" for s in p["stages"])


def test_hard_kill_is_recovered_as_interrupted_then_resumed(separate_worker, monkeypatch):
    """Spegnimento improvviso: nessuno ha chiuso niente, lo stato resta 'running' su disco."""
    with TestClient(app) as site:
        _new(site, "w-crash")
        p = store.load("w-crash")
        p.status = "running"                  # come se il processo fosse morto di colpo durante la prima fase
        p.stages[0].status, p.stages[0].started = "running", time.time()
        store.save(p)
        monkeypatch.setattr(config, "FAKE_SPEED", 200)
        w = Worker().start()
        separate_worker.append(w)
        p = _wait(lambda: _get(site, "w-crash") if _get(site, "w-crash")["status"] == "done" else None, msg="non ha ripreso")
        assert all(s["status"] == "done" for s in p["stages"])


def test_queue_order_and_position(separate_worker, monkeypatch):
    """Senza lavoratore i progetti restano in coda: le posizioni sono esatte e non dipendono dai tempi."""
    monkeypatch.setattr(config, "FAKE_SPEED", 200)
    with TestClient(app) as c:
        for name in ("q-1", "q-2", "q-3"):
            _new(c, name)
            c.post(f"/api/projects/{name}/start")
            time.sleep(0.02)                                   # ordine di arrivo ben distinto
        assert {n: _get(c, n)["queue_position"] for n in ("q-1", "q-2", "q-3")} == {"q-1": 1, "q-2": 2, "q-3": 3}
        assert all(_get(c, n)["status"] == "queued" for n in ("q-1", "q-2", "q-3"))
        w = Worker().start()
        separate_worker.append(w)
        for name in ("q-1", "q-2", "q-3"):
            _wait(lambda n=name: _get(c, n)["status"] == "done", msg=f"{name} non è finito")
        ended = [_stage(_get(c, n), "export")["ended"] for n in ("q-1", "q-2", "q-3")]
        assert ended == sorted(ended)                          # eseguiti in ordine di arrivo, uno alla volta
        assert all(_get(c, n)["queue_position"] is None for n in ("q-1", "q-2", "q-3"))


def test_stop_without_worker_marks_it_stopped(monkeypatch):
    monkeypatch.setattr(config, "EMBEDDED_WORKER", False)
    with TestClient(app) as site:
        _new(site, "w-nobody")
        site.post("/api/projects/w-nobody/start")
        assert _get(site, "w-nobody")["status"] == "queued"                    # nessuno lo esegue
        assert site.get("/api/system").json()["worker"]["alive"] is False
        assert site.post("/api/projects/w-nobody/stop").json()["status"] == "stopped"


def test_stop_of_a_running_job_goes_through_the_worker(separate_worker, monkeypatch):
    w = Worker().start()
    separate_worker.append(w)
    with TestClient(app) as site:
        _new(site, "w-stop")
        site.post("/api/projects/w-stop/start")
        _wait(lambda: _get(site, "w-stop")["status"] == "running")
        r = site.post("/api/projects/w-stop/stop").json()
        assert r["status"] == "stopped"
        assert _stage(r, "probe")["status"] in ("done", "interrupted")
        # fermato dall'utente: un nuovo lavoratore NON lo riprende da solo (a differenza di un'interruzione di sistema)
        w.stop()
        w2 = Worker().start()
        separate_worker.append(w2)
        time.sleep(1.0)
        assert _get(site, "w-stop")["status"] == "stopped"


def test_viewer_goes_through_the_worker(client_embedded):
    c = client_embedded
    _new(c, "w-view")
    c.post("/api/projects/w-view/start")
    _wait(lambda: _get(c, "w-view")["status"] == "done")
    assert c.get("/api/system").json()["worker"]["alive"] is True
    assert c.post("/api/projects/w-view/viewer").json()["requested"] is True
    _wait(lambda: (c.get("/api/system").json()["viewer"] or {}).get("project") == "w-view", msg="il viewer non è partito")
    c.delete("/api/viewer")
    _wait(lambda: c.get("/api/system").json()["viewer"] is None, msg="il viewer non si è fermato")


@pytest.fixture()
def client_embedded(monkeypatch):
    monkeypatch.setattr(config, "EMBEDDED_WORKER", True)
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------- marcatori di ripresa (senza ffmpeg/COLMAP)
def _project_with_frames(tmp_path, monkeypatch, name="mk"):
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path)
    p = Project(name=name, settings=Settings(), videos=[Video(id="v1", filename="a.mp4"), Video(id="v2", filename="b.mp4", device=2)])
    paths = ProjectPaths(name)
    for vid, n in (("v1", 3), ("v2", 2)):
        (paths.frames / vid).mkdir(parents=True)
        for i in range(1, n + 1):
            (paths.frames / vid / f"frame_{i:05d}.jpg").write_bytes(b"x")
        (paths.frames / vid / ".done").write_text("{}")
    paths.colmap.mkdir(parents=True)
    return p, paths


def test_features_signature_changes_only_when_the_input_changes(tmp_path, monkeypatch):
    p, paths = _project_with_frames(tmp_path, monkeypatch)
    sig = _features_signature(p, paths)
    assert _features_signature(p, paths) == sig                                  # stabile: ripresa possibile
    p.videos[0].camera_model = "OPENCV_FISHEYE"
    assert _features_signature(p, paths) != sig                                  # un'altra camera: da rifare
    p.videos[0].camera_model = None
    assert _features_signature(p, paths) == sig
    (paths.frames / "v1" / "frame_00004.jpg").write_bytes(b"x")                   # un frame in più: da rifare
    assert _features_signature(p, paths) != sig
    p.videos[1].device = 3
    assert _features_signature(p, paths) != _features_signature(Project(name="x", videos=[p.videos[0]]), paths)


def test_reset_markers_clears_resume_state_only_from_the_given_stage(tmp_path, monkeypatch):
    p, paths = _project_with_frames(tmp_path, monkeypatch)
    sig = paths.colmap / "features.sig"
    sig.write_text("abc")
    reset_markers(paths, "matching")                                              # più avanti delle feature: restano
    assert sig.exists() and (paths.frames / "v1" / ".done").exists()
    reset_markers(paths, "features")                                              # rifare le feature: da zero
    assert not sig.exists() and (paths.frames / "v1" / ".done").exists()
    reset_markers(paths, "probe")                                                 # rifare tutto
    assert not (paths.frames / "v1" / ".done").exists() and not (paths.frames / "v2" / ".done").exists()
