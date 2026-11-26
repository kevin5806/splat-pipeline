"""Recupero dei tratti deboli: logica, fase completa (COLMAP e ffmpeg simulati), ripresa e video di supporto."""
import asyncio
import json
import re
import sqlite3
import struct
from pathlib import Path

import pytest

from app import config, store, weak
from app.models import Project, Settings, StageState, STAGES, Video
from app.pipeline import Manager
from app.recover import load_state, stage_recover
from app.stages import Blocked, Ctx, database_matches_files, keep_previous_results, reset_markers, stage_frames, write_features_signature, _features_signature
from app.store import ProjectPaths
from app.tools.colmap_model import read_observations

FPS, STEP, N_BASE = 30.0, 8, 150            # un video di 40 s, un frame ogni 8
GAP = range(400, 640)                       # indici (8 s) dove la camera si perde


def write_images_bin(path: Path, observations: dict[str, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        f.write(struct.pack("<Q", len(observations)))
        for i, (name, obs) in enumerate(sorted(observations.items()), start=1):
            f.write(struct.pack("<I4d3dI", i, 1, 0, 0, 0, 0, 0, 0, 1) + name.encode() + b"\0")
            points = [struct.pack("<ddq", 1.0, 2.0, k) for k in range(obs)] + [struct.pack("<ddq", 1.0, 2.0, -1)] * 3
            f.write(struct.pack("<Q", len(points)) + b"".join(points))


def test_observations_count_only_triangulated_points(tmp_path):
    write_images_bin(tmp_path / "images.bin", {"v1/frame_00001.jpg": 5, "v1/frame_00002.jpg": 0})
    assert read_observations(tmp_path / "images.bin") == {"v1/frame_00001.jpg": 5, "v1/frame_00002.jpg": 0}


# ---------------------------------------------------------------- logica pura

def seq_with_gap(gap=GAP, weak_obs=None, n=N_BASE):
    obs = {}
    files = []
    for i in range(1, n + 1):
        name = f"frame_{i:05d}.jpg"
        files.append(name)
        idx = (i - 1) * STEP
        if idx not in gap:
            obs[f"v1/{name}"] = 500
        elif weak_obs is not None:
            obs[f"v1/{name}"] = weak_obs
    return weak.sequence(files, "v1", STEP, obs)


def test_unregistered_and_barely_connected_frames_are_weak():
    for seq in (seq_with_gap(), seq_with_gap(weak_obs=20)):       # camera persa, oppure agganciata con 20 punti
        runs = weak.weak_runs(seq, FPS, STEP)
        assert len(runs) == 1 and runs[0]["a"] == 400 and runs[0]["b"] == 632
        assert runs[0]["start_s"] == 13.3 and runs[0]["end_s"] == 21.3
    assert weak.weak_runs(seq_with_gap(weak_obs=300), FPS, STEP) == []


def test_a_single_missed_frame_is_ignored_and_near_gaps_are_merged():
    assert weak.weak_runs(seq_with_gap(gap=range(400, 401)), FPS, STEP) == []
    seq = seq_with_gap(gap=list(range(400, 440)) + list(range(456, 496)))
    assert len(weak.weak_runs(seq, FPS, STEP)) == 1


def test_significance():
    assert weak.significant({"v1": [{"seconds": 3.0}]}, 400)
    assert not weak.significant({"v1": [{"seconds": 0.6}]}, 400)
    assert weak.significant({"v1": [{"seconds": 0.8}] * 50}, 400)
    assert not weak.significant({}, 400)


def plan(seq, rounds=()):
    return weak.plan_round({"v1": seq}, {"v1": weak.weak_runs(seq, FPS, STEP)}, {"v1": FPS}, {"v1": STEP}, list(rounds))


def test_first_round_doubles_the_density_only_around_the_gap():
    p = plan(seq_with_gap())
    spec = p["videos"]["v1"]
    assert spec["step"] == 4 and spec["windows"] == [[355, 677]]            # il buco più 1,5 s per lato
    assert all(i % 4 == 0 and i % 8 != 0 and 355 <= i <= 677 for i in spec["frames"])
    assert p["extra_frames"] == len(spec["frames"]) == 41


def test_second_round_is_finer_but_only_where_the_first_helped():
    seq = seq_with_gap()
    runs = weak.weak_runs(seq, FPS, STEP)
    helped = {"kind": "densify", "accepted": True, "weak_before": {"v1": [{**runs[0], "seconds": 20.0}]}}
    assert plan(seq, [helped])["videos"]["v1"]["step"] == 2
    useless = {"kind": "densify", "accepted": True, "weak_before": {"v1": runs}}
    assert plan(seq, [useless]) is None                                     # non è migliorato: serve un video nuovo
    assert plan(seq, [helped, helped]) is None                              # al massimo due giri
    assert plan(seq, [{**helped, "accepted": False}]) is None


def test_too_many_frames_raise_the_step(monkeypatch):
    monkeypatch.setattr(weak, "MAX_EXTRA", 15)
    seq = seq_with_gap()
    runs = weak.weak_runs(seq, FPS, STEP)
    helped = {"kind": "densify", "accepted": True, "weak_before": {"v1": [{**runs[0], "seconds": 20.0}]}}
    assert plan(seq, [helped]) is None or plan(seq, [helped])["extra_frames"] <= 15


def test_temporal_pairs_involve_only_new_images():
    names = [f"i{k:02d}" for k in range(30)]
    pairs = weak.temporal_pairs(names, {"i15"})
    assert all("i15" in p for p in pairs) and ("i05", "i15") in pairs and ("i15", "i25") in pairs and ("i00", "i15") in pairs


# ---------------------------------------------------------------- la fase completa, con COLMAP finto

class World:
    """Un COLMAP e un ffmpeg finti: `recoverable(indice)` dice quali frame nuovi il mapper riesce ad agganciare."""

    def __init__(self, tmp_path, monkeypatch, recovery="ask", recoverable=lambda i: True, lose_old=False):
        monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path / "projects")
        monkeypatch.setattr("app.stages.ensure_vocab_tree", lambda: tmp_path / "vt.bin")
        self.recoverable, self.lose_old, self.commands, self.fail_mapper_once = recoverable, lose_old, [], False
        self.p = store.create("rec", Settings(recovery=recovery, frame_step=STEP))
        self.p.videos = [Video(id="v1", filename="a.mp4", name="stanza.mp4")]
        self.paths = ProjectPaths("rec")
        self.paths.ensure()
        (self.paths.input / "a.mp4").write_bytes(b"video")
        self.p.stage("probe").metrics = {"videos": {"v1": {"fps": FPS, "step": STEP, "width": 100, "height": 50, "total_frames": 1200}}}
        (self.paths.frames / "v1").mkdir(parents=True)
        for i in range(1, N_BASE + 1):
            (self.paths.frames / "v1" / f"frame_{i:05d}.jpg").write_bytes(b"x")
        (self.paths.frames / "v1" / ".done").write_text(json.dumps({"step": STEP, "video": "a.mp4", "size": 5, "frames": N_BASE}))
        old = {f"v1/frame_{i:05d}.jpg": 500 for i in range(1, N_BASE + 1) if (i - 1) * STEP not in GAP}
        write_images_bin(self.paths.refined / "images.bin", old)
        self.paths.colmap.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.paths.database)
        con.execute("CREATE TABLE images (name TEXT, camera_id INTEGER)")
        con.executemany("INSERT INTO images VALUES (?, 7)", [(f"v1/frame_{i:05d}.jpg",) for i in range(1, N_BASE + 1)])
        con.commit()
        con.close()
        monkeypatch.setattr(Ctx, "cmd", self._cmd())

    def _cmd(self):
        world = self

        async def cmd(ctx, args, **kw):
            args = [str(a) for a in args]
            world.commands.append(args)
            if args[0] == "ffmpeg" and args[-1].endswith("%06d.jpg"):        # estrazione mirata
                expr = args[args.index("-vf") + 1]
                windows = [(int(a), int(b)) for a, b in re.findall(r"between\(n\\,(\d+)\\,(\d+)\)", expr)]
                step = int(re.search(r"mod\(n\\,(\d+)\)", expr).group(1))
                for k, idx in enumerate(weak.selected_indices([list(w) for w in windows], step), start=1):
                    Path(args[-1] % k).write_bytes(str(idx).encode())
            elif args[0] == "ffmpeg" and args[-1].endswith("frame_%05d.jpg"):  # video di supporto
                for i in range(1, 41):
                    Path(args[-1] % i).write_bytes(b"s")
            elif args[0] == "ffmpeg":                                          # miniature
                Path(args[-1]).write_bytes(b"thumb")
            elif args[:2] == ["colmap", "mapper"]:
                if world.fail_mapper_once:
                    world.fail_mapper_once = False
                    return 1
                source = read_observations(Path(args[args.index("--input_path") + 1]) / "images.bin")
                queries = (world.paths.colmap / "recovery").glob("round*/new_images.txt")
                newest = max(queries, key=lambda f: int(f.parent.name[5:]))
                for name in newest.read_text().split():
                    idx = weak.source_index(name.split("/")[1], STEP)
                    if world.recoverable(idx) or name.startswith("v2/") and world.recoverable(-1):
                        source[name] = 400
                        if name.startswith("v1/extra_") and idx - idx % STEP in GAP:      # il vicino di prima si aggancia
                            source[f"v1/frame_{idx // STEP + 1:05d}.jpg"] = 400
                if world.lose_old:
                    source = dict(list(source.items())[: len(source) // 2])
                write_images_bin(Path(args[args.index("--output_path") + 1]) / "images.bin", source)
            return 0
        return cmd

    def run(self):
        st = self.p.stage("recover")
        st.status = "running"
        return asyncio.run(stage_recover(Ctx(self.p, self.paths, st, lambda: None)))

    def colmap(self, name):
        return [a for a in self.commands if a[:2] == ["colmap", name]]


def opt(args, name):
    return args[args.index(name) + 1]


def test_ask_mode_proposes_without_touching_anything(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    with pytest.raises(Blocked, match="41 frame più fitti"):
        w.run()
    m = w.p.stage("recover").metrics
    assert m["decision"] == "approve" and m["plan"]["step"] == {"v1": 4} and m["plan"]["est_seconds"] > 0
    assert m["weak"][0]["start_s"] == 13.3 and m["weak"][0]["thumb"] and m["weak"][0]["video_name"] == "stanza.mp4"
    assert not [a for a in w.commands if a[0] == "colmap"] and not list(w.paths.frames.rglob("extra_*"))


def test_after_approval_the_gap_is_recovered_without_redoing_the_reconstruction(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    before = len(read_observations(w.paths.refined / "images.bin"))
    w.p.recovery_approved = 1
    r = w.run()
    assert r["weak"] == [] and r["weak_seconds"] == 0
    assert r["rounds"][0]["new_registered"] == 41 and r["rounds"][0]["accepted"] and r["rounds"][0]["weak_s_after"] == 0
    assert len(read_observations(w.paths.refined / "images.bin")) > before + 40
    assert len(read_observations(w.paths.colmap / "recovery" / "round1" / "before" / "images.bin")) == before
    # i frame in più stanno accanto a quelli veri, e portano il loro indice nel video (il contenuto lo conferma)
    for f in (w.paths.frames / "v1").glob("extra_*.jpg"):
        assert int(f.read_text()) == int(f.stem.split("_")[1])
    # niente matching completo né ricostruzione da zero; camera e pose di prima restano ferme
    assert not w.colmap("exhaustive_matcher") and not w.colmap("bundle_adjuster")
    assert opt(w.colmap("feature_extractor")[0], "--ImageReader.existing_camera_id") == "7"
    assert "--VocabTreeMatching.match_list_path" in w.colmap("vocab_tree_matcher")[0]
    mapper = w.colmap("mapper")[0]
    assert opt(mapper, "--Mapper.fix_existing_images") == "1" and opt(mapper, "--input_path") == str(w.paths.refined)


def test_auto_mode_does_two_rounds_then_asks_for_a_new_video(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, recovery="auto", recoverable=lambda i: 400 <= i < 480 or (400 <= i < 560 and i % 4 != 0))
    with pytest.raises(Blocked, match="serve un video"):
        w.run()
    m = w.p.stage("recover").metrics
    assert m["decision"] == "new_video" and m["weak"]
    assert [r["step"]["v1"] for r in m["rounds"]] == [4, 2]
    assert m["rounds"][1]["weak_s_after"] < m["rounds"][0]["weak_s_before"]


def test_a_round_that_recovers_nothing_goes_straight_to_the_new_video_request(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, recovery="auto", recoverable=lambda i: False)
    with pytest.raises(Blocked, match="serve un video"):
        w.run()
    assert len(load_state(w.paths)["rounds"]) == 1


def test_a_worse_model_is_thrown_away(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, recovery="auto", lose_old=True)
    before = read_observations(w.paths.refined / "images.bin")
    with pytest.raises(Blocked):
        w.run()
    assert read_observations(w.paths.refined / "images.bin") == before
    assert load_state(w.paths)["rounds"][0]["accepted"] is False


def test_an_interrupted_round_resumes_from_where_it_was(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    w.p.recovery_approved = 1
    w.fail_mapper_once = True
    with pytest.raises(RuntimeError, match="registrazione"):
        w.run()
    assert load_state(w.paths)["current"]["number"] == 1
    snap = w.paths.colmap / "recovery" / "round1" / "snapshots" / "100"
    write_images_bin(snap / "images.bin", read_observations(w.paths.refined / "images.bin"))
    done_before = len(w.commands)
    r = w.run()
    again = w.commands[done_before:]
    assert not any(a[0] == "ffmpeg" and a[-1].endswith("%06d.jpg") for a in again)      # frame non rifatti
    assert not any(a[1:2] in (["matches_importer"], ["vocab_tree_matcher"]) for a in again)  # matching non rifatto
    assert opt([a for a in again if a[1] == "mapper"][0], "--input_path") == str(snap)    # riparte dallo snapshot
    assert r["rounds"][0]["accepted"] and load_state(w.paths)["current"] is None


def test_interrupted_while_swapping_the_model_keeps_a_valid_one(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, recovery="off")
    w.paths.refined.rename(w.paths.colmap / "refined.new")
    w.run()
    assert (w.paths.refined / "images.bin").exists()


def test_off_mode_only_reports(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, recovery="off")
    r = w.run()
    assert r["skipped"] and r["weak_seconds"] == 8.0 and not [a for a in w.commands if a[0] == "colmap"]


def test_support_video_is_attached_to_the_existing_model(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, recovery="off", recoverable=lambda i: True)
    w.p.videos.append(Video(id="v2", filename="b.mp4", name="supporto.mp4", support=True))
    (w.paths.input / "b.mp4").write_bytes(b"video2")

    async def probe(c, path, log):
        return {"width": 100, "height": 50, "fps": 30.0, "duration_s": 10.0, "total_frames": 320, "rotation": 0}
    monkeypatch.setattr("app.stages._probe_one", probe)
    r = w.run()
    assert r["rounds"][0]["kind"] == "support" and r["rounds"][0]["new_registered"] == 40
    assert w.p.stage("probe").metrics["videos"]["v2"]["step"] == STEP          # stesso passo del primo video
    assert r["per_video"]["v2"]["registered"] == 40
    assert not [a for a in w.commands if a[:2] == ["colmap", "bundle_adjuster"]]
    done = len(w.commands)
    w.run()                                                                      # rilanciata: non lo integra due volte
    assert not [a for a in w.commands[done:] if a[:2] == ["colmap", "mapper"]]


def test_support_video_that_does_not_attach_asks_for_another(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, recovery="off", recoverable=lambda i: False)
    w.p.videos.append(Video(id="v2", filename="b.mp4", support=True))
    (w.paths.input / "b.mp4").write_bytes(b"video2")

    async def probe(c, path, log):
        return {"width": 100, "height": 50, "fps": 30.0, "duration_s": 10.0, "total_frames": 320, "rotation": 0}
    monkeypatch.setattr("app.stages._probe_one", probe)
    with pytest.raises(Blocked, match="non si è agganciato"):
        w.run()


# ---------------------------------------------------------------- non perdere il lavoro già fatto

def test_extra_frames_do_not_invalidate_frames_features_or_database(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    sig = _features_signature(w.p, w.paths)
    (w.paths.frames / "v1" / "extra_000404.jpg").write_bytes(b"x")
    assert _features_signature(w.p, w.paths) == sig                             # il database non si butta
    w.p.stage("probe").metrics["expected_frames"] = N_BASE
    r = asyncio.run(stage_frames(Ctx(w.p, w.paths, w.p.stage("frames"), lambda: None)))
    assert r["frames"] == N_BASE and (w.paths.frames / "v1" / "extra_000404.jpg").exists()   # nessuna nuova estrazione
    assert not [a for a in w.commands if a[0] == "ffmpeg"]


def test_database_citing_deleted_images_is_not_reused(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    assert database_matches_files(w.paths)
    (w.paths.frames / "v1" / "frame_00003.jpg").unlink()
    assert not database_matches_files(w.paths)


def test_redoing_training_starts_clean_but_keeps_the_previous_model(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    ck = w.paths.outputs / "main" / "splatfacto" / "run1" / "nerfstudio_models"
    ck.mkdir(parents=True)
    (ck / "step-000029999.ckpt").write_bytes(b"model")
    w.paths.export.mkdir(parents=True, exist_ok=True)
    (w.paths.export / "splat_clean.ply").write_bytes(b"ply")
    reset_markers(w.paths, "train")
    assert not (w.paths.outputs / "main").exists()                              # niente "training già completo"
    assert (w.paths.outputs / "main.prev" / "splatfacto" / "run1" / "nerfstudio_models" / "step-000029999.ckpt").exists()
    assert (w.paths.export / "splat_clean_precedente.ply").read_bytes() == b"ply"


def test_redoing_only_the_export_keeps_the_trained_model(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    (w.paths.outputs / "main").mkdir(parents=True)
    keep_previous_results(w.paths, "export")
    assert (w.paths.outputs / "main").exists()


def test_old_projects_get_the_new_stage_in_the_right_place():
    old = [StageState(id=i, label=l).model_dump() for i, l in STAGES if i != "recover"]
    for s in old:
        if s["id"] in ("probe", "frames", "features", "matching", "mapping", "refine", "convert"):
            s["status"] = "done"
    p = Project.model_validate({"name": "old", "stages": old})
    assert [s.id for s in p.stages] == [i for i, _ in STAGES]
    assert p.stage("recover").status == "done" and p.stage("recover").metrics == {"skipped": True}
    fresh = Project.model_validate({"name": "new", "stages": [dict(s, status="pending") for s in old]})
    assert fresh.stage("recover").status == "pending"


def test_approve_and_reopen_in_the_manager(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    m = Manager()
    with pytest.raises(ValueError):
        m.approve_recovery("rec")
    with pytest.raises(ValueError, match="camere"):
        m.reopen_recovery("rec")
    p = store.load("rec")
    p.stage("refine").status = "done"
    st = p.stage("recover")
    st.status, st.metrics = "blocked", {"decision": "approve", "rounds": [{"kind": "densify"}]}
    p.status = "blocked"
    store.save(p)
    assert m.approve_recovery("rec").recovery_approved == 2
    (w.paths.colmap / "recovery").mkdir(exist_ok=True)
    (w.paths.colmap / "recovery" / "state.json").write_text('{"rounds": [{"kind": "densify"}], "current": null}')
    (w.paths.outputs / "main").mkdir(parents=True)
    q = m.reopen_recovery("rec")
    assert q.stage("refine").status == "done" and q.stage("recover").status == "pending" and q.recovery_approved == 2
    assert load_state(w.paths)["rounds"] and (w.paths.outputs / "main.prev").exists()
    assert m.reset_from("rec", "recover").recovery_approved == 0 and load_state(w.paths)["rounds"] == []
