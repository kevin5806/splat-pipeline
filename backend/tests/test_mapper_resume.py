"""Regressione: dopo una ripresa da snapshot COLMAP scrive il modello in sparse/ (non in sparse/0).

Caso reale: sparse/0 conteneva un frammento di 2 camere dell'esecuzione interrotta e sparse/ il modello completo da 1.541.
Veniva scelto il frammento e la pipeline proseguiva fino a crollare nel test.
"""
import asyncio
import shutil
import struct
from pathlib import Path

import pytest

from app import config, stages
from app.models import Project, Settings, Video
from app.stages import Ctx, check_reconstruction, model_dirs, pick_best_model
from app.store import ProjectPaths


def write_model(d: Path, names: list[str]) -> None:
    d.mkdir(parents=True, exist_ok=True)
    out = bytearray(struct.pack("<Q", len(names)))
    for i, name in enumerate(names, start=1):
        out += struct.pack("<I4d3dI", i, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1) + name.encode() + b"\x00" + struct.pack("<Q", 0)
    (d / "images.bin").write_bytes(bytes(out))
    (d / "cameras.bin").write_bytes(struct.pack("<Q", 1) + b"\x00" * 40)
    (d / "points3D.bin").write_bytes(struct.pack("<Q", 0))


def names(n, vid="v1"):
    return [f"{vid}/frame_{i:05d}.jpg" for i in range(1, n + 1)]


def project(tmp_path, monkeypatch, frames=200):
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path)
    p = Project(name="rs", settings=Settings(), videos=[Video(id="v1", filename="a.mp4")])
    paths = ProjectPaths("rs")
    paths.ensure()
    (paths.frames / "v1").mkdir(parents=True)
    for nm in names(frames):
        (paths.frames / nm).write_bytes(b"x")
    p.stage("probe").metrics = {"videos": {"v1": {"fps": 30.0, "step": 8}}}
    return p, paths


def test_the_model_in_the_sparse_root_wins_over_a_leftover_fragment(tmp_path):
    sparse = tmp_path / "sparse"
    write_model(sparse / "0", names(2))        # frammento dell'esecuzione interrotta
    write_model(sparse, names(150))            # modello completo scritto dalla ripresa
    assert {d.name for d in model_dirs(sparse)} == {"sparse", "0"}
    assert pick_best_model(sparse) == sparse


def test_best_model_is_chosen_by_camera_count_not_file_size(tmp_path):
    sparse = tmp_path / "sparse"
    write_model(sparse / "0", names(10))
    write_model(sparse / "1", names(40))
    (sparse / "0" / "images.bin").write_bytes((sparse / "0" / "images.bin").read_bytes() + b"\x00" * 100_000)   # più grande
    assert pick_best_model(sparse).name == "1"


def test_check_reconstruction():
    with pytest.raises(RuntimeError, match="solo 2 camere su 1735"):
        check_reconstruction(2, 1735, "Test")
    with pytest.raises(RuntimeError):
        check_reconstruction(300, 1735, "Test")            # 17%: sotto il minimo
    check_reconstruction(1541, 1735, "Test")
    check_reconstruction(25, 25, "Test")                   # progetto piccolissimo: tutte ricostruite va bene
    check_reconstruction(None, 100, "Test")


def _fake_colmap(refine_keeps=None):
    """Imita COLMAP: il mapper ripreso scrive in sparse/; il bundle adjustment copia il modello in uscita."""
    async def run(c, args, *a, **k):
        opt = {args[i]: args[i + 1] for i in range(len(args) - 1) if args[i].startswith("--")}
        if args[0] == "mapper":
            out = Path(opt["--output_path"])
            write_model(out if "--input_path" in opt else out / "0", names(180))
        elif args[0] == "bundle_adjuster":
            src, dst = Path(opt["--input_path"]), Path(opt["--output_path"])
            if refine_keeps is None:
                for f in src.glob("*.bin"):
                    shutil.copy(f, dst / f.name)
            else:
                write_model(dst, names(refine_keeps))
        return 0
    return run


def test_resumed_mapping_then_refine_uses_the_complete_model(tmp_path, monkeypatch):
    p, paths = project(tmp_path, monkeypatch)
    write_model(paths.sparse / "0", names(2))                      # lasciato dall'esecuzione interrotta
    write_model(paths.snapshots / "0000000001", names(100))        # snapshot da cui ripartire
    monkeypatch.setattr(stages, "_colmap", _fake_colmap())
    m = asyncio.run(stages.stage_mapping(Ctx(p, paths, p.stage("mapping"), lambda: None)))
    assert m == {"registered": 180, "total": 200}
    r = asyncio.run(stages.stage_refine(Ctx(p, paths, p.stage("refine"), lambda: None)))
    assert r["registered"] == 180 and r["per_video"]["v1"]["registered"] == 180


def test_fresh_mapping_removes_old_models(tmp_path, monkeypatch):
    p, paths = project(tmp_path, monkeypatch)
    write_model(paths.sparse, names(199))                          # vecchio risultato: non deve essere riusato
    monkeypatch.setattr(stages, "_colmap", _fake_colmap())
    m = asyncio.run(stages.stage_mapping(Ctx(p, paths, p.stage("mapping"), lambda: None)))
    assert m["registered"] == 180


def test_refine_that_loses_cameras_falls_back_to_the_mapper_model(tmp_path, monkeypatch):
    p, paths = project(tmp_path, monkeypatch)
    write_model(paths.sparse / "0", names(180))
    monkeypatch.setattr(stages, "_colmap", _fake_colmap(refine_keeps=3))
    r = asyncio.run(stages.stage_refine(Ctx(p, paths, p.stage("refine"), lambda: None)))
    assert r["registered"] == 180


def test_a_useless_reconstruction_stops_the_pipeline(tmp_path, monkeypatch):
    p, paths = project(tmp_path, monkeypatch)
    write_model(paths.sparse / "0", names(2))
    monkeypatch.setattr(stages, "_colmap", _fake_colmap())
    with pytest.raises(RuntimeError, match="solo 2 camere su 200"):
        asyncio.run(stages.stage_refine(Ctx(p, paths, p.stage("refine"), lambda: None)))
