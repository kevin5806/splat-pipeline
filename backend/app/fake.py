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


async def probe(c: Ctx) -> dict:
    await _tick(c, 4, 1.0, "analisi")
    return {"width": 3840, "height": 2160, "fps": 30.0, "duration_s": 441.3, "total_frames": 13242, "rotation": -90, "expected_frames": 828}


async def refine(c: Ctx) -> dict:
    await _tick(c, 20, 3.0, "bundle adjustment")
    return {"registered": 1421, "total": 1842, "ratio": 0.771}


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
    "frames": _make(3.0, "frame", {"frames": 828}),
    "features": _make(3.0, "feature"),
    "matching": _make(4.0, "matching"),
    "mapping": _make(5.0, "camere"),
    "refine": refine,
    "convert": _make(2.0, "conversione", {"frames_in_dataset": 1421}),
    "test": test,
    "train": train,
    "export": export,
}
