"""Versioni del modello: rifare il training non sovrascrive mai un risultato precedente."""
import json

import pytest
from fastapi.testclient import TestClient

from app import config, disk, store, versions, viewcfg
from app.stages import keep_previous_results, reset_markers
from app.store import ProjectPaths


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(config, "FAKE", True)
    monkeypatch.setattr(config, "EMBEDDED_WORKER", False)
    store.create("vv")


def train_result(tag: str, gaussians: int, psnr: float, thresh="0.0008"):
    """Lascia nel progetto quello che lasciano training ed esportazione."""
    paths = ProjectPaths("vv")
    run = paths.outputs / "main" / "splatfacto" / "run1"
    run.mkdir(parents=True, exist_ok=True)
    (run / "config.yml").write_text(f"max_num_iterations: 30000\n    densify_grad_thresh: {thresh}\n    reset_alpha_every: 76\n")
    (paths.outputs / "main" / "eval.json").write_text(json.dumps({"results": {"psnr": psnr, "ssim": 0.9, "lpips": 0.25}}))
    paths.export.mkdir(parents=True, exist_ok=True)
    (paths.export / "splat_clean.ply").write_bytes(tag.encode() * 100)
    (paths.export / "splat_clean.json").write_text(json.dumps({"kept": gaussians, "center": [0, 0, 0]}))
    final = paths.root / "quality" / "final"
    final.mkdir(parents=True, exist_ok=True)
    (final / "views.json").write_text(json.dumps({"count": 269, "mean": psnr, "views": [], "tag": tag}))
    return paths


def test_redoing_the_training_many_times_keeps_every_version():
    for k, (tag, g, psnr) in enumerate((("a", 308015, 22.2), ("b", 900000, 23.5), ("c", 950000, 23.9)), start=1):
        paths = train_result(tag, g, psnr)
        reset_markers(paths, "train")
        assert versions.numbers(paths) == list(range(1, k + 1))
        assert not (paths.outputs / "main").exists() and not (paths.export / "splat_clean.ply").exists()
    got = versions.listing("vv")
    assert got["current"] is None
    assert [(v["number"], v["gaussians"], v["psnr"]) for v in got["versions"]] == [(1, 308015, 22.2), (2, 900000, 23.5), (3, 950000, 23.9)]
    for number, tag in ((1, "a"), (2, "b"), (3, "c")):                      # ognuna col SUO splat, confronto e modello
        assert (versions.export_dir(paths, number) / "splat_clean.ply").read_bytes() == tag.encode() * 100
        assert json.loads((versions.quality_dir(paths, number) / "views.json").read_text())["tag"] == tag
        assert (versions.model_dir(paths, number) / "splatfacto" / "run1" / "config.yml").exists()
    v1 = got["versions"][0]
    assert v1["steps"] == 30000 and v1["quality"] == "standard" and v1["reset_every"] == 7600 and v1["ssim"] == 0.9
    train_result("d", 1_100_000, 24.0, thresh="0.0005")
    current = versions.listing("vv")["current"]
    assert current["number"] == 4 and current["quality"] == "high" and current["file"] == "splat_clean.ply"


def test_a_training_that_never_exported_does_not_create_or_destroy_versions():
    paths = train_result("a", 10, 20.0)
    reset_markers(paths, "train")
    (paths.outputs / "main").mkdir(parents=True)                     # training interrotto, niente splat
    reset_markers(paths, "train")
    reset_markers(paths, "train")
    assert versions.numbers(paths) == [1] and (paths.outputs / "main.prev").exists()


def test_redoing_only_the_export_keeps_the_trained_model():
    paths = train_result("a", 10, 20.0)
    keep_previous_results(paths, "export")
    assert (paths.outputs / "main").exists() and versions.numbers(paths) == [1]
    assert not versions.listing("vv")["versions"][0]["has_model"]


def test_the_previous_model_of_the_old_system_becomes_version_one():
    """Il caso del progetto reale: il 'precedente' c'è già, e intanto sta girando un training nuovo."""
    paths = ProjectPaths("vv")
    paths.export.mkdir(parents=True, exist_ok=True)
    (paths.export / "splat_clean_precedente.ply").write_bytes(b"vecchio")
    (paths.export / "splat_clean_precedente.json").write_text(json.dumps({"kept": 308015}))
    (paths.export / "splat_clean_precedente.dataparser.json").write_text("{}")
    (paths.root / "quality" / "previous").mkdir(parents=True)
    (paths.root / "quality" / "previous" / "views.json").write_text(json.dumps({"count": 269, "mean": 22.21}))
    (paths.outputs / "main.prev").mkdir(parents=True)
    (paths.outputs / "main.prev" / "eval.json").write_text(json.dumps({"results": {"psnr": 22.2111, "ssim": 0.8982, "lpips": 0.2503}}))
    running = paths.outputs / "main"                                 # il training in corso non si deve toccare
    running.mkdir(parents=True)
    (running / "checkpoint").write_bytes(b"in corso")
    got = versions.listing("vv")
    v = got["versions"][0]
    assert (v["number"], v["gaussians"], v["psnr"], v["ssim"], v["lpips"], v["views"]) == (1, 308015, 22.21, 0.898, 0.25, 269)
    assert (versions.export_dir(paths, 1) / "splat_clean.dataparser.json").exists() and v["has_comparison"] and v["has_model"]
    assert (running / "checkpoint").read_bytes() == b"in corso"
    assert versions.listing("vv")["versions"] == got["versions"]     # ripetibile
    train_result("nuovo", 950000, 23.0)                              # finisce il training in corso
    reset_markers(paths, "train")
    assert versions.numbers(paths) == [1, 2]
    assert (versions.export_dir(paths, 1) / "splat_clean.ply").read_bytes() == b"vecchio"


def test_deleted_numbers_are_never_reused():
    paths = train_result("a", 1, 20.0)
    reset_markers(paths, "train")
    train_result("b", 2, 21.0)
    reset_markers(paths, "train")
    versions.delete("vv", 2)
    assert versions.next_number(paths) == 3 and versions.numbers(paths) == [1]
    with pytest.raises(KeyError):
        versions.delete("vv", 2)


def test_versions_are_never_in_the_general_cleanup_except_their_heavy_models():
    paths = train_result("a", 1, 20.0)
    reset_markers(paths, "train")
    p = store.load("vv")
    items = disk.cleanable(p, paths)
    assert "version_models" in items
    disk.clean(p, ["version_models"])
    assert (versions.export_dir(paths, 1) / "splat_clean.ply").exists() and (versions.quality_dir(paths, 1) / "views.json").exists()


def test_old_versions_can_be_aligned_with_the_current_model():
    paths = train_result("a", 1, 20.0)
    reset_markers(paths, "train")
    train_result("b", 2, 21.0)
    assert viewcfg.alignment("vv", "versions/v1/splat_clean.ply") is not None


def test_api_lists_serves_annotates_and_deletes_versions():
    paths = train_result("a", 1, 20.0)
    reset_markers(paths, "train")
    train_result("b", 2, 21.0)
    from app.main import app
    with TestClient(app) as c:
        got = c.get("/api/projects/vv/versions").json()
        assert got["current"]["number"] == 2 and [v["number"] for v in got["versions"]] == [1]
        assert set(c.get("/api/projects/vv/quality").json()) == {"final", "v1"}
        r = c.get(f"/api/projects/vv/files/{got['versions'][0]['file']}")
        assert r.status_code == 200 and r.content == b"a" * 100
        assert all(not a["path"].startswith("versions/") for a in c.get("/api/projects/vv").json()["artifacts"])
        assert "versions/v1/splat_clean.ply" in c.get("/api/projects/vv/view").json()["models"]
        assert c.patch("/api/projects/vv/versions/1", json={"note": "prima della correzione"}).status_code == 200
        assert c.get("/api/projects/vv/versions").json()["versions"][0]["note"] == "prima della correzione"
        assert c.get("/api/projects/vv/quality/v1x/a.jpg").status_code == 404
        assert c.delete("/api/projects/vv/versions/9").status_code == 404
        assert c.delete("/api/projects/vv/versions/1").status_code == 200
        assert c.get("/api/projects/vv/versions").json()["versions"] == []
