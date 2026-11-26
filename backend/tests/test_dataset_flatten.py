"""Regressione: con più video il training falliva con 'No such file: dataset/images_2/frame_00001.jpg'.

Nerfstudio cerca images_<n>/<nome file> ignorando le sottocartelle, e i due video hanno frame con lo stesso nome.
"""
import asyncio
import json
from pathlib import Path

import pytest

from app import config
from app.models import Project, Settings, Video
from app.stages import Ctx, stage_convert
from app.store import ProjectPaths
from app.tools.flatten import flatten_dataset, missing_training_images


def nerfstudio_opens(dataset: Path, downscale: int) -> list[Path]:
    """La regola del lettore di Nerfstudio (_get_fname): con downscale > 1 conta solo il NOME del file."""
    frames = json.loads((dataset / "transforms.json").read_text())["frames"]
    return [dataset / f"images_{downscale}" / Path(f["file_path"]).name if downscale > 1 else dataset / f["file_path"]
            for f in frames]


def setup(tmp_path, monkeypatch, per_video=3, downscale=2):
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path)
    p = Project(name="fl", settings=Settings(downscale=downscale),
                videos=[Video(id="v1", filename="a.mp4"), Video(id="v2", filename="b.mp4")])
    paths = ProjectPaths("fl")
    paths.ensure()
    names = []
    for vid in ("v1", "v2"):
        (paths.frames / vid).mkdir(parents=True)
        for i in range(1, per_video + 1):
            (paths.frames / vid / f"frame_{i:05d}.jpg").write_bytes(f"{vid}-{i}".encode())
            names.append(f"{vid}/frame_{i:05d}.jpg")
    return p, paths, names


def fake_cmd(paths, names):
    """Imita la conversione di Nerfstudio (transforms.json con le sottocartelle) e il ridimensionamento di ffmpeg."""
    async def cmd(self, args, **kw):
        args = [str(a) for a in args]
        if "app.tools.convert" in args:
            paths.dataset.mkdir(parents=True, exist_ok=True)
            (paths.dataset / "transforms.json").write_text(json.dumps(
                {"camera_model": "OPENCV", "frames": [{"file_path": f"images/{n}", "transform_matrix": []} for n in names]}))
        elif args[0] == "ffmpeg":
            src = Path(args[args.index("-i") + 1])
            Path(args[-1]).write_bytes(b"small:" + src.read_bytes())
        return 0
    return cmd


def test_two_videos_with_the_same_frame_names_end_up_unique_and_readable(tmp_path, monkeypatch):
    p, paths, names = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(Ctx, "cmd", fake_cmd(paths, names))
    r = asyncio.run(stage_convert(Ctx(p, paths, p.stage("convert"), lambda: None)))
    assert r == {"frames_in_dataset": 6}
    opened = nerfstudio_opens(paths.dataset, 2)
    assert len({o.name for o in opened}) == 6                                   # nessuna collisione tra v1 e v2
    assert all(o.is_file() for o in opened)                                     # tutto ciò che Nerfstudio apre esiste
    assert (paths.dataset / "images_2" / "v2__frame_00001.jpg").read_bytes() == b"small:v2-1"   # contenuto del video giusto
    assert (paths.dataset / "images" / "v1__frame_00002.jpg").read_bytes() == b"v1-2"
    assert (paths.frames / "v1" / "frame_00001.jpg").read_bytes() == b"v1-1"   # i frame originali restano intatti


def test_full_resolution_uses_the_images_folder(tmp_path, monkeypatch):
    p, paths, names = setup(tmp_path, monkeypatch, downscale=1)
    monkeypatch.setattr(Ctx, "cmd", fake_cmd(paths, names))
    asyncio.run(stage_convert(Ctx(p, paths, p.stage("convert"), lambda: None)))
    assert all(o.is_file() for o in nerfstudio_opens(paths.dataset, 1))


def test_running_convert_twice_is_safe_and_keeps_the_original_frames(tmp_path, monkeypatch):
    p, paths, names = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(Ctx, "cmd", fake_cmd(paths, names))
    for _ in range(2):
        asyncio.run(stage_convert(Ctx(p, paths, p.stage("convert"), lambda: None)))
    assert sum(1 for _ in paths.frames.rglob("*.jpg")) == 6
    flatten_dataset(paths.dataset, paths.frames)                                # anche su un dataset già piatto
    assert not missing_training_images(paths.dataset, 2)


def test_convert_fails_early_if_a_training_image_is_missing(tmp_path, monkeypatch):
    p, paths, names = setup(tmp_path, monkeypatch)
    good = fake_cmd(paths, names)

    async def broken(self, args, **kw):
        if str(args[0]) == "ffmpeg" and str(args[-1]).endswith("v2__frame_00002.jpg"):
            return 0                                                            # ffmpeg "dimentica" un'immagine
        return await good(self, args, **kw)
    monkeypatch.setattr(Ctx, "cmd", broken)
    with pytest.raises(RuntimeError, match="mancano 1 immagini su 6"):
        asyncio.run(stage_convert(Ctx(p, paths, p.stage("convert"), lambda: None)))


def test_missing_source_frame_is_reported(tmp_path):
    ds, frames = tmp_path / "ds", tmp_path / "frames"
    ds.mkdir()
    frames.mkdir()
    (ds / "transforms.json").write_text(json.dumps({"frames": [{"file_path": "images/v1/frame_00001.jpg"}]}))
    with pytest.raises(FileNotFoundError):
        flatten_dataset(ds, frames)
