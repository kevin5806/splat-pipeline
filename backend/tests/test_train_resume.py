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
    p, paths, *_ = setup(tmp_path, monkeypatch, steps=1000)
    ctx = Ctx(p, paths, p.stage("train"), lambda: None)
    # dopo la ripresa ns-train stampa il passo assoluto con una percentuale calcolata sui soli passi richiesti (>100%)
    _on_resumable_train_line(ctx, 1000)("700 (233.33%)      180.000 ms           36 s       10.0 M      ")
    assert ctx.stage.progress == pytest.approx(70.0, abs=0.1)
    assert ctx.stage.detail == "ETA 36 s"


def test_high_quality_uses_the_official_big_profile_only_for_the_final_training(tmp_path, monkeypatch):
    from app.stages import _train_cmd
    p, paths, *_ = setup(tmp_path, monkeypatch, steps=30000)
    ctx = Ctx(p, paths, p.stage("train"), lambda: None)
    standard = _train_cmd(ctx, "main", 30000, "viewer", 7007, None)
    assert not any(a.startswith("--pipeline.model") or a.startswith("--optimizers") for a in standard)    # invariato
    p.settings.quality = "high"
    high = _train_cmd(ctx, "main", 30000, "viewer", 7007, None)
    assert opt(high, "--pipeline.model.densify-grad-thresh") == "0.0005" and opt(high, "--pipeline.model.cull-alpha-thresh") == "0.005"
    assert high.index("--pipeline.model.densify-grad-thresh") < high.index("nerfstudio-data")   # prima del sotto-comando
    assert "--pipeline.model.densify-grad-thresh" not in _train_cmd(ctx, "test", 4000, "tensorboard", None, None)


def test_longer_training_stretches_the_schedule_on_total_steps_even_when_resuming(tmp_path, monkeypatch):
    p, paths, ck, calls = setup(tmp_path, monkeypatch, steps=60000)
    ck.mkdir(parents=True)
    (ck / "step-000039999.ckpt").write_bytes(b"x")
    run(p, paths)
    assert opt(calls[0], "--max-num-iterations") == "20000"                       # solo i passi mancanti
    assert opt(calls[0], "--pipeline.model.stop-split-at") == "30000"             # ma la pianificazione è sul totale
    assert opt(calls[0], "--optimizers.means.scheduler.max-steps") == "60000"


def test_redoing_the_training_keeps_the_previous_comparison(tmp_path, monkeypatch):
    from app.stages import keep_previous_results
    p, paths, *_ = setup(tmp_path, monkeypatch)
    final = paths.root / "quality" / "final"
    final.mkdir(parents=True)
    (final / "views.json").write_text('{"mean": 20}')
    keep_previous_results(paths, "train")                                  # senza splat: non si butta niente
    assert (final / "views.json").exists()
    paths.export.mkdir(parents=True, exist_ok=True)
    (paths.export / "splat_clean.ply").write_bytes(b"ply")
    keep_previous_results(paths, "train")
    assert not final.exists()
    assert (paths.root / "quality" / "versions" / "v1" / "views.json").read_text() == '{"mean": 20}'


# ---------------------------------------------------------------- con molte foto il training smetteva di aggiungere dettaglio
def test_densification_with_many_photos_is_not_starved():
    from app.stages import densify_passes, densify_schedule
    default = (3000, 15000)
    # il caso reale: 2.425 foto di training, solo 20 occasioni col ciclo predefinito (modello da 308.000 gaussiane)
    assert densify_passes(2425, *default) == 20
    assert densify_passes(2900, *default) == 0 and densify_passes(200, *default) > 130
    reset, stop = densify_schedule(2425, 30000)
    assert (reset, stop) == (7600, 15200) and densify_passes(2425, reset, stop) == 100
    for images in (1400, 2900, 4000, 6000):
        reset, stop = densify_schedule(images, 30000)
        assert stop <= 21000 and densify_passes(images, reset, stop) >= 30, (images, reset, stop)


def test_small_projects_keep_the_nerfstudio_defaults():
    from app.stages import densify_schedule, quality_options
    for images in (0, 200, 900):
        assert densify_schedule(images, 30000) == (3000, 15000)
    assert quality_options(Settings(densify="extended"), 900) == []
    assert "--pipeline.model.reset-alpha-every" in quality_options(Settings(densify="extended"), 901)
    # predefinito: il comportamento di Nerfstudio, anche con tantissime foto (la versione estesa ha dato un modello peggiore)
    assert quality_options(Settings(), 2425) == []


def test_the_final_training_command_adapts_to_the_dataset_also_on_resume(tmp_path, monkeypatch):
    import json
    p, paths, ck, calls = setup(tmp_path, monkeypatch, steps=30000)
    p.settings.densify = "extended"
    paths.dataset.mkdir(parents=True)
    (paths.dataset / "transforms.json").write_text(json.dumps({"frames": [{}] * 2694}))
    ck.mkdir(parents=True)
    (ck / "step-000019999.ckpt").write_bytes(b"x")
    run(p, paths)
    assert opt(calls[0], "--pipeline.model.reset-alpha-every") == "76"
    assert opt(calls[0], "--pipeline.model.stop-split-at") == "15200"
    assert calls[0].index("--pipeline.model.reset-alpha-every") < calls[0].index("nerfstudio-data")
