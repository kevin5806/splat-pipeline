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

from . import config, versions, weak
from .models import STAGES, Project, StageState
from .runner import CmdError, run_cmd
from .store import ProjectPaths
from .tools.colmap_model import read_camera_count, read_image_names
from .tools.flatten import flatten_dataset, missing_training_images

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


def reset_markers(paths: ProjectPaths, stage_id: str) -> None:
    """Rifare una fase "da zero" cancella i marcatori che permettono di riprenderla: altrimenti si riuserebbe un lavoro vecchio."""
    ids = [s for s, _ in STAGES]
    i = ids.index(stage_id)
    if i <= ids.index("frames") and paths.frames.exists():
        for f in paths.frames.glob("*/.done"):
            f.unlink(missing_ok=True)
    if i <= ids.index("features"):
        (paths.colmap / "features.sig").unlink(missing_ok=True)
    if i <= ids.index("recover"):
        shutil.rmtree(paths.colmap / "recovery", ignore_errors=True)
    keep_previous_results(paths, stage_id)


def keep_previous_results(paths: ProjectPaths, stage_id: str) -> None:
    """Rifare il training deve ripartire da zero (con il vecchio checkpoint risulterebbe "già completo"), ma il risultato
    di prima non si cancella e non si sovrascrive: diventa una versione (vedi versions.py)."""
    ids = [s for s, _ in STAGES]
    i = ids.index(stage_id)
    redo_training = i <= ids.index("train")
    main = paths.outputs / "main"
    ply = paths.export / "splat_clean.ply"
    final = paths.root / "quality" / "final"
    if i <= ids.index("export"):
        versions.migrate(paths)                     # il "precedente" del sistema di prima diventa anch'esso una versione
        if ply.exists():
            versions.archive(paths, ply, "splat_clean", main if redo_training and main.exists() else None,
                             final if final.exists() else None)
        for stale in ("mesh.ply", "mesh.json", "splat_ritaglio.ply", "splat_in_metri.ply"):
            (paths.export / stale).unlink(missing_ok=True)      # derivati dal modello di prima
    if redo_training and main.exists():             # training mai arrivato all'esportazione: se ne tiene l'ultimo
        previous = paths.outputs / "main.prev"
        shutil.rmtree(previous, ignore_errors=True)
        os.replace(main, previous)
    if i <= ids.index("test"):
        shutil.rmtree(paths.root / "quality" / "test", ignore_errors=True)


def _features_signature(project: Project, paths: ProjectPaths) -> str:
    """Impronta di ciò che le feature descrivono: immagini e modello di camera per dispositivo. Se coincide con quella
    salvata alla partenza precedente, il database di COLMAP si può riusare (le immagini già elaborate si saltano)."""
    import hashlib
    h = hashlib.sha1()
    for v in sorted(project.videos, key=lambda x: x.id):
        model = v.camera_model or project.settings.camera_model
        names = sorted(p.name for p in (paths.frames / v.id).glob("frame_*.jpg"))
        h.update(f"{v.id}|{v.device}|{model}|{len(names)}|{names[0] if names else ''}|{names[-1] if names else ''}".encode())
    return h.hexdigest()


def write_features_signature(project: Project, paths: ProjectPaths) -> None:
    paths.colmap.mkdir(parents=True, exist_ok=True)
    (paths.colmap / "features.sig").write_text(_features_signature(project, paths))


def database_matches_files(paths: ProjectPaths) -> bool:
    """Ogni immagine registrata nel database di COLMAP esiste ancora su disco?"""
    import sqlite3
    try:
        con = sqlite3.connect(paths.database)
        try:
            names = [r[0] for r in con.execute("SELECT name FROM images")]
        finally:
            con.close()
    except sqlite3.Error:
        return True                       # database non leggibile: ci penserà COLMAP
    return all((paths.frames / n).is_file() for n in names)


def frame_files(paths: ProjectPaths) -> list[Path]:
    """Tutti i frame del progetto: frames/<id_video>/frame_00001.jpg."""
    return sorted(paths.frames.rglob("*.jpg")) if paths.frames.exists() else []


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


def model_dirs(sparse: Path) -> list[Path]:
    """Tutte le cartelle che contengono un modello COLMAP.

    Attenzione, COLMAP scrive in due posti diversi: una ricostruzione nuova finisce in sparse/<n>/, ma quando il mapper
    RIPARTE da uno snapshot (--input_path) scrive direttamente in sparse/. Guardare solo le sottocartelle ha fatto usare un
    frammento di 2 camere al posto del modello completo da 1.541.
    """
    if not sparse.exists():
        return []
    candidates = [sparse] + sorted(d for d in sparse.iterdir() if d.is_dir())
    return [d for d in candidates if (d / "images.bin").exists()]


def pick_best_model(sparse: Path) -> Path:
    """Il modello con più camere ricostruite (numero scritto nell'intestazione di images.bin, non la dimensione del file)."""
    models = model_dirs(sparse)
    if not models:
        raise RuntimeError("COLMAP non ha prodotto nessun modello: foto troppo simili, sfocate o con pochi punti riconoscibili.")
    return max(models, key=lambda d: (_count_registered(d / "images.bin") or 0, (d / "images.bin").stat().st_size))


MIN_CAMERAS, MIN_RATIO = 30, 0.2


def check_reconstruction(registered: Optional[int], total: int, where: str) -> None:
    """Ferma il lavoro se la ricostruzione è inutilizzabile: andare avanti sprecherebbe ore e darebbe errori incomprensibili."""
    if registered is None or not total:
        return
    if registered < min(MIN_CAMERAS, total) or registered / total < MIN_RATIO:
        raise RuntimeError(
            f"{where}: ricostruite solo {registered} camere su {total} ({100 * registered / total:.1f}%). Con così poche il "
            "modello non si può allenare, quindi il lavoro si ferma qui. Cause tipiche: video che non si sovrappongono, "
            "movimenti troppo veloci, pareti senza punti riconoscibili; se invece il mapper ne aveva contate molte di più, "
            "è un errore di questa applicazione (vedi logs/mapping.log).")


# ---------------------------------------------------------------- fasi reali

async def _probe_one(c: Ctx, video_path: Path, log_name: str) -> dict:
    out = c.paths.logs / log_name
    c.paths.logs.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_streams", "-show_format", "-of", "json", str(video_path),
            stdout=f, stderr=asyncio.subprocess.PIPE)
        _, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe ha fallito su {video_path.name}: {err.decode(errors='replace')[:300]}")
    info = json.loads(out.read_text())
    if not info.get("streams"):
        raise RuntimeError(f"{video_path.name}: nessuna traccia video.")
    st = info["streams"][0]
    num, den = (st.get("r_frame_rate", "30/1").split("/") + ["1"])[:2]
    fps = float(num) / float(den or 1)
    duration = float(st.get("duration") or info.get("format", {}).get("duration") or 0)
    total = int(st.get("nb_frames") or round(duration * fps))
    rotation = 0
    for sd in st.get("side_data_list", []) or []:
        if "rotation" in sd:
            rotation = int(sd["rotation"])
    return {"width": st.get("width"), "height": st.get("height"), "fps": round(fps, 2), "duration_s": round(duration, 1),
            "total_frames": total, "rotation": rotation}


def compute_steps(videos: list, totals: dict, settings) -> dict:
    """Un frame ogni quanti, per ogni video.

    Priorità: `frame_step` del singolo video; altrimenti, se il progetto ha `target_frames`, un passo comune calcolato in modo
    che il totale sia circa quel numero (i frame si ripartiscono in proporzione alla durata); altrimenti `settings.frame_step`.
    """
    steps = {v.id: v.frame_step for v in videos if v.frame_step}
    rest = [v for v in videos if not v.frame_step]
    if rest:
        step = settings.frame_step
        if settings.target_frames:
            used = sum(-(-totals[v.id] // steps[v.id]) for v in videos if v.id in steps)
            remaining = max(1, settings.target_frames - used)
            tot = sum(totals[v.id] for v in rest)
            if tot:
                step = max(1, min(300, round(tot / remaining)))
        for v in rest:
            steps[v.id] = step
    return steps


async def ffprobe_info(video_path: Path) -> Optional[dict]:
    """Dati essenziali di un video, senza Ctx (usato al caricamento per poter stimare i tempi). None se non riesce."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_streams", "-show_format", "-of", "json", str(video_path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode != 0:
            return None
        info = json.loads(out)
        st = info["streams"][0]
        num, den = (st.get("r_frame_rate", "30/1").split("/") + ["1"])[:2]
        fps = float(num) / float(den or 1)
        duration = float(st.get("duration") or info.get("format", {}).get("duration") or 0)
        total = int(st.get("nb_frames") or round(duration * fps))
        return {"width": st.get("width"), "height": st.get("height"), "fps": round(fps, 2),
                "duration_s": round(duration, 1), "total_frames": total}
    except (OSError, ValueError, KeyError, IndexError, asyncio.TimeoutError):
        return None


def check_devices(project: Project) -> None:
    """Video dello stesso dispositivo condividono una camera: il modello di camera deve coincidere."""
    by_dev: dict[int, list] = {}
    for v in project.videos:
        by_dev.setdefault(v.device, []).append(v)
    for dev, vids in by_dev.items():
        models = {v.camera_model or project.settings.camera_model for v in vids}
        if len(models) > 1:
            raise RuntimeError(f"Il dispositivo {dev} ha video con modelli di camera diversi ({', '.join(sorted(models))}): "
                               "usa lo stesso modello oppure assegna un dispositivo diverso.")


async def stage_probe(c: Ctx) -> dict:
    if not c.project.videos:
        raise RuntimeError("Nessun video caricato per questo progetto.")
    check_devices(c.project)
    videos: dict[str, dict] = {}
    for i, v in enumerate(c.project.videos):
        path = c.paths.input / v.filename
        if not path.exists():
            raise RuntimeError(f"Video non trovato: {v.name or v.filename}")
        videos[v.id] = await _probe_one(c, path, f"probe_{v.id}.json")
        c.progress(100 * (i + 1) / len(c.project.videos), f"{v.name or v.filename}")
    # risoluzione coerente dentro lo stesso dispositivo (una sola camera COLMAP per dispositivo)
    sizes: dict[int, set] = {}
    for v in c.project.videos:
        sizes.setdefault(v.device, set()).add((videos[v.id]["width"], videos[v.id]["height"]))
    for dev, sz in sizes.items():
        if len(sz) > 1:
            raise RuntimeError(f"I video del dispositivo {dev} hanno risoluzioni diverse ({sorted(sz)}): "
                               "assegnali a dispositivi diversi.")
    steps = compute_steps(c.project.videos, {vid: x["total_frames"] for vid, x in videos.items()}, c.s)
    for vid, x in videos.items():
        x["step"] = steps[vid]
        x["expected_frames"] = math.ceil(x["total_frames"] / steps[vid]) if x["total_frames"] else 0
    first = videos[c.project.videos[0].id]
    return {**{k: first[k] for k in ("width", "height", "fps", "duration_s", "total_frames", "rotation")},
            "video_count": len(videos), "videos": videos,
            "expected_frames": sum(x["expected_frames"] for x in videos.values())}


async def extract_video(c: Ctx, v, step: int, expected_total: int, done_before: int = 0) -> int:
    """Estrae un frame ogni `step` da un video in frames/<id>/. Un video già estratto con lo stesso passo non si rifà."""
    video = c.paths.input / v.filename
    out_dir = c.paths.frames / v.id
    marker = out_dir / ".done"
    want = {"step": step, "video": v.filename, "size": video.stat().st_size if video.exists() else 0}
    try:
        have = json.loads(marker.read_text()) if marker.exists() else None
    except (OSError, ValueError):
        have = None
    # contano solo i frame dell'estrazione normale: quelli aggiunti per infittire non invalidano il marcatore
    if have and {k: have.get(k) for k in want} == want and count_files(out_dir, "frame_*.jpg") == have.get("frames"):
        c.progress(100 * (done_before + have["frames"]) / max(1, expected_total), f"{v.id}: già estratto ({have['frames']} frame)")
        return have["frames"]
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    def on_line(line: str) -> None:
        m = re.match(r"frame=(\d+)", line)
        if m and expected_total:
            c.progress(100 * (done_before + int(m.group(1))) / expected_total, f"{v.id}: {m.group(1)} frame")

    base = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-progress", "pipe:1", "-nostats", "-y", "-i", str(video), "-an",
            "-vf", f"select=not(mod(n\\,{step}))"]
    out = [str(out_dir / "frame_%05d.jpg")]
    # ffmpeg recente: -fps_mode; ffmpeg vecchio (immagine di Nerfstudio): -vsync
    rc = await c.cmd(base + ["-fps_mode", "vfr", "-qscale:v", "2"] + out, on_line=on_line, check=False)
    if rc != 0:
        shutil.rmtree(out_dir, ignore_errors=True)
        out_dir.mkdir(parents=True, exist_ok=True)
        await c.cmd(base + ["-vsync", "vfr", "-qscale:v", "2"] + out, on_line=on_line)
    frames = count_files(out_dir, "frame_*.jpg")
    marker.write_text(json.dumps({**want, "frames": frames}))
    return frames


async def stage_frames(c: Ctx) -> dict:
    probe = c.project.stage("probe").metrics
    expected_total = probe.get("expected_frames") or 0
    c.paths.frames.mkdir(parents=True, exist_ok=True)
    keep = {v.id for v in c.project.videos}
    for d in c.paths.frames.iterdir():                      # cartelle di video tolti dal progetto
        if d.is_dir() and d.name not in keep:
            shutil.rmtree(d, ignore_errors=True)
    done_before = 0
    per_video: dict[str, int] = {}
    for v in c.project.videos:
        step = int((probe.get("videos", {}).get(v.id) or {}).get("step") or c.s.frame_step)
        per_video[v.id] = await extract_video(c, v, step, expected_total, done_before)
        done_before += per_video[v.id]
    n = sum(per_video.values())
    if n < 30:
        raise RuntimeError(f"Estratti solo {n} frame: video troppo corti o 'frame_step' troppo alto.")
    return {"frames": n, "per_video": per_video}


async def _colmap(c: Ctx, args: list[str], total: int = 0, rx: Optional[str] = None, label: str = "") -> int:
    def on_line(line: str) -> None:
        if rx:
            m = re.search(rx, line)
            if m and total:
                c.progress(100 * int(m.group(1)) / total, f"{label} {m.group(1)} / {total}")
    return await c.cmd(["colmap", *args], on_line=on_line, check=False)


async def stage_features(c: Ctx) -> dict:
    """Feature SIFT. Una camera COLMAP per dispositivo: i video dello stesso dispositivo la condividono."""
    check_devices(c.project)
    n_total = len(frame_files(c.paths))
    c.paths.colmap.mkdir(parents=True, exist_ok=True)
    sig_file = c.paths.colmap / "features.sig"
    sig = _features_signature(c.project, c.paths)
    if (c.paths.database.exists() and sig_file.exists() and sig_file.read_text().strip() == sig
            and database_matches_files(c.paths)):
        # ripresa dopo un'interruzione: COLMAP salta le immagini già presenti nel database
        c.progress(None, "ripresa: le feature già estratte si saltano")
    else:
        for f in (c.paths.database, Path(str(c.paths.database) + "-shm"), Path(str(c.paths.database) + "-wal")):
            f.unlink(missing_ok=True)
        shutil.rmtree(c.paths.sparse, ignore_errors=True)
        shutil.rmtree(c.paths.snapshots, ignore_errors=True)
        sig_file.write_text(sig)
    groups: dict[int, list] = {}
    for v in c.project.videos:
        groups.setdefault(v.device, []).append(v)
    done = 0
    for device, vids in sorted(groups.items()):
        names = sorted(f"{v.id}/{p.name}" for v in vids for p in (c.paths.frames / v.id).glob("*.jpg"))
        list_file = c.paths.colmap / f"images_device{device}.txt"
        list_file.write_text("\n".join(names) + "\n", encoding="utf-8")
        model = vids[0].camera_model or c.s.camera_model
        base = ["feature_extractor", "--database_path", str(c.paths.database), "--image_path", str(c.paths.frames),
                "--image_list_path", str(list_file), "--ImageReader.single_camera", "1", "--ImageReader.camera_model", model]
        label = f"dispositivo {device}" if len(groups) > 1 else "immagini"

        async def run(gpu: int, done: int = done, label: str = label, count: int = len(names)) -> int:
            def on_line(line: str) -> None:
                m = re.search(r"Processed file \[(\d+)/", line)
                if m and n_total:
                    c.progress(100 * (done + int(m.group(1))) / n_total, f"{label} {m.group(1)} / {count}")
            return await c.cmd(["colmap", *base, "--SiftExtraction.use_gpu", str(gpu)], on_line=on_line, check=False)

        rc = await run(1)
        if rc != 0:  # niente GPU/CUDA per COLMAP: ripiega sulla CPU (molto più lento)
            c.progress(None, "GPU non disponibile per COLMAP: uso la CPU (lento)")
            rc = await run(0)
            if rc != 0:
                raise RuntimeError("Estrazione delle feature fallita: vedi logs/features.log")
        done += len(names)
    return {"images": n_total, "devices": len(groups)}


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
    n = len(frame_files(c.paths))
    c.paths.sparse.mkdir(parents=True, exist_ok=True)
    c.paths.snapshots.mkdir(parents=True, exist_ok=True)
    snaps = sorted((d for d in c.paths.snapshots.iterdir() if (d / "images.bin").exists()), key=lambda d: int(d.name) if d.name.isdigit() else 0)
    args = ["mapper", "--database_path", str(c.paths.database), "--image_path", str(c.paths.frames),
            "--output_path", str(c.paths.sparse), "--Mapper.ba_global_function_tolerance=1e-6",
            "--Mapper.snapshot_path", str(c.paths.snapshots), "--Mapper.snapshot_images_freq", "200"]
    if snaps:
        args += ["--input_path", str(snaps[-1])]
        c.progress(None, f"ripresa dallo snapshot {snaps[-1].name}")
    else:
        shutil.rmtree(c.paths.sparse, ignore_errors=True)     # da zero: niente modelli di esecuzioni precedenti
        c.paths.sparse.mkdir(parents=True, exist_ok=True)
    rc = await _colmap(c, args, n, r"Registering image #\d+ \((\d+)\)", "camere")
    if rc != 0:
        raise RuntimeError("Il mapper di COLMAP è uscito con errore: vedi logs/mapping.log")
    best = pick_best_model(c.paths.sparse)
    registered = _count_registered(best / "images.bin")
    check_reconstruction(registered, n, "Ricostruzione delle camere")
    return {"registered": registered, "total": n}


async def stage_refine(c: Ctx) -> dict:
    if not model_dirs(c.paths.sparse):
        raise RuntimeError("I modelli intermedi della ricostruzione non ci sono (forse cancellati per liberare spazio): usa «Rifai "
                           "da qui» sulla fase «Ricostruzione delle camere».")
    best = pick_best_model(c.paths.sparse)
    shutil.rmtree(c.paths.refined, ignore_errors=True)
    c.paths.refined.mkdir(parents=True, exist_ok=True)
    c.progress(None, "bundle adjustment (può richiedere molto con migliaia di camere)")
    rc = await _colmap(c, ["bundle_adjuster", "--input_path", str(best), "--output_path", str(c.paths.refined),
                           "--BundleAdjustment.refine_principal_point", "1"])
    before = _count_registered(best / "images.bin")
    after = _count_registered(c.paths.refined / "images.bin")
    if rc != 0 or after is None or (before and after < 0.95 * before):
        c.progress(None, "il bundle adjustment non è utilizzabile: uso il modello del mapper")
        for f in best.glob("*.bin"):
            shutil.copy(f, c.paths.refined / f.name)
    total = len(frame_files(c.paths))
    registered = _count_registered(c.paths.refined / "images.bin")
    check_reconstruction(registered, total, "Dopo il bundle adjustment")
    m: dict = {"registered": registered, "total": total, "ratio": round(registered / total, 3) if registered and total else None,
               "models": len(model_dirs(c.paths.sparse)),
               "cameras": read_camera_count(c.paths.refined / "cameras.bin")}
    m["per_video"] = coverage_per_video(c, read_image_names(c.paths.refined / "images.bin"))
    return m


def coverage_per_video(c: Ctx, registered_names: list[str]) -> dict:
    """Per ogni video: frame agganciati e intervalli (in secondi) dove COLMAP ha perso la camera.

    Servono a dire dove conviene riprendere: un buco di 10 secondi vuol dire sovrapposizione insufficiente
    o pareti senza punti riconoscibili in quel tratto.
    """
    probe = c.project.stage("probe").metrics.get("videos", {})
    registered = {n: 10 ** 9 for n in registered_names}     # qui conta solo "agganciata o no"
    out: dict[str, dict] = {}
    for v in c.project.videos:
        fps = float((probe.get(v.id) or {}).get("fps") or 30.0)
        step = int((probe.get(v.id) or {}).get("step") or c.s.frame_step)
        seq = weak.sequence(weak.list_frames(c.paths.frames / v.id), v.id, step, registered)
        ok = sum(1 for x in seq if x[2])
        ranges = [[r["start_s"], r["end_s"], r["frames"]] for r in weak.weak_runs(seq, fps, step) if r["frames"] >= 2]
        out[v.id] = {"registered": ok, "total": len(seq), "ratio": round(ok / len(seq), 3) if seq else None,
                     "missing": len(seq) - ok, "missing_ranges": ranges[:40]}
    return out


def _count_registered(images_bin: Path) -> Optional[int]:
    """Legge il numero di immagini dall'intestazione di images.bin (uint64 little-endian)."""
    try:
        with images_bin.open("rb") as f:
            return int.from_bytes(f.read(8), "little")
    except OSError:
        return None


async def stage_convert(c: Ctx) -> dict:
    """Converte il modello COLMAP in transforms.json, rende unici i nomi dei frame e crea le immagini ridotte."""
    shutil.rmtree(c.paths.dataset, ignore_errors=True)      # i collegamenti si tolgono senza toccare i file originali
    c.paths.dataset.mkdir(parents=True, exist_ok=True)
    py = sys.executable
    await c.cmd([py, "-m", "app.tools.convert", str(c.paths.refined), str(c.paths.dataset), str(c.paths.frames)],
                cwd=Path(__file__).resolve().parent.parent)
    pairs = flatten_dataset(c.paths.dataset, c.paths.frames)   # Nerfstudio ignora le sottocartelle: servono nomi unici
    d = c.s.downscale
    if d > 1:
        out = c.paths.dataset / f"images_{d}"
        out.mkdir(exist_ok=True)
        sem = asyncio.Semaphore(max(2, min(8, os.cpu_count() or 4)))
        done = 0

        async def one(name: str, source: Path) -> None:
            nonlocal done
            async with sem:
                await c.cmd(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
                             "-vf", f"scale=iw/{d}:ih/{d}", "-qscale:v", "2", str(out / name)])
            done += 1
            if done % 25 == 0:
                c.progress(100 * done / len(pairs), f"immagini ridotte {done}/{len(pairs)}")
        await asyncio.gather(*(one(name, source) for name, source in pairs))
    # controllo prima del training: esistono esattamente i file che Nerfstudio andrà ad aprire?
    missing = missing_training_images(c.paths.dataset, d)
    if missing:
        raise RuntimeError(f"Dataset incompleto: mancano {len(missing)} immagini su {len(pairs)} (la prima: {missing[0]}).")
    return {"frames_in_dataset": len(pairs)}


STANDARD_STEPS = 30000
# I valori del profilo ufficiale splatfacto-big di Nerfstudio: più gaussiane, quindi più dettaglio (e più tempo e VRAM).
HIGH_QUALITY = {"--pipeline.model.cull-alpha-thresh": "0.005", "--pipeline.model.densify-grad-thresh": "0.0005"}


REFINE_EVERY, WARMUP, DEFAULT_RESET, DEFAULT_STOP = 100, 500, 3000, 15000
TRAIN_FRACTION = 0.9                      # quota di foto usata per il training (le altre restano di controllo)


def densify_schedule(train_images: int, steps: int) -> tuple[int, int]:
    """(ogni quanti passi azzerare l'opacità, fino a che passo aggiungere dettaglio), in base al numero di foto.

    Dopo ogni azzeramento Nerfstudio NON aggiunge gaussiane per «foto di training + 100» passi (vuole prima rivederle
    tutte). Col ciclo fisso di 3.000 passi, con 2.425 foto restavano 20 occasioni di aggiungere dettaglio invece delle
    circa 135 che si hanno con 200 foto (e da 2.900 foto in su nessuna): il modello veniva con un terzo delle gaussiane.
    Qui il ciclo è almeno tre volte l'attesa, così per due terzi del tempo si aggiunge dettaglio. Fino a circa 900 foto
    di training il risultato è identico a quello predefinito.

    È un'OPZIONE, non il comportamento predefinito: alla prima prova vera (2.425 foto) ha dato 739.528 gaussiane invece di
    308.015 e più dettaglio, ma punteggi peggiori sulle stesse 269 foto di controllo (PSNR 20,88 contro 22,21, SSIM 0,880
    contro 0,898, LPIPS 0,301 contro 0,250) e più artefatti a vista. La causa non è verificata; l'ipotesi è che con meno
    azzeramenti dell'opacità restino più gaussiane fluttuanti.
    """
    pause = max(0, train_images) + REFINE_EVERY
    reset = max(DEFAULT_RESET, -(-pause * 3 // REFINE_EVERY) * REFINE_EVERY)
    stop = max(DEFAULT_STOP, steps // 2)
    if reset > DEFAULT_RESET:
        stop = min(max(stop, 2 * reset), int(steps * 0.7))    # almeno due cicli, lasciando passi per rifinire
    return reset, stop


def densify_passes(train_images: int, reset: int, stop: int) -> int:
    """Quante volte il training potrà aggiungere dettaglio (la stessa condizione di gsplat.DefaultStrategy)."""
    pause = train_images + REFINE_EVERY
    return sum(1 for step in range(REFINE_EVERY, stop, REFINE_EVERY) if step > WARMUP and step % reset >= pause)


def training_images(paths: ProjectPaths) -> int:
    try:
        frames = len(json.loads((paths.dataset / "transforms.json").read_text(encoding="utf-8"))["frames"])
    except (OSError, ValueError, KeyError):
        return 0
    return math.ceil(frames * TRAIN_FRACTION)


def quality_options(s, train_images: int = 0) -> list[str]:
    """Opzioni di ns-train per il training finale. Si calcolano sui passi TOTALI, così una ripresa non le cambia."""
    out: list[str] = []
    if s.quality == "high":
        for flag, value in HIGH_QUALITY.items():
            out += [flag, value]
    reset, stop = densify_schedule(train_images, s.steps) if s.densify == "extended" else (DEFAULT_RESET, DEFAULT_STOP)
    if reset > DEFAULT_RESET:
        out += ["--pipeline.model.reset-alpha-every", str(reset // REFINE_EVERY), "--pipeline.model.stop-split-at", str(stop)]
    elif s.steps > STANDARD_STEPS:
        # più passi servono davvero solo se si allunga anche la pianificazione: di suo Nerfstudio smette di aggiungere
        # dettaglio a 15.000 passi
        out += ["--pipeline.model.stop-split-at", str(s.steps // 2)]
    if s.steps > STANDARD_STEPS:
        out += ["--optimizers.means.scheduler.max-steps", str(s.steps)]    # e di rallentare a 30.000
    return out


def _train_cmd(c: Ctx, name: str, steps: int, vis: str, port: Optional[int], load_dir: Optional[Path]) -> list[str]:
    cmd = ["ns-train", "splatfacto", "--output-dir", str(c.paths.outputs), "--experiment-name", name, "--timestamp", "run1",
           "--pipeline.datamanager.cache-images", c.s.cache_images, "--max-num-iterations", str(steps),
           "--steps-per-save", "1000", "--vis", vis]
    if port:
        cmd += ["--viewer.websocket-port", str(port), "--viewer.quit-on-train-completion", "True"]
    if load_dir:
        cmd += ["--load-dir", str(load_dir)]
    if name == "main":                      # il test resta standard: la sua soglia è tarata su quello
        cmd += quality_options(c.s, training_images(c.paths))
    cmd += ["nerfstudio-data", "--data", str(c.paths.dataset), "--downscale-factor", str(c.s.downscale)]
    return cmd


APP_ROOT = Path(__file__).resolve().parent.parent


def _with_preview(cmd: list[str]) -> list[str]:
    """Stesso comando di ns-train, passando dal nostro avvio che salva un'anteprima ogni tanto."""
    return [sys.executable, "-m", "app.tools.train_preview", *cmd[1:]]


def _ckpt_dir(c: Ctx, name: str) -> Path:
    return c.paths.outputs / name / "splatfacto" / "run1" / "nerfstudio_models"


def _on_train_line(c: Ctx):
    def f(line: str) -> None:
        p = _rx_progress(line)
        if p:
            c.progress(p[0], f"ETA {p[1]}" if p[1] else None)
    return f


def quality_dir(paths: ProjectPaths, kind: str) -> Path:
    return paths.root / "quality" / kind


async def evaluate(c: Ctx, kind: str, run_dir: Path) -> dict:
    """ns-eval su un modello, più il confronto vista per vista (foto vera e modello) conservato in quality/<kind>/."""
    cfg = run_dir / "splatfacto" / "run1" / "config.yml"
    ev = run_dir / "eval.json"
    renders = run_dir / "renders"
    shutil.rmtree(renders, ignore_errors=True)
    shutil.rmtree(quality_dir(c.paths, kind), ignore_errors=True)
    c.progress(None, "valutazione sulle immagini di controllo")
    await c.cmd([sys.executable, "-m", "app.tools.eval_run", "--load-config", str(cfg), "--output-path", str(ev),
                 "--render-output-path", str(renders)], cwd=APP_ROOT)
    results = json.loads(ev.read_text())["results"]
    probe = c.project.stage("probe").metrics.get("videos", {})
    params = {v.id: {"fps": (probe.get(v.id) or {}).get("fps") or 30.0,
                     "step": (probe.get(v.id) or {}).get("step") or c.s.frame_step} for v in c.project.videos}
    (c.paths.root / "quality").mkdir(parents=True, exist_ok=True)
    params_file = c.paths.root / "quality" / "params.json"
    params_file.write_text(json.dumps(params))
    try:        # il confronto è un di più: se non riesce, il risultato del test resta valido
        await c.cmd([sys.executable, "-m", "app.tools.eval_views", str(renders), str(c.paths.dataset), str(params_file),
                     str(quality_dir(c.paths, kind))], cwd=APP_ROOT)
    except CmdError as e:
        c.progress(None, f"confronto delle viste non disponibile: {str(e).splitlines()[-1][:150]}")
    shutil.rmtree(renders, ignore_errors=True)      # centinaia di PNG: restano solo le viste scelte
    return results


async def stage_test(c: Ctx) -> dict:
    if c.s.skip_test:
        return {"skipped": True}
    shutil.rmtree(c.paths.outputs / "test", ignore_errors=True)
    await c.cmd(_train_cmd(c, "test", c.s.test_steps, "tensorboard", None, None), on_line=_on_train_line(c))
    r = await evaluate(c, "test", c.paths.outputs / "test")
    metrics = {"psnr": round(r["psnr"], 2), "ssim": round(r["ssim"], 3), "lpips": round(r["lpips"], 3), "gate": c.s.gate_psnr}
    c.stage.metrics = metrics
    if r["psnr"] < c.s.gate_psnr:
        raise Blocked(f"PSNR {r['psnr']:.1f} dB sotto la soglia {c.s.gate_psnr} dB. Può dipendere da pose delle camere sbagliate "
                      "(se è molto sotto) oppure da zone poco coperte dal video (se è di poco sotto). Guarda il confronto delle "
                      "viste qui sotto; puoi continuare comunque.")
    return metrics


def latest_checkpoint_step(ck: Path) -> Optional[int]:
    """Il passo dell'ultimo checkpoint (step-000000599.ckpt -> 599), o None se non ce ne sono."""
    steps = [int(m.group(1)) for f in (ck.glob("step-*.ckpt") if ck.exists() else []) if (m := re.search(r"step-(\d+)", f.name))]
    return max(steps) if steps else None


def _on_resumable_train_line(c: Ctx, total: int):
    """Avanzamento sul totale vero: dopo una ripresa la percentuale di ns-train è calcolata sui soli passi mancanti."""
    def f(line: str) -> None:
        m = re.search(r"(?:^|\s)(\d+) \(\d+(?:\.\d+)?%\)", line)
        if not m:
            return
        p = _rx_progress(line)
        c.progress(100 * int(m.group(1)) / total, f"ETA {p[1]}" if p and p[1] else None)
    return f


async def stage_train(c: Ctx) -> dict:
    ck = _ckpt_dir(c, "main")
    last = latest_checkpoint_step(ck)
    total = c.s.steps
    load, todo = None, total
    if last is not None:
        done = last + 1
        todo = total - done
        # Attenzione: con --load-dir ns-train AGGIUNGE --max-num-iterations ai passi già fatti (verificato: ripreso a 600
        # chiedendone 900, si è fermato a 1500). Vanno quindi chiesti solo i passi mancanti.
        if todo <= 0:
            c.progress(100, f"training già completo ({done} passi)")
            return {"resumed_from": done}
        load = ck
        c.progress(100 * done / total, f"ripresa dal checkpoint: passo {done} di {total}")
    preview = c.paths.root / "preview"
    if load is None:
        shutil.rmtree(preview, ignore_errors=True)          # training da zero: via le anteprime di quello di prima
    await c.cmd(_with_preview(_train_cmd(c, "main", todo, "viewer", config.VIEWER_PORT, load)),
                on_line=_on_resumable_train_line(c, total), cwd=APP_ROOT,
                env={"SPLAT_PREVIEW_DIR": str(preview), "SPLAT_PREVIEW_EVERY": "1000", "SPLAT_PREVIEW_VIEWS": "4"})
    if latest_checkpoint_step(ck) is None:
        raise RuntimeError("Il training è terminato senza checkpoint: vedi logs/train.log")
    return {"resumed_from": last + 1} if last is not None else {}


async def stage_export(c: Ctx) -> dict:
    cfg = c.paths.outputs / "main" / "splatfacto" / "run1" / "config.yml"
    raw = c.paths.export / "raw"
    shutil.rmtree(raw, ignore_errors=True)
    raw.mkdir(parents=True, exist_ok=True)
    await c.cmd(["ns-export", "gaussian-splat", "--load-config", str(cfg), "--output-dir", str(raw)])
    c.progress(60, "ritaglio delle gaussiane lontane e ricentratura")
    await c.cmd([sys.executable, "-m", "app.tools.crop", str(raw / "splat.ply"), str(c.paths.export / "splat_clean.ply"), str(c.s.crop_radius)],
                cwd=Path(__file__).resolve().parent.parent)
    transforms = c.paths.outputs / "main" / "splatfacto" / "run1" / "dataparser_transforms.json"
    if transforms.exists():     # serve per allineare questo modello a quelli allenati dopo (confronto prima/dopo)
        shutil.copy(transforms, c.paths.export / "splat_clean.dataparser.json")
    try:
        gaussians = json.loads((c.paths.export / "splat_clean.json").read_text())["kept"]
    except (OSError, ValueError, KeyError):
        gaussians = None
    metrics = {**({"gaussians": gaussians} if gaussians else {}),
               "ply": "export/splat_clean.ply", "ply_raw": "export/raw/splat.ply",
               "size_mb": round((c.paths.export / "splat_clean.ply").stat().st_size / 1e6, 1)}
    c.progress(75, "qualità del modello finale")
    try:        # la valutazione finale è un di più: il modello è già esportato
        r = await evaluate(c, "final", c.paths.outputs / "main")
        metrics.update(psnr=round(r["psnr"], 2), ssim=round(r["ssim"], 3), lpips=round(r["lpips"], 3))
    except Exception as e:  # noqa: BLE001
        metrics["final_eval"] = "non riuscita (vedi il log)"
        c.progress(None, f"valutazione finale non riuscita: {str(e).splitlines()[0][:150] if str(e) else type(e).__name__}")
    return metrics


async def stage_recover(c: Ctx) -> dict:
    from .recover import stage_recover as run          # import qui: recover.py usa le funzioni di questo modulo
    return await run(c)


REAL: dict[str, Callable[[Ctx], Awaitable[dict]]] = {
    "probe": stage_probe, "frames": stage_frames, "features": stage_features, "matching": stage_matching,
    "mapping": stage_mapping, "refine": stage_refine, "recover": stage_recover, "convert": stage_convert, "test": stage_test,
    "train": stage_train, "export": stage_export,
}
