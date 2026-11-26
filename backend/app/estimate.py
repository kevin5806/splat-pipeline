"""Stima dei tempi di un progetto.

I coefficienti vengono dalle misure fatte su una Tesla P4 con 20 core (Xeon E5-2620 v2), vedi docs/LESSONS.md:

    frame estratti da un 4K    ~125 s per 13.242 frame (decodifica)     feature COLMAP       ~0,15 s/frame
    matching vocab_tree        ~25 min a 828 frame, cresce ~ n^1,6      mapper               ~18 min a 828, ~ n^2
    bundle adjustment          ~65 min a 1.666 camere, ~ n^2            test 4000 passi      ~10 min
    training 30.000 passi      87-112 min                               export + ritaglio    ~4 min

Sono ordini di grandezza, non previsioni: le fasi di COLMAP crescono più che proporzionalmente e dipendono dalle CPU.
Dopo ogni fase completata la stima si ricalibra sul rapporto tra tempo reale e tempo stimato di quel progetto.
"""
from __future__ import annotations

import math
import re
from typing import Optional

from .models import STAGES, Project
from .stages import compute_steps

REFERENCE = "Tesla P4 + 20 core"
CPU_STAGES = ("probe", "frames", "features", "matching", "mapping", "refine", "recover", "convert")
GPU_STAGES = ("test", "train", "export")
SPREAD = {  # (minimo, massimo) rispetto alla stima centrale
    "probe": (0.8, 1.5), "frames": (0.7, 1.5), "features": (0.7, 1.6), "convert": (0.7, 1.6),
    "matching": (0.6, 1.9), "mapping": (0.6, 1.9), "refine": (0.6, 1.9),
    "recover": (0.0, 2.5),
    "test": (0.85, 1.3), "train": (0.85, 1.3), "export": (0.8, 1.4),
}
LONG_HOURS = 12.0  # oltre questa stima si avvisa
RAM_FACTOR = 2.2


def _gpu_factor(downscale: int) -> float:
    """Il costo per passo cresce con i pixel: dimezzare la riduzione (2 -> 1) lo porta a ~2,8 volte."""
    return (2.0 / max(1, downscale)) ** 1.5


def video_infos(p: Project) -> Optional[dict]:
    """Dati dei video (da analisi completata, oppure dalla lettura fatta al caricamento). None se mancano."""
    probe = p.stage("probe").metrics.get("videos") or {}
    out = {}
    for v in p.videos:
        info = probe.get(v.id) or v.info
        if not info or not info.get("total_frames"):
            return None
        out[v.id] = info
    return out or None


def frames_per_video(p: Project, infos: dict) -> dict:
    steps = compute_steps(p.videos, {vid: x["total_frames"] for vid, x in infos.items()}, p.settings)
    return {vid: math.ceil(infos[vid]["total_frames"] / steps[vid]) for vid in infos}


def _mem_total_gb() -> Optional[float]:
    from . import system
    return system.memory_limit_gb()


def preview(settings, videos: list[dict]) -> Optional[dict]:
    """Stima per dei video non ancora caricati, da durata, risoluzione e fps letti nel browser (modulo "Nuovo progetto")."""
    from .models import Video
    vids = []
    for i, v in enumerate(videos):
        fps = float(v.get("fps") or 30.0)
        dur = float(v["duration_s"])
        info = {"width": int(v["width"]), "height": int(v["height"]), "fps": fps, "duration_s": dur,
                "total_frames": max(1, round(dur * fps))}
        vids.append(Video(id=f"v{i + 1}", filename=v.get("name") or f"video{i + 1}", device=v.get("device") or 1,
                          camera_model=v.get("camera_model") or None, frame_step=v.get("frame_step") or None, info=info))
    return project_estimate(Project(name="anteprima", settings=settings, videos=vids)) if vids else None


def stage_estimates(p: Project, infos: dict, n: int) -> dict:
    """Secondi centrali per fase (tarati sull'hardware di riferimento) e se la misura è affidabile."""
    s = p.settings
    ratio = n / 828.0
    pixel_factor = lambda i: (i["width"] * i["height"]) / (3840 * 2160)  # noqa: E731
    est: dict[str, tuple[float, str]] = {}
    est["probe"] = (2.0 * len(infos), "misurato")
    est["frames"] = (sum(x["total_frames"] / 13242 * 125 * max(0.3, pixel_factor(x)) for x in infos.values()), "misurato")
    est["features"] = (0.15 * n, "misurato")
    if s.matcher == "vocab_tree":
        est["matching"] = (1500 * ratio ** 1.6, "misurato" if 400 <= n <= 2500 else "estrapolato")
    elif s.matcher == "sequential":
        est["matching"] = (0.5 * n, "ipotesi")
    else:
        est["matching"] = (6000 * ratio ** 2, "ipotesi")
    est["mapping"] = (1080 * ratio ** 2, "misurato" if 400 <= n <= 2500 else "estrapolato")
    est["refine"] = (960 * ratio ** 2, "misurato" if 400 <= n <= 2500 else "estrapolato")
    # recupero dei tratti deboli: dipende da quanti ce ne sono, che si scopre solo dopo la ricostruzione
    plan = p.stage("recover").metrics.get("plan") or {}
    if plan.get("est_seconds"):
        rec = float(plan["est_seconds"])
    else:
        rec = 0.0 if s.recovery == "off" else 0.25 * (est["matching"][0] + est["mapping"][0])
    est["recover"] = (rec, "ipotesi")
    est["convert"] = (40 + 0.06 * n, "misurato")
    g = _gpu_factor(s.downscale)
    est["test"] = (0.0 if s.skip_test else 120 + 0.14 * s.test_steps * g, "misurato")
    est["train"] = (150 + 0.19 * s.steps * g, "misurato" if s.downscale == 2 else "estrapolato")
    est["export"] = (230.0, "misurato")
    return est


def _parse_eta(detail: str) -> Optional[float]:
    """'ETA 1 h, 5 m, 39 s' -> secondi."""
    m = re.match(r"ETA\s+(.*)", detail or "")
    if not m:
        return None
    total, found = 0.0, False
    for val, unit in re.findall(r"(\d+)\s*(h|m|s)\b", m.group(1)):
        total += int(val) * {"h": 3600, "m": 60, "s": 1}[unit]
        found = True
    return total if found else None


def project_estimate(p: Project, now: Optional[float] = None) -> Optional[dict]:
    if p.external or not p.videos:
        return None
    infos = video_infos(p)
    if not infos:
        return None
    import time as _t
    now = now or _t.time()
    per = frames_per_video(p, infos)
    n = sum(per.values())
    base = stage_estimates(p, infos, n)

    # ricalibrazione: rapporto tra tempo reale e stimato, separato per fasi CPU e GPU
    scale = {"cpu": 1.0, "gpu": 1.0}
    for group, ids in (("cpu", CPU_STAGES), ("gpu", GPU_STAGES)):
        act = est = 0.0
        for sid in ids:
            st = p.stage(sid)
            if sid != "recover" and st.status == "done" and st.started and st.ended and not st.metrics.get("skipped") and not st.metrics.get("forced") \
                    and st.ended > st.started and base[sid][0] > 20:
                act += st.ended - st.started
                est += base[sid][0]
        if est > 30:
            scale[group] = max(0.4, min(3.0, act / est))

    stages: dict[str, dict] = {}
    rem_c = rem_lo = rem_hi = 0.0
    tot_c = tot_lo = tot_hi = 0.0
    for sid, _label in STAGES:
        st = p.stage(sid)
        group = "cpu" if sid in CPU_STAGES else "gpu"
        c, basis = base[sid]
        c *= scale[group]
        lo_m, hi_m = SPREAD[sid]
        lo, hi = c * lo_m, c * hi_m
        if st.status == "done":
            r = r_lo = r_hi = 0.0
        elif st.status == "running":
            eta = _parse_eta(st.detail)
            elapsed = max(0.0, now - (st.started or now))
            if eta is not None:
                r = eta
            elif st.progress > 3:
                r = c * (1 - st.progress / 100)
            else:
                r = max(c * 0.1, c - elapsed)
            r_lo, r_hi = r * lo_m, r * hi_m
        else:
            r, r_lo, r_hi = c, lo, hi
        stages[sid] = {"est": round(c), "low": round(lo), "high": round(hi), "remaining": round(r), "basis": basis}
        rem_c, rem_lo, rem_hi = rem_c + r, rem_lo + r_lo, rem_hi + r_hi
        tot_c, tot_lo, tot_hi = tot_c + c, tot_lo + lo, tot_hi + hi

    warnings: list[str] = []
    long_s = rem_c if rem_c > 0 else tot_c
    if long_s / 3600 > LONG_HOURS:
        warnings.append(
            f"Tempo molto lungo: circa {_fmt_h(rem_c)} (tra {_fmt_h(rem_lo)} e {_fmt_h(rem_hi)}) con {n} frame. COLMAP cresce più che "
            "proporzionalmente con i frame: con meno frame (passo più alto o «Frame totali desiderati») si accorcia moltissimo.")
    first = next(iter(infos.values()))
    # RAM vera del training: circa il doppio delle sole immagini in cache. Misurato: 2.694 frame a 1080p, circa 37-40 GB
    # le immagini del training comprendono anche i frame aggiunti dal recupero, che nel conto per passo non ci sono
    images = p.stage("convert").metrics.get("frames_in_dataset") or p.stage("recover").metrics.get("total") or n
    cache_gb = RAM_FACTOR * max(images, 0) * first["width"] * first["height"] * 3 / (max(1, p.settings.downscale) ** 2) / 1e9
    mem = _mem_total_gb()
    if p.settings.cache_images == "cpu" and mem and cache_gb > 0.7 * mem:
        warnings.append(
            f"Memoria: il training userà circa {cache_gb:.0f} GB di RAM su {mem:.0f} disponibili. Rischio di "
            "esaurirla dopo ore di calcolo: riduci i frame o aumenta il fattore di riduzione.")
    elif n > 3000:
        warnings.append(f"{n} frame sono molti: oltre i ~2.000 le fasi di COLMAP diventano lentissime e non sono mai state misurate qui.")

    return {
        "frames": n, "per_video": per, "reference": REFERENCE, "calibration": {k: round(v, 2) for k, v in scale.items()},
        "stages": stages,
        "remaining": {"est": round(rem_c), "low": round(rem_lo), "high": round(rem_hi)},
        "total": {"est": round(tot_c), "low": round(tot_lo), "high": round(tot_hi)},
        "warnings": warnings,
    }


def _fmt_h(s: float) -> str:
    h = s / 3600
    if h >= 1.5:
        return f"{h:.0f} ore" if h >= 10 else f"{h:.1f} ore"
    return f"{max(1, round(s / 60))} min"
