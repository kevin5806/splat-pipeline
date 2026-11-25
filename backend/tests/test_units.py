import asyncio
import sys
import time

import numpy as np
import pytest

from app import runner
from app.models import Project, Settings
from app.stages import _rx_progress, pick_best_model
from app.tools import crop as crop_tool


def test_project_name_validation():
    Project(name="camera-1")
    for bad in ("", "../x", "a b", "-x", "x" * 41, "a/b"):
        with pytest.raises(ValueError):
            Project(name=bad)


def test_settings_bounds():
    with pytest.raises(ValueError):
        Settings(frame_step=0)
    assert Settings().gate_psnr == 18.5 and Settings().steps == 30000


def test_progress_parsing():
    line = "13700 (45.67%)      204.310 ms           55 m, 30 s           10.21 M       "
    assert _rx_progress(line) == (45.67, "55 m, 30 s")
    assert _rx_progress("18850 (62.83%)") == (62.83, None)
    assert _rx_progress("niente di utile") is None


def test_pick_best_model(tmp_path):
    (tmp_path / "0").mkdir(); (tmp_path / "1").mkdir()
    (tmp_path / "0" / "images.bin").write_bytes(b"x" * 100)
    (tmp_path / "1" / "images.bin").write_bytes(b"x" * 500)
    assert pick_best_model(tmp_path).name == "1"
    with pytest.raises(RuntimeError):
        pick_best_model(tmp_path / "vuoto")


def _write_ply(path, xyz):
    n = len(xyz)
    props = ["x", "y", "z", "opacity", "f_dc_0"]
    header = "ply\nformat binary_little_endian 1.0\nelement vertex %d\n%s\nend_header\n" % (
        n, "\n".join(f"property float {p}" for p in props))
    arr = np.zeros((n, len(props)), dtype="<f4")
    arr[:, :3] = xyz
    arr[:, 3] = 1.0
    arr[:, 4] = np.arange(n)  # un valore per riconoscere le righe
    path.write_bytes(header.encode() + arr.tobytes())


def test_crop_removes_far_floaters_and_recenters(tmp_path):
    rng = np.random.default_rng(0)
    room = rng.normal(size=(5000, 3)).astype("f4") * 0.5 + np.array([10, 20, 30], dtype="f4")
    floaters = np.array([[500, 500, 500], [-400, 300, 200], [10, 20, 900]], dtype="f4")
    src, dst = tmp_path / "in.ply", tmp_path / "out.ply"
    _write_ply(src, np.vstack([room, floaters]))
    info = crop_tool.crop(src, dst, 3.5)
    assert info["removed"] == 3 and info["kept"] == 5000
    _, data = crop_tool.read_ply(dst)
    assert len(data) == 5000
    xyz = np.stack([data["x"], data["y"], data["z"]], 1)
    assert np.abs(np.median(xyz, axis=0)).max() < 0.05          # ricentrato
    assert float(np.abs(xyz).max()) < 10                        # nessun floater rimasto
    assert set(data["f_dc_0"][:5]) <= set(np.arange(5003, dtype="f4"))  # gli altri attributi sono intatti


def test_runner_progress_lines_and_exit_code(tmp_path):
    seen = []
    code = asyncio.run(runner.run_cmd(
        [sys.executable, "-c", "import sys;sys.stdout.write('10 (10.00%)\\r20 (20.00%)\\rfine\\n')"],
        tmp_path / "x.log", on_line=seen.append))
    assert code == 0 and seen == ["10 (10.00%)", "20 (20.00%)", "fine"]
    with pytest.raises(runner.CmdError):
        asyncio.run(runner.run_cmd([sys.executable, "-c", "raise SystemExit(3)"], tmp_path / "y.log"))


@pytest.mark.skipif(sys.platform == "win32", reason="gruppi di processi POSIX")
def test_runner_cancel_kills_process(tmp_path):
    async def go():
        t = asyncio.create_task(runner.run_cmd([sys.executable, "-c", "import time;time.sleep(60)"], tmp_path / "z.log"))
        await asyncio.sleep(0.5)
        t.cancel()
        start = time.time()
        with pytest.raises(asyncio.CancelledError):
            await t
        return time.time() - start
    assert asyncio.run(go()) < 8
