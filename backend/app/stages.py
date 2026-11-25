"""Le fasi della pipeline. Ognuna è idempotente: rilanciarla dopo un'interruzione è sicuro."""
from __future__ import annotations

import asyncio
import json
import math
import os
import re
import shutil
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Optional

from . import config
from .models import Project, StageState
from .runner import CmdError, run_cmd
from .store import ProjectPaths

VOCAB_TREE_URL = "https://demuc.de/colmap/vocab_tree_flickr100K_words32K.bin"


class Blocked(Exception):
    """La fase si ferma in attesa di una decisione (es. controllo di qualità non superato)."""


@dataclass
class Ctx:
    project: Project
    paths: ProjectPaths
    stage: StageState
    flush: Callable[[], None]   # salva lo stato su disco

    @property
    def s(self):
        return self.project.settings

    @property
    def log(self) -> Path:
        return self.paths.logs / f"{self.stage.id}.log"

    def progress(self, pct: Optional[float] = None, detail: Optional[str] = None) -> None:
        if pct is not None:
            self.stage.progress = max(0.0, min(100.0, pct))
        if detail is not None:
            self.stage.detail = detail
        self.flush()

    async def cmd(self, args, **kw) -> int:
        return await run_cmd(args, self.log, **kw)


# ---------------------------------------------------------------- utilità

def count_files(d: Path, pattern: str = "*") -> int:
    return sum(1 for _ in d.glob(pattern)) if d.exists() else 0


def _rx_progress(line: str) -> Optional[tuple[float, Optional[str]]]:
    """Percentuale e ETA dalle righe di ns-train: '13700 (45.67%)  204 ms  55 m, 30 s ...'."""
    m = re.search(r"(\d+) \((\d+(?:\.\d+)?)%\)\s+[\d.]+ ms\s+(.+?)\s{2,}", line)
    if m:
        return float(m.group(2)), m.group(3).strip()
    m = re.search(r"(\d+) \((\d+(?:\.\d+)?)%\)", line)
    return (float(m.group(2)), None) if m else None


def ensure_vocab_tree() -> Path:
    dst = config.DATA_DIR / "cache" / "vocab_tree.bin"
    if not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_suffix(".part")
        urllib.request.urlretrieve(VOCAB_TREE_URL, tmp)
        tmp.replace(dst)
    return dst


def pick_best_model(sparse: Path) -> Path:
    """Il modello con più immagini (images.bin più grande): COLMAP può produrne più di uno."""
    models = [d for d in sparse.iterdir() if (d / "images.bin").exists()] if sparse.exists() else []
    if not models:
        raise RuntimeError("COLMAP non ha prodotto nessun modello: foto troppo simili, sfocate o con pochi punti riconoscibili.")
    return max(models, key=lambda d: (d / "images.bin").stat().st_size)


# ---------------------------------------------------------------- fasi reali

async def stage_probe(c: Ctx) -> dict:
    if not c.project.video:
        raise RuntimeError("Nessun video caricato per questo progetto.")
    video = c.paths.input / c.project.video
    if not video.exists():
        raise RuntimeError(f"Video non trovato: {video.name}")
    out = c.paths.logs / "probe.json"
    c.paths.logs.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_streams", "-show_format", "-of", "json", str(video),
            stdout=f, stderr=asyncio.subprocess.PIPE)
        _, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe ha fallito: {err.decode(errors='replace')[:300]}")
    info = json.loads(out.read_text())
    st = info["streams"][0]
    num, den = (st.get("r_frame_rate", "30/1").split("/") + ["1"])[:2]
    fps = float(num) / float(den or 1)
    duration = float(st.get("duration") or info.get("format", {}).get("duration") or 0)
    total = int(st.get("nb_frames") or round(duration * fps))
    rotation = 0
    for sd in st.get("side_data_list", []) or []:
        if "rotation" in sd:
            rotation = int(sd["rotation"])
    expected = math.ceil(total / c.s.frame_step) if total else 0
    return {"width": st.get("width"), "height": st.get("height"), "fps": round(fps, 2), "duration_s": round(duration, 1),
            "total_frames": total, "rotation": rotation, "expected_frames": expected}


async def stage_frames(c: Ctx) -> dict:
    video = c.paths.input / c.project.video
    expected = c.project.stage("probe").metrics.get("expected_frames") or 0
    shutil.rmtree(c.paths.frames, ignore_errors=True)
    c.paths.frames.mkdir(parents=True, exist_ok=True)

    def on_line(line: str) -> None:
        m = re.match(r"frame=(\d+)", line)
        if m and expected:
            c.progress(100 * int(m.group(1)) / expected, f"{m.group(1)} / ~{expected} frame")

    base = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-progress", "pipe:1", "-nostats", "-y", "-i", str(video), "-an",
            "-vf", f"select=not(mod(n\\,{c.s.frame_step}))"]
    out = [str(c.paths.frames / "frame_%05d.jpg")]
    # ffmpeg recente: -fps_mode; ffmpeg vecchio (immagine di Nerfstudio): -vsync
    rc = await c.cmd(base + ["-fps_mode", "vfr", "-qscale:v", "2"] + out, on_line=on_line, check=False)
    if rc != 0:
        shutil.rmtree(c.paths.frames, ignore_errors=True)
        c.paths.frames.mkdir(parents=True, exist_ok=True)
        await c.cmd(base + ["-vsync", "vfr", "-qscale:v", "2"] + out, on_line=on_line)
    n = count_files(c.paths.frames, "*.jpg")
    if n < 30:
        raise RuntimeError(f"Estratti solo {n} frame: video troppo corto o 'frame_step' troppo alto.")
    return {"frames": n}


async def _colmap(c: Ctx, args: list[str], total: int = 0, rx: Optional[str] = None, label: str = "") -> int:
    def on_line(line: str) -> None:
        if rx:
            m = re.search(rx, line)
            if m and total:
                c.progress(100 * int(m.group(1)) / total, f"{label} {m.group(1)} / {total}")
    return await c.cmd(["colmap", *args], on_line=on_line, check=False)


async def stage_features(c: Ctx) -> dict:
    n = count_files(c.paths.frames, "*.jpg")
    for f in (c.paths.database, Path(str(c.paths.database) + "-shm"), Path(str(c.paths.database) + "-wal")):
        f.unlink(missing_ok=True)
    shutil.rmtree(c.paths.sparse, ignore_errors=True)
    shutil.rmtree(c.paths.snapshots, ignore_errors=True)
    c.paths.colmap.mkdir(parents=True, exist_ok=True)
    base = ["feature_extractor", "--database_path", str(c.paths.database), "--image_path", str(c.paths.frames),
            "--ImageReader.single_camera", "1", "--ImageReader.camera_model", c.s.camera_model]
    rc = await _colmap(c, base + ["--SiftExtraction.use_gpu", "1"], n, r"Processed file \[(\d+)/", "immagini")
    if rc != 0:  # niente GPU/CUDA per COLMAP: ripiega sulla CPU (molto più lento)
        c.progress(0, "GPU non disponibile per COLMAP: uso la CPU (lento)")
        rc = await _colmap(c, base + ["--SiftExtraction.use_gpu", "0"], n, r"Processed file \[(\d+)/", "immagini (CPU)")
        if rc != 0:
            raise RuntimeError("Estrazione delle feature fallita: vedi logs/features.log")
    return {"images": n}


async def stage_matching(c: Ctx) -> dict:
    db = ["--database_path", str(c.paths.database)]
    m = c.s.matcher
    c.progress(None, f"matcher: {m} (nessuna percentuale disponibile, può richiedere molto)")
    if m == "vocab_tree":
        vt = await asyncio.to_thread(ensure_vocab_tree)
        args = ["vocab_tree_matcher", *db, "--SiftMatching.use_gpu", "1", "--VocabTreeMatching.vocab_tree_path", str(vt)]
    elif m == "sequential":
        vt = await asyncio.to_thread(ensure_vocab_tree)
        args = ["sequential_matcher", *db, "--SiftMatching.use_gpu", "1", "--SequentialMatching.overlap", "20",
                "--SequentialMatching.loop_detection", "1", "--SequentialMatching.vocab_tree_path", str(vt)]
    else:
        args = ["exhaustive_matcher", *db, "--SiftMatching.use_gpu", "1"]
    rc = await _colmap(c, args)
    if rc != 0:
        args[args.index("--SiftMatching.use_gpu") + 1] = "0"
        rc = await _colmap(c, args)
        if rc != 0:
            raise RuntimeError("Matching fallito: vedi logs/matching.log")
    return {"matcher": m}


async def stage_mapping(c: Ctx) -> dict:
    """Mapper incrementale di COLMAP, riprendibile da snapshot ogni 200 immagini."""
    n = count_files(c.paths.frames, "*.jpg")
    c.paths.sparse.mkdir(parents=True, exist_ok=True)
    c.paths.snapshots.mkdir(parents=True, exist_ok=True)
    snaps = sorted((d for d in c.paths.snapshots.iterdir() if (d / "images.bin").exists()), key=lambda d: int(d.name) if d.name.isdigit() else 0)
    args = ["mapper", "--database_path", str(c.paths.database), "--image_path", str(c.paths.frames),
            "--output_path", str(c.paths.sparse), "--Mapper.ba_global_function_tolerance=1e-6",
            "--Mapper.snapshot_path", str(c.paths.snapshots), "--Mapper.snapshot_images_freq", "200"]
    if snaps:
        args += ["--input_path", str(snaps[-1])]
        c.progress(None, f"ripresa dallo snapshot {snaps[-1].name}")
    rc = await _colmap(c, args, n, r"Registering image #\d+ \((\d+)\)", "camere")
    if rc != 0:
        raise RuntimeError("Il mapper di COLMAP è uscito con errore: vedi logs/mapping.log")
    pick_best_model(c.paths.sparse)
    return {}


async def stage_refine(c: Ctx) -> dict:
    best = pick_best_model(c.paths.sparse)
    shutil.rmtree(c.paths.refined, ignore_errors=True)
    c.paths.refined.mkdir(parents=True, exist_ok=True)
    c.progress(None, "bundle adjustment (può richiedere molto con migliaia di camere)")
    rc = await _colmap(c, ["bundle_adjuster", "--input_path", str(best), "--output_path", str(c.paths.refined),
                           "--BundleAdjustment.refine_principal_point", "1"])
    if rc != 0 or not (c.paths.refined / "images.bin").exists():
        for f in best.glob("*.bin"):
            shutil.copy(f, c.paths.refined / f.name)
    total = count_files(c.paths.frames, "*.jpg")
    registered = _count_registered(c.paths.refined / "images.bin")
    if registered is None:
        rc = await _colmap(c, ["model_analyzer", "--path", str(c.paths.refined)])
    return {"registered": registered, "total": total, "ratio": round(registered / total, 3) if registered and total else None}


def _count_registered(images_bin: Path) -> Optional[int]:
    """Legge il numero di immagini dall'intestazione di images.bin (uint64 little-endian)."""
    try:
        with images_bin.open("rb") as f:
            return int.from_bytes(f.read(8), "little")
    except OSError:
        return None


async def stage_convert(c: Ctx) -> dict:
    """Converte il modello COLMAP in transforms.json e crea le immagini ridotte."""
    c.paths.dataset.mkdir(parents=True, exist_ok=True)
    py = sys.executable
    await c.cmd([py, "-m", "app.tools.convert", str(c.paths.refined), str(c.paths.dataset), str(c.paths.frames)],
                cwd=Path(__file__).resolve().parent.parent)
    n = int(json.loads((c.paths.dataset / "transforms.json").read_text())["frames"].__len__())
    d = c.s.downscale
    if d > 1:
        out = c.paths.dataset / f"images_{d}"
        out.mkdir(exist_ok=True)
        files = sorted(c.paths.frames.glob("*.jpg"))
        sem = asyncio.Semaphore(max(2, min(8, os.cpu_count() or 4)))
        done = 0

        async def one(f: Path) -> None:
            nonlocal done
            async with sem:
                dst = out / f.name
                if not dst.exists():
                    await c.cmd(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(f), "-vf", f"scale=iw/{d}:ih/{d}",
                                 "-qscale:v", "2", str(dst)])
            done += 1
            if done % 25 == 0:
                c.progress(100 * done / len(files), f"immagini ridotte {done}/{len(files)}")
        await asyncio.gather(*(one(f) for f in files))
    return {"frames_in_dataset": n}


def _train_cmd(c: Ctx, name: str, steps: int, vis: str, port: Optional[int], load_dir: Optional[Path]) -> list[str]:
    cmd = ["ns-train", "splatfacto", "--output-dir", str(c.paths.outputs), "--experiment-name", name, "--timestamp", "run1",
           "--pipeline.datamanager.cache-images", c.s.cache_images, "--max-num-iterations", str(steps),
           "--steps-per-save", "1000", "--vis", vis]
    if port:
        cmd += ["--viewer.websocket-port", str(port), "--viewer.quit-on-train-completion", "True"]
    if load_dir:
        cmd += ["--load-dir", str(load_dir)]
    cmd += ["nerfstudio-data", "--data", str(c.paths.dataset), "--downscale-factor", str(c.s.downscale)]
    return cmd


def _ckpt_dir(c: Ctx, name: str) -> Path:
    return c.paths.outputs / name / "splatfacto" / "run1" / "nerfstudio_models"


def _on_train_line(c: Ctx):
    def f(line: str) -> None:
        p = _rx_progress(line)
        if p:
            c.progress(p[0], f"ETA {p[1]}" if p[1] else None)
    return f


async def stage_test(c: Ctx) -> dict:
    if c.s.skip_test:
        return {"skipped": True}
    shutil.rmtree(c.paths.outputs / "test", ignore_errors=True)
    await c.cmd(_train_cmd(c, "test", c.s.test_steps, "tensorboard", None, None), on_line=_on_train_line(c))
    cfg = c.paths.outputs / "test" / "splatfacto" / "run1" / "config.yml"
    ev = c.paths.outputs / "test" / "eval.json"
    c.progress(None, "valutazione sulle immagini di controllo")
    await c.cmd(["ns-eval", "--load-config", str(cfg), "--output-path", str(ev)])
    r = json.loads(ev.read_text())["results"]
    metrics = {"psnr": round(r["psnr"], 2), "ssim": round(r["ssim"], 3), "lpips": round(r["lpips"], 3), "gate": c.s.gate_psnr}
    c.stage.metrics = metrics
    if r["psnr"] < c.s.gate_psnr:
        raise Blocked(f"PSNR {r['psnr']:.1f} dB sotto la soglia {c.s.gate_psnr} dB: le pose delle camere sono probabilmente sbagliate. "
                      "Puoi continuare comunque, ma il training finale rischia di venire male.")
    return metrics


async def stage_train(c: Ctx) -> dict:
    ck = _ckpt_dir(c, "main")
    load = ck if ck.exists() and any(ck.glob("step-*.ckpt")) else None
    if load:
        c.progress(None, "ripresa dall'ultimo checkpoint")
    await c.cmd(_train_cmd(c, "main", c.s.steps, "viewer", config.VIEWER_PORT, load), on_line=_on_train_line(c))
    if not any(ck.glob("step-*.ckpt")):
        raise RuntimeError("Il training è terminato senza checkpoint: vedi logs/train.log")
    return {}


async def stage_export(c: Ctx) -> dict:
    cfg = c.paths.outputs / "main" / "splatfacto" / "run1" / "config.yml"
    raw = c.paths.export / "raw"
    shutil.rmtree(raw, ignore_errors=True)
    raw.mkdir(parents=True, exist_ok=True)
    await c.cmd(["ns-export", "gaussian-splat", "--load-config", str(cfg), "--output-dir", str(raw)])
    c.progress(60, "ritaglio delle gaussiane lontane e ricentratura")
    await c.cmd([sys.executable, "-m", "app.tools.crop", str(raw / "splat.ply"), str(c.paths.export / "splat_clean.ply"), str(c.s.crop_radius)],
                cwd=Path(__file__).resolve().parent.parent)
    return {"ply": "export/splat_clean.ply", "ply_raw": "export/raw/splat.ply",
            "size_mb": round((c.paths.export / "splat_clean.ply").stat().st_size / 1e6, 1)}


REAL: dict[str, Callable[[Ctx], Awaitable[dict]]] = {
    "probe": stage_probe, "frames": stage_frames, "features": stage_features, "matching": stage_matching,
    "mapping": stage_mapping, "refine": stage_refine, "convert": stage_convert, "test": stage_test,
    "train": stage_train, "export": stage_export,
}
