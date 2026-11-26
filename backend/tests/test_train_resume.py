"""Regressione: con --load-dir ns-train AGGIUNGE i passi richiesti a quelli già fatti.

Misurato sul vero Nerfstudio: ripreso dal passo 600 chiedendo 900 passi, si è fermato a 1500.
"""
import asyncio

import pytest

from app import config
from app.models import Project, Settings, Video
from app.stages import Ctx, _on_resumable_train_line, latest_checkpoint_step, stage_train
from app.store import ProjectPaths


def setup(tmp_path, monkeypatch, steps=30000):
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path)
    p = Project(name="tr", settings=Settings(steps=steps), videos=[Video(id="v1", filename="a.mp4")])
    paths = ProjectPaths("tr")
    paths.ensure()
    ck = paths.outputs / "main" / "splatfacto" / "run1" / "nerfstudio_models"
    calls = []

    async def cmd(self, args, **kw):
        args = [str(a) for a in args]
        calls.append(args)
        n = int(args[args.index("--max-num-iterations") + 1])
        start = (latest_checkpoint_step(ck) + 1) if "--load-dir" in args else 0
        ck.mkdir(parents=True, exist_ok=True)
        for f in ck.glob("*.ckpt"):
            f.unlink()
        (ck / f"step-{start + n - 1:09d}.ckpt").write_bytes(b"x")     # come il vero ns-train: passi AGGIUNTI
        return 0
    monkeypatch.setattr(Ctx, "cmd", cmd)
    return p, paths, ck, calls


def run(p, paths):
    return asyncio.run(stage_train(Ctx(p, paths, p.stage("train"), lambda: None)))


def opt(args, name):
    return args[args.index(name) + 1]


def test_fresh_training_asks_for_all_steps(tmp_path, monkeypatch):
    p, paths, ck, calls = setup(tmp_path, monkeypatch)
    assert run(p, paths) == {}
    assert opt(calls[0], "--max-num-iterations") == "30000" and "--load-dir" not in calls[0]
    assert latest_checkpoint_step(ck) == 29999


def test_resume_asks_only_for_the_missing_steps_and_ends_at_the_target(tmp_path, monkeypatch):
    p, paths, ck, calls = setup(tmp_path, monkeypatch)
    ck.mkdir(parents=True)
    (ck / "step-000019999.ckpt").write_bytes(b"x")
    assert run(p, paths) == {"resumed_from": 20000}
    assert opt(calls[0], "--max-num-iterations") == "10000" and "--load-dir" in calls[0]
    assert latest_checkpoint_step(ck) == 29999                         # non 49999


def test_completed_training_is_not_run_again(tmp_path, monkeypatch):
    p, paths, ck, calls = setup(tmp_path, monkeypatch, steps=1000)
    ck.mkdir(parents=True)
    (ck / "step-000000999.ckpt").write_bytes(b"x")
    assert run(p, paths) == {"resumed_from": 1000}
    assert calls == []


def test_progress_after_resume_is_relative_to_the_total(tmp_path, monkeypatch):
    p, paths, *_ = setup(tmp_path, monkeypatch, steps=900)
    ctx = Ctx(p, paths, p.stage("train"), lambda: None)
    # dopo la ripresa ns-train stampa il passo assoluto con una percentuale calcolata sui soli passi richiesti (>100%)
    _on_resumable_train_line(ctx, 900)("700 (233.33%)      180.000 ms           36 s       10.0 M      ")
    assert ctx.stage.progress == pytest.approx(77.8, abs=0.1)
    assert ctx.stage.detail == "ETA 36 s"
