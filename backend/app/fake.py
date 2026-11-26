"""Fasi simulate (SPLAT_FAKE=1): permettono di sviluppare e provare il pannello senza GPU."""
from __future__ import annotations

import asyncio
import os
from typing import Awaitable, Callable

from . import config
from .stages import Blocked, Ctx, compute_steps


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
    return {"width": 3840, "height": 2160, "fps": 30.0, "duration_s": round(duration, 1), "total_frames": total, "rotation": -90}


def fake_info(i: int) -> dict:
    v = _fake_video(i)
    return {k: v[k] for k in ("width", "height", "fps", "duration_s", "total_frames")}


async def probe(c: Ctx) -> dict:
    await _tick(c, 4, 1.0, "analisi")
    videos = {v.id: _fake_video(i) for i, v in enumerate(c.project.videos)}
    steps = compute_steps(c.project.videos, {vid: x["total_frames"] for vid, x in videos.items()}, c.s)
    for vid, x in videos.items():
        x["step"] = steps[vid]
        x["expected_frames"] = -(-x["total_frames"] // steps[vid])
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


async def recover(c: Ctx) -> dict:
    """Con SPLAT_FAKE_WEAK=1 simula tratti deboli: chiede conferma, poi (dopo il giro) chiede un video nuovo."""
    c.stage.metrics = {}
    if os.environ.get("SPLAT_FAKE_WEAK") != "1" or c.s.recovery == "off":
        return {"skipped": True}
    weak_list = [{"video": c.project.videos[0].id, "video_name": c.project.videos[0].name, "start_s": 22.0, "end_s": 26.5,
                  "seconds": 4.5, "thumb": None}]
    supported = any(v.support for v in c.project.videos)
    vid = c.project.videos[0].id

    def report(*sections):
        return {vid: {"original": {"total": 828, "before": 637, "now": 637 if sections[0][0] == "pending" else 690},
                      "added": {"total": 0 if sections[0][0] == "pending" else 48,
                                "registered": 0 if sections[0][0] == "pending" else 41},
                      "support": False,
                      "sections": [{"status": s, "start_s": a, "end_s": b, "before_s": before, "after_s": after}
                                   for s, a, b, before, after in sections]}}
    if c.s.recovery == "ask" and c.project.recovery_approved == 0 and not supported:
        c.stage.metrics = {"decision": "approve", "weak": weak_list, "weak_seconds": 4.5, "rounds": [],
                           "report": report(("pending", 22.0, 26.5, 4.5, 4.5)),
                           "plan": {"kind": "densify", "number": 1, "extra_frames": 48, "step": {"v1": 4}, "est_seconds": 900}}
        raise Blocked("Trovati 4 s di video poco coperti (simulato).")
    await _tick(c, 10, 2.0, "recupero")
    if not supported:
        c.stage.metrics = {"decision": "new_video", "weak": weak_list, "weak_seconds": 4.5,
                           "report": report(("fixed", 10.0, 12.0, 2.0, 0.0), ("improved", 60.0, 68.0, 8.0, 2.4),
                                            ("unchanged", 276.0, 281.0, 5.0, 4.5)),
                           "rounds": [{"number": 1, "kind": "densify", "extra_frames": 48, "new_registered": 3, "accepted": True}]}
        raise Blocked("Serve un video nuovo di quei punti (simulato).")
    return {"weak": [], "weak_seconds": 0, "rounds": []}


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
    "recover": recover,
    "convert": _make(2.0, "conversione", {"frames_in_dataset": 1421}),
    "test": test,
    "train": train,
    "export": export,
}
