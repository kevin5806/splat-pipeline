"""Cronologia, notifiche, impostazioni del viewer, spazio su disco, mappa, confronto del test, splat e mesh."""
import json
import math
import struct
import sys
import types
from pathlib import Path

import numpy as np
import pytest

from app import config, disk, history, mapdata, notify, store, viewcfg
from app.models import Project, Settings, Video
from app.store import ProjectPaths
from app.tools import plyops
from app.tools.colmap_model import read_poses
from app.tools.crop import crop, read_ply


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path / "projects")
    (tmp_path / "projects").mkdir()


def project(name="ex", **kw):
    p = store.create(name, Settings(frame_step=8))
    p.videos = [Video(id="v1", filename="a.mp4", name="a.mp4")]
    for k, v in kw.items():
        setattr(p, k, v)
    store.save(p)
    return p


# ---------------------------------------------------------------- cronologia
def test_history_appends_and_survives_garbage():
    project()
    history.add("ex", "stage_start", stage="frames", detail=None)
    history.path("ex").open("a").write("{rotto\n")
    history.add("ex", "stage_done", stage="frames", duration=3)
    rows = history.read("ex")
    assert [r["event"] for r in rows] == ["stage_start", "stage_done"] and "detail" not in rows[0]
    history.add("nonesiste", "x")                                   # nessun errore, nessuna cartella creata
    assert not ProjectPaths("nonesiste").root.exists()


# ---------------------------------------------------------------- notifiche
def test_notify_requests_for_each_destination():
    r = notify.build_request({**notify.DEFAULTS, "kind": "ntfy", "url": "https://ntfy.example/casa-splat", "token": "tk"},
                             "Città: è pronto", "ok", "http://pannello/p/x")
    body = json.loads(r.data)
    assert r.full_url == "https://ntfy.example/" and body["topic"] == "casa-splat" and body["title"] == "Città: è pronto"
    assert body["click"] == "http://pannello/p/x" and r.get_header("Authorization") == "Bearer tk"
    r = notify.build_request({**notify.DEFAULTS, "kind": "telegram", "token": "123:abc", "chat_id": "9"}, "T", "M")
    assert r.full_url == "https://api.telegram.org/bot123:abc/sendMessage" and json.loads(r.data)["text"] == "T\nM"
    r = notify.build_request({**notify.DEFAULTS, "kind": "homeassistant", "url": "http://ha.local:8123/api/webhook/abc"}, "T", "M")
    assert r.full_url.endswith("/api/webhook/abc") and json.loads(r.data)["message"] == "M"


def test_notify_secrets_are_masked_and_kept_when_unchanged():
    notify.save({"kind": "homeassistant", "url": "http://ha:8123/api/webhook/segreto", "token": "tok"})
    shown = notify.public()
    assert "segreto" not in json.dumps(shown) and shown["token"] == notify.SECRET and shown["url"].startswith("http://ha:8123/")
    notify.save({**shown, "events": ["blocked"]})                      # il modulo rimanda i valori mascherati
    c = notify.load()
    assert c["url"].endswith("/segreto") and c["token"] == "tok" and c["events"] == ["blocked"]


@pytest.mark.parametrize("bad", [{"kind": "ntfy", "url": "https://ntfy.sh/"}, {"kind": "webhook", "url": "file:///etc/passwd"},
                                 {"kind": "telegram", "token": "x"}, {"kind": "boh"}])
def test_notify_rejects_bad_settings(bad):
    with pytest.raises(ValueError):
        notify.save(bad)


def test_notify_messages_follow_the_chosen_events_and_never_raise(monkeypatch):
    p = project()
    p.status = "blocked"
    p.stage("recover").status, p.stage("recover").detail = "blocked", "Serve un video"
    assert notify.project_message(p) is None                           # spente
    notify.save({"kind": "webhook", "url": "http://127.0.0.1:9/x", "panel_url": "http://pannello:8088/", "events": ["blocked"]})
    title, message, link = notify.project_message(p)
    assert "serve una tua decisione" in title and "Serve un video" in message and link == "http://pannello:8088/p/ex"
    p.status = "done"
    assert notify.project_message(p) is None
    p.status = "blocked"
    notify.project_event(p)                                            # destinazione irraggiungibile: nessuna eccezione


def test_notify_send_error_does_not_leak_the_token():
    c = {**notify.DEFAULTS, "kind": "webhook", "url": "http://127.0.0.1:9/segretissimo"}
    with pytest.raises(RuntimeError) as e:
        notify.send("t", "m", c=c)
    assert "segretissimo" not in str(e.value)


# ---------------------------------------------------------------- impostazioni del viewer
def test_view_settings_scale_and_model_change():
    project()
    export = ProjectPaths("ex").export
    (export / "splat_clean.ply").write_bytes(b"modello uno")
    v = viewcfg.save("ex", {"up": [0, 0, 2], "reference": {"a": [0, 0, 0], "b": [0, 2, 0], "meters": 1.0}})
    assert v["up"] == [0, 0, 1] and v["scale"] == 0.5 and v["stale"] is False
    (export / "splat_clean.ply").write_bytes(b"modello due, diverso")
    assert viewcfg.load("ex")["stale"] is True                         # la scala non vale per un altro modello


@pytest.mark.parametrize("bad", [{"up": [0, 0, 0]}, {"reference": {"a": [0, 0, 0], "b": [0, 0, 0], "meters": 1}},
                                 {"reference": {"a": [0, 0, 0], "b": [1, 0, 0], "meters": -1}},
                                 {"box": {"min": [0, 0, 0], "max": [1, 0, 1]}}, {"start": {"pos": [1, 2]}},
                                 {"up": [float("nan"), 0, 1]}])
def test_view_settings_validation(bad):
    with pytest.raises(ValueError):
        viewcfg.validate(bad)


# ---------------------------------------------------------------- spazio su disco
def test_cleanup_keeps_what_matters():
    p = project()
    paths = ProjectPaths("ex")
    for sid in ("refine", "test", "train"):
        p.stage(sid).status = "done"
    files = ["input/a.mp4", "frames/v1/frame_00001.jpg", "colmap/database.db", "colmap/refined/images.bin", "colmap/sparse/0/images.bin",
             "colmap/snapshots/200/images.bin", "colmap/recovery/round1/before/images.bin", "colmap/recovery/round1/model/images.bin",
             "colmap/recovery/round2/before/images.bin", "outputs/test/x.ckpt", "outputs/main/x.ckpt", "outputs/main.prev/x.ckpt",
             "export/splat_clean.ply", "export/splat_clean_precedente.ply"]
    for f in files:
        (paths.root / f).parent.mkdir(parents=True, exist_ok=True)
        (paths.root / f).write_bytes(b"x" * 1000)
    u = disk.usage(p)
    assert {c["id"] for c in u["cleanable"]} == {"test", "previous", "sparse", "recovery"} and u["total_mb"] >= 0
    disk.clean(p, ["test", "previous", "sparse", "recovery"])
    left = {str(f.relative_to(paths.root)).replace("\\", "/") for f in paths.root.rglob("*") if f.is_file() and f.name != "state.json"}
    assert left == {"input/a.mp4", "frames/v1/frame_00001.jpg", "colmap/database.db", "colmap/refined/images.bin",
                    "colmap/recovery/round1/before/images.bin",        # serve al confronto prima/dopo
                    "outputs/main/x.ckpt", "export/splat_clean.ply"}
    with pytest.raises(ValueError):
        disk.clean(p, ["sparse"])                                      # non c'è più niente: non si accetta
    with pytest.raises(ValueError):
        disk.clean(p, ["../input"])


# ---------------------------------------------------------------- mappa
def write_images(path: Path, cameras: dict):
    """cameras: nome -> (quaternione, centro, punti 3D). t = -R c"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        f.write(struct.pack("<Q", len(cameras)))
        for i, (name, (q, c, obs)) in enumerate(cameras.items(), start=1):
            r = np.array(mapdata.rotation_matrix(q))
            t = -r @ np.array(c, dtype=float)
            f.write(struct.pack("<I4d3dI", i, *q, *t, 1) + name.encode() + b"\0" + struct.pack("<Q", obs))
            f.write(b"".join(struct.pack("<ddq", 0, 0, k) for k in range(obs)))


# camera orizzontale che guarda lungo +Y del mondo con "alto" = +Z del mondo
UPRIGHT = (math.sqrt(0.5), math.sqrt(0.5), 0.0, 0.0)


def test_camera_pose_math():
    r = np.array(mapdata.rotation_matrix(UPRIGHT))
    assert np.allclose(r @ np.array([0, 0, -1.0]), [0, 1, 0])           # "giù" nel mondo è l'asse y della camera
    assert np.allclose(mapdata.camera_up(UPRIGHT), [0, 0, 1])
    t = -r @ np.array([1.0, 2.0, 3.0])
    assert np.allclose(mapdata.camera_center(UPRIGHT, t), [1, 2, 3])
    m = np.array(mapdata.level_matrix([0, -1, 0]))
    assert np.allclose(m @ [0, -1, 0], [0, 0, 1]) and np.allclose(m @ m.T, np.eye(3)) and np.isclose(np.linalg.det(m), 1)


def test_map_marks_weak_and_interpolates_missing_cameras():
    p = project()
    paths = ProjectPaths("ex")
    (paths.frames / "v1").mkdir(parents=True)
    for i in range(1, 11):
        (paths.frames / "v1" / f"frame_{i:05d}.jpg").write_bytes(b"x")
    cams = {f"v1/frame_{i:05d}.jpg": (UPRIGHT, (float(i), 0.0, 1.0), 10 if i == 3 else 200) for i in range(1, 11) if i not in (5, 6)}
    write_images(paths.refined / "images.bin", cams)
    with (paths.refined / "points3D.bin").open("wb") as f:
        f.write(struct.pack("<Q", 2))
        for pid, xyz in ((1, (5.0, 2.0, 1.0)), (2, (5000.0, 0.0, 0.0))):
            f.write(struct.pack("<Q3d3BdQ", pid, *xyz, 10, 20, 30, 0.5, 2) + struct.pack("<IIII", 1, 1, 2, 2))
    assert len(read_poses(paths.refined / "images.bin")) == 8
    m = mapdata.build(p)
    by_name = {c["f"]: c for c in m["cameras"]}
    assert by_name["frame_00003.jpg"]["q"] == "weak" and by_name["frame_00001.jpg"]["q"] == "good"
    assert by_name["frame_00005.jpg"]["q"] == "missing"
    xs = [by_name[f"frame_{i:05d}.jpg"]["p"][0] for i in range(1, 11)]
    steps = np.diff(xs)
    assert np.allclose(np.abs(steps), 1, atol=1e-3)                     # le mancanti sono state messe al posto giusto
    assert all(abs(c["p"][2]) < 1e-6 for c in m["cameras"])             # pavimento orizzontale
    assert len(m["points"]) == 3 and m["colors"] == [10, 20, 30]        # il punto lontanissimo è scartato
    first = mapdata.cached(p)
    assert mapdata.cached(p).stat().st_mtime == first.stat().st_mtime


def test_map_needs_a_reconstruction():
    with pytest.raises(ValueError):
        mapdata.build(project())


# ---------------------------------------------------------------- confronto del test
def test_eval_views_are_linked_to_video_and_time(tmp_path):
    from PIL import Image
    from app.tools.eval_views import eval_indices, process
    names = sorted(f"v1__frame_{i:05d}.jpg" for i in range(1, 101)) + ["v2__extra_000300.jpg"]
    names = sorted(names)
    ds = tmp_path / "ds"
    ds.mkdir()
    (ds / "transforms.json").write_text(json.dumps({"frames": [{"file_path": f"images/{n}"} for n in names]}))
    idx = eval_indices(len(names))
    assert len(idx) == len(names) - math.ceil(len(names) * 0.9)
    renders = tmp_path / "renders"
    renders.mkdir()
    for k, _i in enumerate(idx):
        img = np.full((40, 120, 3), 100, dtype=np.uint8)
        img[:, 60:] = 100 + 2 * (k + 1)                                 # ogni vista un po' peggio della precedente
        Image.fromarray(img).save(renders / f"eval_img_{k:04d}.png")
    out = tmp_path / "q"
    s = process(renders, ds, {"v1": {"fps": 30, "step": 8}, "v2": {"fps": 30, "step": 8}}, out, width=100)
    assert s["count"] == len(idx) and s["views"][0]["psnr"] > s["views"][-1]["psnr"]
    first = s["views"][0]
    assert first["name"] == names[idx[0]]
    assert first["video"] == "v1" and first["t"] == round((int(first["name"].split("_")[-1].split(".")[0]) - 1) * 8 / 30, 2)
    kept = [v for v in s["views"] if "file" in v]
    assert kept and all((out / v["file"]).exists() for v in kept)
    assert min(kept, key=lambda v: v["psnr"])["group"] == "worst"
    with pytest.raises(ValueError, match="Attese"):
        (renders / "eval_img_0000.png").unlink()
        process(renders, ds, {}, out)


def test_frame_time_of_extra_frames():
    from app.tools.eval_views import frame_time
    assert frame_time("v2__extra_000300.jpg", {"v2": {"fps": 30, "step": 8}}) == ("v2", 10.0)


# ---------------------------------------------------------------- splat
FIELDS = ["x", "y", "z", "f_dc_0", "f_dc_1", "f_dc_2", "opacity", "scale_0", "scale_1", "scale_2", "rot_0", "rot_1", "rot_2", "rot_3"]


def write_splat(path: Path, rows: np.ndarray):
    data = np.zeros(len(rows), dtype=[(f, "<f4") for f in FIELDS])
    for i, f in enumerate(FIELDS):
        data[f] = rows[:, i]
    header = ["ply", "format binary_little_endian 1.0", f"element vertex {len(rows)}",
              *[f"property float {f}" for f in FIELDS], "end_header"]
    path.write_bytes(("\n".join(header) + "\n").encode() + data.tobytes())


def row(x=0.0, y=0.0, z=0.0, opacity=4.0, scales=(-3.0, -3.0, -6.0), rot=(1, 0, 0, 0), color=(0.0, 0.0, 0.0)):
    return [x, y, z, *color, opacity, *scales, *rot]


def test_box_crop_uses_the_leveled_frame_and_keeps_the_original(tmp_path):
    src = tmp_path / "s.ply"
    write_splat(src, np.array([row(0, 0, 0), row(0, -2, 0), row(0, 2, 0)], dtype=float))
    before = src.read_bytes()
    r = plyops.box_crop(src, tmp_path / "c.ply", [-1, -1, 1], [1, 1, 3], up=[0, -1, 0])    # "alto" = -Y
    _h, data = read_ply(tmp_path / "c.ply")
    assert r["kept"] == 1 and float(data["y"][0]) == -2.0 and src.read_bytes() == before
    with pytest.raises(ValueError):
        plyops.box_crop(src, tmp_path / "d.ply", [9, 9, 9], [10, 10, 10])


def test_scaling_to_metres_scales_positions_and_sizes(tmp_path):
    src = tmp_path / "s.ply"
    write_splat(src, np.array([row(1, 2, 3)], dtype=float))
    plyops.scale_ply(src, tmp_path / "m.ply", 2.0)
    _h, data = read_ply(tmp_path / "m.ply")
    assert [float(data[a][0]) for a in "xyz"] == [2, 4, 6]
    assert np.isclose(np.exp(data["scale_0"][0]), 2 * np.exp(-3.0), rtol=1e-5)
    with pytest.raises(ValueError):
        plyops.scale_ply(src, tmp_path / "n.ply", 0)


def test_crop_records_the_new_centre(tmp_path):
    write_splat(tmp_path / "s.ply", np.array([row(x, 5, 5) for x in range(-5, 6)], dtype=float))
    info = crop(tmp_path / "s.ply", tmp_path / "out.ply", 3.5)
    assert json.loads((tmp_path / "out.json").read_text())["center"] == info["center"] == [0.0, 5.0, 5.0]


def test_mesh_points_normals_and_orientation(tmp_path):
    from app.tools.mesh import orient_normals, surface_points
    rows = [row(i * 0.1, 0, 0) for i in range(100)]
    rows.append(row(50, 0, 0, opacity=-6.0))                            # quasi trasparente: via
    write_splat(tmp_path / "s.ply", np.array(rows, dtype=float))
    _h, data = read_ply(tmp_path / "s.ply")
    points, colors, normals = surface_points(data)
    assert len(points) == 100 and np.allclose(colors, 0.5)
    assert np.allclose(np.abs(normals), [0, 0, 1])                      # l'asse più corto è Z
    up = orient_normals(points, normals, np.array([[5.0, 0.0, 10.0]]))
    down = orient_normals(points, normals, np.array([[5.0, 0.0, -10.0]]))
    assert np.allclose(up, [0, 0, 1]) and np.allclose(down, [0, 0, -1])


def test_cameras_are_brought_into_the_exported_frame(tmp_path):
    from app.tools.mesh import camera_positions
    write_images(tmp_path / "model" / "images.bin", {"v1/frame_00001.jpg": (UPRIGHT, (1.0, 2.0, 3.0), 1)})
    (tmp_path / "dp.json").write_text(json.dumps({"transform": [[0, -1, 0, 1], [1, 0, 0, 0], [0, 0, 1, 0]], "scale": 0.5}))
    (tmp_path / "crop.json").write_text(json.dumps({"center": [0.1, 0.2, 0.3]}))
    got = camera_positions(tmp_path / "model", tmp_path / "dp.json", tmp_path / "crop.json")
    assert np.allclose(got, [[-0.5 - 0.1, 0.5 - 0.2, 1.5 - 0.3]])
    assert camera_positions(tmp_path / "model", tmp_path / "manca.json", tmp_path / "crop.json") is None


# ---------------------------------------------------------------- anteprima del training
def test_preview_never_breaks_training(tmp_path, monkeypatch, capsys):
    from app.tools import train_preview as tp

    class Trainer:
        calls = 0

        def eval_iteration(self, step):
            Trainer.calls += 1
            return "originale"
    module = types.ModuleType("nerfstudio.engine.trainer")
    module.Trainer = Trainer
    for name in ("nerfstudio", "nerfstudio.engine"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "nerfstudio.engine.trainer", module)
    monkeypatch.setenv("SPLAT_PREVIEW_DIR", str(tmp_path))
    monkeypatch.setattr(tp, "_disabled", False)
    seen = []

    def render(trainer, step, out):
        seen.append(step)
        if step == 2000:
            raise MemoryError("GPU piena")
    monkeypatch.setattr(tp, "render", render)
    tp.install()
    t = Trainer()
    assert [t.eval_iteration(s) for s in (0, 100, 200, 1000, 1500, 2000, 3000)] == ["originale"] * 7
    assert seen == [200, 1000, 2000] and Trainer.calls == 7             # dopo l'errore si disattiva, il training no
    assert "il training continua" in capsys.readouterr().out


def test_training_command_goes_through_the_preview_wrapper():
    from app.stages import _with_preview
    cmd = _with_preview(["ns-train", "splatfacto", "--max-num-iterations", "10"])
    assert cmd[1:4] == ["-m", "app.tools.train_preview", "splatfacto"] and cmd[-1] == "10"


# ---------------------------------------------------------------- allineamento tra modelli allenati in momenti diversi
def test_previous_model_is_aligned_exactly_to_the_current_one():
    project()
    export = ProjectPaths("ex").export
    export.mkdir(parents=True, exist_ok=True)

    def rot(axis, angle):
        c, s_ = math.cos(angle), math.sin(angle)
        return np.array({"z": [[c, -s_, 0], [s_, c, 0], [0, 0, 1]], "x": [[1, 0, 0], [0, c, -s_], [0, s_, c]]}[axis])

    frames = {"splat_clean": (rot("z", 0.4), np.array([1.0, 2.0, 3.0]), 0.5, np.array([0.1, -0.2, 0.3])),
              "splat_clean_precedente": (rot("x", -2.9) @ rot("z", 1.7), np.array([-1.0, 0.5, 0.0]), 0.8, np.array([1.0, 1.0, -1.0]))}
    for stem, (r, t, s_, c) in frames.items():
        (export / f"{stem}.json").write_text(json.dumps({"center": c.tolist()}))
        (export / f"{stem}.dataparser.json").write_text(json.dumps({"transform": np.column_stack([r, t]).tolist(), "scale": s_}))
    a = viewcfg.alignment("ex", "splat_clean_precedente.ply")
    assert a["exact"]
    x, y, z, w = a["quaternion"]
    m = np.array(mapdata.rotation_matrix((w, x, y, z)))
    for point in ([0.0, 0.0, 0.0], [1.0, -2.0, 0.5], [-3.0, 1.0, 4.0]):           # un punto reale, in coordinate COLMAP
        in_file = {k: s_ * (r @ np.array(point) + t) - c for k, (r, t, s_, c) in frames.items()}
        moved = a["scale"] * (m @ in_file["splat_clean_precedente"]) + np.array(a["position"])
        assert np.allclose(moved, in_file["splat_clean"], atol=1e-6)
    (export / "splat_clean_precedente.dataparser.json").unlink()
    rough = viewcfg.alignment("ex", "splat_clean_precedente.ply")
    assert rough["exact"] is False and np.allclose(rough["position"], [0.9, 1.2, -1.3])
    (export / "splat_clean_precedente.json").unlink()
    assert viewcfg.alignment("ex", "splat_clean_precedente.ply") is None
