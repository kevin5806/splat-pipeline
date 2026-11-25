"""Stima dei tempi, pulizia dei completati e letture di sistema."""
import time

import pytest
from fastapi.testclient import TestClient

from app import estimate, store, system
from app.main import app
from app.models import Project, Settings, Video

INFO_4K = {"width": 3840, "height": 2160, "fps": 30.0, "duration_s": 441.3, "total_frames": 13242}


def _project(name="est", videos=1, settings=None, **video_kw):
    vids = [Video(id=f"v{i + 1}", filename=f"{i}.mp4", info=dict(INFO_4K), **video_kw) for i in range(videos)]
    return Project(name=name, videos=vids, settings=settings or Settings())


def test_estimate_matches_the_measured_run():
    e = estimate.project_estimate(_project())
    assert e["frames"] == 828                                   # 13.242 / 16: il primo giro misurato
    s = e["stages"]
    assert 5000 < s["train"]["est"] < 6500                      # misurato 87-112 minuti
    assert 1200 < s["matching"]["est"] < 1800                   # ~25 minuti
    assert 800 < s["mapping"]["est"] < 1400                     # ~18 minuti
    assert 2.5 * 3600 < e["total"]["est"] < 3.5 * 3600
    assert all(s[k]["low"] <= s[k]["est"] <= s[k]["high"] for k in s)
    assert e["total"]["low"] < e["total"]["est"] < e["total"]["high"]
    assert e["warnings"] == []


def test_estimate_needs_video_info_and_skips_external():
    p = _project()
    p.videos[0].info = None
    assert estimate.project_estimate(p) is None
    assert estimate.project_estimate(Project(name="vuoto")) is None
    ext = _project("ext")
    ext.external = {"train_log": "x"}
    assert estimate.project_estimate(ext) is None


def test_estimate_warns_on_the_huge_project_that_was_actually_started(monkeypatch):
    monkeypatch.setattr(estimate, "_mem_total_gb", lambda: 48.0)
    p = Project(name="grosso", videos=[
        Video(id="v1", filename="a.mp4", frame_step=2, info={**INFO_4K, "total_frames": 6759}),
        Video(id="v2", filename="b.mp4", frame_step=2, info={**INFO_4K, "total_frames": 7113})])
    e = estimate.project_estimate(p)
    assert e["frames"] == 6937
    assert e["total"]["est"] > 24 * 3600                        # oltre un giorno
    text = " ".join(e["warnings"])
    assert "Tempo molto lungo" in text and "RAM" in text
    assert e["stages"]["matching"]["basis"] == "estrapolato"


def test_target_frames_and_downscale_change_the_estimate():
    base = estimate.project_estimate(_project())
    few = estimate.project_estimate(_project(settings=Settings(target_frames=400)))
    many = estimate.project_estimate(_project(settings=Settings(target_frames=1600)))
    assert few["frames"] < base["frames"] < many["frames"]
    assert few["total"]["est"] < base["total"]["est"] < many["total"]["est"]
    full = estimate.project_estimate(_project(settings=Settings(downscale=1)))
    assert full["stages"]["train"]["est"] > 2 * base["stages"]["train"]["est"]      # 4K nativo costa molto di più


def test_remaining_shrinks_as_stages_finish_and_uses_the_training_eta():
    p = _project()
    full = estimate.project_estimate(p)["remaining"]["est"]
    now = time.time()
    for sid in ("probe", "frames"):
        st = p.stage(sid)
        st.status, st.started, st.ended = "done", now - 500, now - 400
    assert estimate.project_estimate(p)["remaining"]["est"] < full
    tr = p.stage("train")
    tr.status, tr.started, tr.progress, tr.detail = "running", now - 600, 40.0, "ETA 1 h, 5 m, 39 s"
    for sid in ("features", "matching", "mapping", "refine", "convert", "test"):
        p.stage(sid).status = "done"
    e = estimate.project_estimate(p, now)
    assert e["stages"]["train"]["remaining"] == 3939
    assert e["remaining"]["est"] == 3939 + e["stages"]["export"]["est"]


def test_calibration_scales_remaining_stages_by_the_real_speed():
    p = _project()
    first = estimate.project_estimate(p)
    now = time.time()
    f = p.stage("features")
    f.status, f.started, f.ended = "done", now - 3000, now - 3000 + 2 * first["stages"]["features"]["est"]   # il doppio del previsto
    p.stage("probe").status = p.stage("frames").status = "done"
    e = estimate.project_estimate(p)
    assert e["calibration"]["cpu"] > 1.5
    assert e["stages"]["matching"]["est"] > 1.5 * first["stages"]["matching"]["est"]
    assert e["calibration"]["gpu"] == 1.0                       # le fasi GPU non cambiano


def test_parse_eta():
    assert estimate._parse_eta("ETA 1 h, 5 m, 39 s") == 3939
    assert estimate._parse_eta("ETA 29 m, 7 s") == 1747
    assert estimate._parse_eta("ETA 48 s") == 48
    assert estimate._parse_eta("immagini 3/10") is None
    assert estimate._parse_eta("") is None


def test_system_parsers():
    stat = "cpu  100 0 50 800 50 0 0 0 0 0\ncpu0 1 2 3 4"
    assert system.parse_proc_stat(stat) == (150, 1000)
    assert system.cpu_percent_between((150, 1000), (350, 1400)) == 50.0
    assert system.cpu_percent_between((10, 10), (10, 10)) == 0.0
    mem = "MemTotal:       48000000 kB\nMemFree:         2000000 kB\nMemAvailable:   40000000 kB\nBuffers: 1 kB"
    assert system.parse_meminfo(mem) == (8.0, 48.0)             # usati = totale - disponibile


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def test_cleanup_removes_only_finished_projects(client):
    for name, status in (("cln-fin1", "done"), ("cln-fin2", "done"), ("cln-busy", "failed"), ("cln-stop", "stopped")):
        p = store.create(name)
        p.status = status
        store.save(p)
    ext = store.create("cln-ext")
    ext.status, ext.external = "done", {"train_log": "x"}
    store.save(ext)
    (store.ProjectPaths("cln-fin1").frames).mkdir(parents=True, exist_ok=True)
    (store.ProjectPaths("cln-fin1").frames / "a.jpg").write_bytes(b"x" * 5000)

    dry = client.post("/api/projects/cleanup", json={"dry_run": True}).json()
    assert sorted(i["name"] for i in dry["would_delete"] if i["name"].startswith("cln-")) == ["cln-fin1", "cln-fin2"]      # non toccati: falliti, fermati, monitorati
    assert store.load("cln-fin1") is not None                                          # dry run: non cancella

    r = client.post("/api/projects/cleanup", json={}).json()
    assert sorted(i["name"] for i in r["deleted"] if i["name"].startswith("cln-")) == ["cln-fin1", "cln-fin2"] and r["freed_mb"] >= 0
    assert store.load("cln-fin1") is None and store.load("cln-fin2") is None
    assert all(store.load(n) is not None for n in ("cln-busy", "cln-stop", "cln-ext"))
    assert client.post("/api/projects/cleanup", json={"status": "failed"}).status_code == 400


def test_project_view_includes_size_and_estimate(client):
    client.post("/api/projects", json={"name": "view1"})
    client.post("/api/projects/view1/videos", files={"file": ("a.mp4", b"\x00" * 2048, "video/mp4")})
    d = client.get("/api/projects/view1").json()
    assert d["size_mb"] >= 0
    assert d["estimate"] and d["estimate"]["frames"] > 0 and "total" in d["estimate"]    # in modalità simulata ha già le info
    assert client.get("/api/system").json().get("mem_total_gb", 1) is not False
