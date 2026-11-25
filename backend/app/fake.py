"""Fasi simulate (SPLAT_FAKE=1): permettono di sviluppare e provare il pannello senza GPU."""
from __future__ import annotations

import asyncio
import os
from typing import Awaitable, Callable

from . import config
from .stages import Blocked, Ctx


async def _tick(c: Ctx, steps: int, seconds: float, label: str) -> None:
    for i in range(1, steps + 1):
        await asyncio.sleep(seconds / steps / config.FAKE_SPEED)
        c.progress(100 * i / steps, f"{label} {i}/{steps}")


def _make(seconds: float, label: str, metrics: dict | None = None):
    async def run(c: Ctx) -> dict:
        await _tick(c, 20, seconds, label)
        return dict(metrics or {})
    return run


def _fake_video(i: int) -> dict:
    """Un video simulato: il primo è un 4K lungo, gli altri più corti (riprese di supporto)."""
    duration = 441.3 if i == 0 else 60.0 + 25 * i
    total = int(duration * 30)
    return {"width": 3840, "height": 2160, "fps": 30.0, "duration_s": round(duration, 1), "total_frames": total,
            "rotation": -90, "expected_frames": -(-total // 16)}


async def probe(c: Ctx) -> dict:
    await _tick(c, 4, 1.0, "analisi")
    videos = {v.id: _fake_video(i) for i, v in enumerate(c.project.videos)}
    first = next(iter(videos.values()))
    return {**{k: first[k] for k in ("width", "height", "fps", "duration_s", "total_frames", "rotation")},
            "video_count": len(videos), "videos": videos,
            "expected_frames": sum(x["expected_frames"] for x in videos.values())}


async def frames(c: Ctx) -> dict:
    await _tick(c, 20, 3.0, "frame")
    per = {vid: x["expected_frames"] for vid, x in c.project.stage("probe").metrics.get("videos", {}).items()}
    return {"frames": sum(per.values()), "per_video": per}


async def refine(c: Ctx) -> dict:
    await _tick(c, 20, 3.0, "bundle adjustment")
    per = {}
    for i, v in enumerate(c.project.videos):
        n = c.project.stage("probe").metrics["videos"][v.id]["expected_frames"]
        reg = int(n * (0.77 if i == 0 else 0.93))
        per[v.id] = {"registered": reg, "total": n, "ratio": round(reg / n, 3), "missing": n - reg,
                     "missing_ranges": [[22.0, 26.5, 8], [276.0, 285.0, 17]] if i == 0 else [[10.0, 12.0, 4]]}
    registered = sum(x["registered"] for x in per.values())
    total = sum(x["total"] for x in per.values())
    return {"registered": registered, "total": total, "ratio": round(registered / total, 3), "models": 1,
            "cameras": len({v.device for v in c.project.videos}), "per_video": per}


async def test(c: Ctx) -> dict:
    if c.s.skip_test:
        return {"skipped": True}
    await _tick(c, 40, 6.0, "test")
    psnr = 14.1 if os.environ.get("SPLAT_FAKE_GATE_FAIL") == "1" else 20.9
    c.stage.metrics = {"psnr": psnr, "ssim": 0.834, "lpips": 0.413, "gate": c.s.gate_psnr}
    if psnr < c.s.gate_psnr:
        raise Blocked(f"PSNR {psnr:.1f} dB sotto la soglia {c.s.gate_psnr} dB (simulato).")
    return c.stage.metrics


async def train(c: Ctx) -> dict:
    await _tick(c, 60, 12.0, "training")
    return {}


async def export(c: Ctx) -> dict:
    await _tick(c, 10, 2.0, "export")
    c.paths.export.mkdir(parents=True, exist_ok=True)
    (c.paths.export / "splat_clean.ply").write_bytes(b"ply\nformat binary_little_endian 1.0\nelement vertex 0\nend_header\n")
    return {"ply": "export/splat_clean.ply", "size_mb": 0.0}


FAKE: dict[str, Callable[[Ctx], Awaitable[dict]]] = {
    "probe": probe,
    "frames": frames,
    "features": _make(3.0, "feature"),
    "matching": _make(4.0, "matching"),
    "mapping": _make(5.0, "camere"),
    "refine": refine,
    "convert": _make(2.0, "conversione", {"frames_in_dataset": 1421}),
    "test": test,
    "train": train,
    "export": export,
}
