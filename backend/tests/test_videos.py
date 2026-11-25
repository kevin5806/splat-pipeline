"""Più video per progetto: API, migrazione dei progetti vecchi e copertura per video."""
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import store
from app.main import app
from app.models import Project, Settings, Video
from app.stages import Ctx, check_devices, coverage_per_video
from app.store import ProjectPaths


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def _video(client, name, filename="giro.mp4", **form):
    return client.post(f"/api/projects/{name}/videos", files={"file": (filename, b"\x00" * 1024, "video/mp4")}, data=form)


def _wait(client, name, states, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        p = client.get(f"/api/projects/{name}").json()
        if p["status"] in states:
            return p
        time.sleep(0.1)
    raise AssertionError(p["status"])


def test_add_multiple_videos_with_devices_and_run(client):
    client.post("/api/projects", json={"name": "multi1"})
    r1 = _video(client, "multi1", "telefono.mp4", device="1")
    r2 = _video(client, "multi1", "gopro.mov", device="2", camera_model="OPENCV_FISHEYE")
    r3 = _video(client, "multi1", "telefono2.mp4", device="1")
    assert r1.status_code == r2.status_code == r3.status_code == 200
    vids = client.get("/api/projects/multi1").json()["videos"]
    assert [v["id"] for v in vids] == ["v1", "v2", "v3"]
    assert [v["device"] for v in vids] == [1, 2, 1]
    assert vids[1]["camera_model"] == "OPENCV_FISHEYE" and vids[0]["camera_model"] is None
    assert vids[1]["name"] == "gopro.mov"
    assert (Path(ProjectPaths("multi1").input) / "v2.mov").exists()

    client.post("/api/projects/multi1/start")
    p = _wait(client, "multi1", {"done", "failed", "blocked"})
    assert p["status"] == "done"
    probe = next(s for s in p["stages"] if s["id"] == "probe")["metrics"]
    assert probe["video_count"] == 3 and set(probe["videos"]) == {"v1", "v2", "v3"}
    refine = next(s for s in p["stages"] if s["id"] == "refine")["metrics"]
    assert set(refine["per_video"]) == {"v1", "v2", "v3"} and refine["cameras"] == 2
    assert refine["per_video"]["v1"]["missing_ranges"][0] == [22.0, 26.5, 8]


def test_changing_videos_restarts_from_the_beginning(client):
    client.post("/api/projects", json={"name": "multi2"})
    _video(client, "multi2")
    client.post("/api/projects/multi2/start")
    assert _wait(client, "multi2", {"done", "failed"})["status"] == "done"
    r = _video(client, "multi2", "altro.mp4")                       # aggiungere un video rifa tutto
    p = r.json()
    assert all(s["status"] == "pending" for s in p["stages"]) and p["status"] == "idle"
    assert len(p["videos"]) == 2


def test_patch_and_delete_video(client):
    client.post("/api/projects", json={"name": "multi3"})
    _video(client, "multi3")
    _video(client, "multi3", "b.mp4")
    r = client.patch("/api/projects/multi3/videos/v2", json={"device": 3, "camera_model": "SIMPLE_RADIAL"})
    assert r.status_code == 200
    v2 = next(v for v in r.json()["videos"] if v["id"] == "v2")
    assert v2["device"] == 3 and v2["camera_model"] == "SIMPLE_RADIAL"
    r = client.patch("/api/projects/multi3/videos/v2", json={"camera_model": None})     # torna al predefinito
    assert next(v for v in r.json()["videos"] if v["id"] == "v2")["camera_model"] is None
    assert client.patch("/api/projects/multi3/videos/v2", json={"device": 99}).status_code == 422
    assert client.patch("/api/projects/multi3/videos/v9", json={"device": 2}).status_code == 404

    frames = ProjectPaths("multi3").frames / "v2"
    frames.mkdir(parents=True, exist_ok=True)
    (frames / "frame_00001.jpg").write_bytes(b"x")
    r = client.delete("/api/projects/multi3/videos/v2")
    assert r.status_code == 200 and [v["id"] for v in r.json()["videos"]] == ["v1"]
    assert not (ProjectPaths("multi3").input / "v2.mp4").exists() and not frames.exists()
    # l'id di un video tolto si riusa senza collisioni sui file
    _video(client, "multi3", "c.mp4")
    assert [v["id"] for v in client.get("/api/projects/multi3").json()["videos"]] == ["v1", "v2"]


def test_upload_validation(client):
    client.post("/api/projects", json={"name": "multi4"})
    assert _video(client, "multi4", device="0").status_code == 422
    assert _video(client, "multi4", device="9").status_code == 422
    assert _video(client, "multi4", camera_model="BANANA").status_code == 422
    assert _video(client, "multi4", "x.exe").status_code == 415
    for i in range(12):
        assert _video(client, "multi4", f"{i}.mp4").status_code == 200
    assert _video(client, "multi4", "13.mp4").status_code == 400          # massimo 12


def test_legacy_single_video_project_is_migrated():
    old = {"name": "vecchio", "video": "video.mp4", "settings": {}, "stages": []}
    p = Project.model_validate(old)
    assert len(p.videos) == 1 and p.videos[0].id == "v1" and p.videos[0].filename == "video.mp4"
    assert "video" not in p.model_dump()
    assert Project.model_validate({"name": "senza", "video": None}).videos == []


def test_check_devices_rejects_mixed_camera_models_on_the_same_device():
    p = Project(name="d1", videos=[Video(id="v1", filename="a.mp4", device=1),
                                   Video(id="v2", filename="b.mp4", device=1, camera_model="OPENCV_FISHEYE")])
    with pytest.raises(RuntimeError, match="dispositivo 1"):
        check_devices(p)
    p.videos[1].device = 2
    check_devices(p)  # dispositivi diversi: ok


def test_coverage_per_video_finds_gaps_in_seconds(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.PROJECTS_DIR", tmp_path)
    p = Project(name="cov", settings=Settings(frame_step=16),
                videos=[Video(id="v1", filename="a.mp4"), Video(id="v2", filename="b.mp4")])
    paths = ProjectPaths("cov")
    for vid, n in (("v1", 30), ("v2", 10)):
        (paths.frames / vid).mkdir(parents=True)
        for i in range(1, n + 1):
            (paths.frames / vid / f"frame_{i:05d}.jpg").write_bytes(b"x")
    # v1: mancano i frame 5-9 (5 frame di fila) e il 20 da solo; v2: registrati tutti
    registered = [f"v1/frame_{i:05d}.jpg" for i in range(1, 31) if i not in (5, 6, 7, 8, 9, 20)]
    registered += [f"v2/frame_{i:05d}.jpg" for i in range(1, 11)]
    p.stage("probe").metrics = {"videos": {"v1": {"fps": 30.0}, "v2": {"fps": 30.0}}}
    ctx = Ctx(p, paths, p.stage("refine"), lambda: None)
    cov = coverage_per_video(ctx, registered)
    assert cov["v1"]["registered"] == 24 and cov["v1"]["total"] == 30 and cov["v1"]["missing"] == 6
    # frame 5..9 -> da (5-1)*16/30 = 2.1 s a 9*16/30 = 4.8 s; il frame 20 isolato non è un "buco" da segnalare
    assert cov["v1"]["missing_ranges"] == [[2.1, 4.8, 5]]
    assert cov["v2"]["ratio"] == 1.0 and cov["v2"]["missing_ranges"] == []


# ---------------------------------------------------------------- scelta dei frame

from app.stages import compute_steps  # noqa: E402


def _vids(*steps):
    return [Video(id=f"v{i + 1}", filename=f"{i}.mp4", frame_step=s) for i, s in enumerate(steps)]


def test_compute_steps_default_uses_project_step():
    vs = _vids(None, None)
    assert compute_steps(vs, {"v1": 13000, "v2": 3000}, Settings(frame_step=16)) == {"v1": 16, "v2": 16}


def test_compute_steps_target_frames_spreads_proportionally():
    vs = _vids(None, None)
    # 13000 + 3000 = 16000 frame video; ne voglio ~800 -> un frame ogni 20 per entrambi (quindi 650 + 150 = 800)
    steps = compute_steps(vs, {"v1": 13000, "v2": 3000}, Settings(target_frames=800))
    assert steps == {"v1": 20, "v2": 20}
    assert -(-13000 // 20) + -(-3000 // 20) == 800


def test_compute_steps_per_video_override_and_target_for_the_rest():
    vs = _vids(4, None, None)       # il primo fissato a "uno ogni 4"
    totals = {"v1": 1200, "v2": 6000, "v3": 6000}
    steps = compute_steps(vs, totals, Settings(target_frames=600))
    assert steps["v1"] == 4                                     # 300 frame usati dal primo
    # restano 300 frame per gli altri 12000 -> passo 40
    assert steps["v2"] == steps["v3"] == 40


def test_compute_steps_limits():
    vs = _vids(None)
    assert compute_steps(vs, {"v1": 100}, Settings(target_frames=6000))["v1"] == 1          # mai sotto 1
    assert compute_steps(vs, {"v1": 10_000_000}, Settings(target_frames=30))["v1"] == 300   # mai sopra 300
    assert compute_steps(vs, {"v1": 0}, Settings(target_frames=100, frame_step=16))["v1"] == 16


def test_api_per_video_frame_step_and_target_frames_in_a_run(client):
    client.post("/api/projects", json={"name": "fr1", "settings": {"target_frames": 500}})
    _video(client, "fr1", "a.mp4")
    _video(client, "fr1", "b.mp4")
    r = client.patch("/api/projects/fr1/videos/v2", json={"frame_step": 8})
    assert next(v for v in r.json()["videos"] if v["id"] == "v2")["frame_step"] == 8
    assert client.patch("/api/projects/fr1/videos/v2", json={"frame_step": 0}).status_code == 422
    assert client.patch("/api/projects/fr1/videos/v2", json={"frame_step": 999}).status_code == 422
    client.post("/api/projects/fr1/start")
    p = _wait(client, "fr1", {"done", "failed"})
    assert p["status"] == "done"
    videos = next(s for s in p["stages"] if s["id"] == "probe")["metrics"]["videos"]
    assert videos["v2"]["step"] == 8                              # impostato a mano
    assert videos["v1"]["step"] >= 1 and videos["v1"]["expected_frames"] > 0
    r = client.patch("/api/projects/fr1/videos/v2", json={"frame_step": None})      # torna automatico
    assert next(v for v in r.json()["videos"] if v["id"] == "v2")["frame_step"] is None
    assert client.post("/api/projects", json={"name": "fr2", "settings": {"target_frames": 10}}).status_code == 422
