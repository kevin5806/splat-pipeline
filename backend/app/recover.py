"""Fase "Recupero dei tratti deboli".

Dove COLMAP ha perso la camera si estraggono dal video frame più fitti e si agganciano al modello GIÀ costruito, senza
rifare matching e ricostruzione da zero e senza muovere le pose già calcolate. Lo stesso meccanismo integra un video di
supporto, che l'applicazione chiede quando infittire non basta.

Si può interrompere in qualunque momento: il giro in corso è salvato in colmap/recovery/state.json e ogni passaggio
(estrazione, feature, matching, registrazione con snapshot) riprende da dov'era. Il modello buono viene sostituito solo
alla fine, e solo se quello nuovo non ha perso camere; quello di prima resta in colmap/recovery/round<N>/before.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
from pathlib import Path
from typing import Optional

from . import stages, weak
from .stages import Blocked, Ctx
from .store import ProjectPaths
from .tools.colmap_model import read_observations

MIN_SUPPORT_RATIO = 0.3
KEEP_RATIO = 0.99


# ---------------------------------------------------------------- stato

def recovery_dir(paths: ProjectPaths) -> Path:
    return paths.colmap / "recovery"


def load_state(paths: ProjectPaths) -> dict:
    f = recovery_dir(paths) / "state.json"
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"rounds": [], "current": None}


def save_state(paths: ProjectPaths, state: dict) -> None:
    f = recovery_dir(paths) / "state.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    os.replace(tmp, f)


def finish_swap(paths: ProjectPaths) -> None:
    """Interrotti a metà della sostituzione del modello: se 'refined' manca ed è pronto quello nuovo, si completa."""
    new = paths.colmap / "refined.new"
    if not new.exists():
        return
    if not paths.refined.exists():
        os.replace(new, paths.refined)
    else:
        shutil.rmtree(new, ignore_errors=True)


# ---------------------------------------------------------------- analisi

def video_params(c: Ctx, vid: str) -> tuple[float, int]:
    info = c.project.stage("probe").metrics.get("videos", {}).get(vid) or {}
    return float(info.get("fps") or 30.0), int(info.get("step") or c.s.frame_step)


def analyze(c: Ctx, observations: dict[str, int]) -> dict:
    seqs, runs, fps, steps, total_s, files_total, registered = {}, {}, {}, {}, 0.0, 0, 0
    for v in c.project.videos:
        f, step = video_params(c, v.id)
        seq = weak.sequence(weak.list_frames(c.paths.frames / v.id), v.id, step, observations)
        if not seq:
            continue
        seqs[v.id], fps[v.id], steps[v.id] = seq, f, step
        runs[v.id] = weak.weak_runs(seq, f, step)
        total_s += (seq[-1][0] + step) / f
        files_total += len(seq)
        registered += sum(1 for s in seq if s[2])
    return {"seqs": seqs, "runs": runs, "fps": fps, "steps": steps, "total_seconds": total_s,
            "total": files_total, "registered": registered}


async def _thumbnails(c: Ctx, runs: dict[str, list[dict]], limit: int = 24) -> list[dict]:
    """L'elenco dei tratti deboli (i più lunghi), ciascuno con una miniatura: così si capisce cosa riprendere."""
    flat = sorted(((vid, r) for vid, rs in runs.items() for r in rs), key=lambda x: -x[1]["seconds"])[:limit]
    out_dir = recovery_dir(c.paths) / "thumbs"
    out_dir.mkdir(parents=True, exist_ok=True)
    items = []
    for vid, r in sorted(flat, key=lambda x: (x[0], x[1]["a"])):
        thumb = f"{vid}_{Path(r['sample']).stem}.jpg"
        if not (out_dir / thumb).exists():
            await c.cmd(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(c.paths.frames / vid / r["sample"]),
                         "-vf", "scale=360:-2", "-qscale:v", "5", str(out_dir / thumb)], check=False)
        name = next((v.name or v.filename for v in c.project.videos if v.id == vid), vid)
        items.append({"video": vid, "video_name": name, "start_s": r["start_s"], "end_s": r["end_s"],
                      "seconds": r["seconds"], "thumb": thumb if (out_dir / thumb).exists() else None})
    return items


def baseline_observations(paths: ProjectPaths) -> Optional[dict[str, int]]:
    """Il modello com'era prima del primo giro accettato (None se non è ancora stato sostituito)."""
    rounds = sorted((d for d in recovery_dir(paths).glob("round*") if (d / "before" / "images.bin").exists()),
                    key=lambda d: int(d.name[5:]))
    return read_observations(rounds[0] / "before" / "images.bin") if rounds else None


def video_report(c: Ctx, rep: dict, state: dict, observations: dict[str, int]) -> dict:
    """Per ogni video, tratto per tratto: com'era all'inizio e com'è adesso.

    Un tratto è "fixed" se non resta niente di scoperto, "improved" se si è accorciato, "unchanged" se è rimasto com'era;
    "pending" finché non si è fatto nessun giro; "new" se prima non c'era.
    """
    before_obs = baseline_observations(c.paths)
    if before_obs is None:
        before_obs = observations                          # nessun giro accettato: l'inizio è adesso
    out: dict[str, dict] = {}
    for vid, seq in rep["seqs"].items():
        step, fps = rep["steps"][vid], rep["fps"][vid]
        base_files = [x[1] for x in seq if x[1].startswith("frame_")]
        extras = [x for x in seq if x[1].startswith("extra_")]
        support = c.project.video_by_id(vid).support
        now = rep["runs"][vid]
        item = {"original": {"total": len(base_files),
                             "now": sum(1 for x in seq if x[2] and x[1].startswith("frame_"))},
                "added": {"total": len(extras), "registered": sum(1 for x in extras if x[2])},
                "support": support, "sections": []}
        before_runs = []
        if not support:                                    # un video di supporto prima non c'era: niente "prima"
            before_seq = weak.sequence(base_files, vid, step, before_obs)
            item["original"]["before"] = sum(1 for x in before_seq if x[2])
            before_runs = weak.weak_runs(before_seq, fps, step)
        matched: set[int] = set()
        for r in before_runs:
            hits = [i for i, n in enumerate(now) if n["a"] <= r["b"] and n["b"] >= r["a"]]
            matched.update(hits)
            left = min(r["seconds"], sum(now[i]["seconds"] for i in hits))
            if not state["rounds"]:
                status = "pending"
            elif left == 0:
                status = "fixed"
            elif left < weak.IMPROVED * r["seconds"]:
                status = "improved"
            else:
                status = "unchanged"
            item["sections"].append({"start_s": r["start_s"], "end_s": r["end_s"], "before_s": r["seconds"],
                                     "after_s": round(left, 2), "status": status})
        for i, n in enumerate(now):
            if i not in matched:
                item["sections"].append({"start_s": n["start_s"], "end_s": n["end_s"], "before_s": 0.0,
                                         "after_s": n["seconds"], "status": "new" if state["rounds"] and not support else "pending"})
        item["sections"].sort(key=lambda x: x["start_s"])
        out[vid] = item
    return out


def _round_summary(r: dict) -> dict:
    keys = ("number", "kind", "video", "extra_frames", "new_registered", "recovered", "registered_before",
            "registered_after", "weak_s_before", "weak_s_after", "accepted", "step")
    return {k: r[k] for k in keys if k in r}


async def _summary(c: Ctx, rep: dict, state: dict) -> dict:
    return {"report": video_report(c, rep, state, read_observations(c.paths.refined / "images.bin")),
            "registered": rep["registered"], "total": rep["total"], "weak_seconds": weak.weak_seconds(rep["runs"]),
            "weak": await _thumbnails(c, rep["runs"]), "rounds": [_round_summary(r) for r in state["rounds"]],
            "per_video": stages.coverage_per_video(c, list(read_observations(c.paths.refined / "images.bin")))}


# ---------------------------------------------------------------- passaggi di un giro

def db_camera_id(database: Path, video_ids: list[str]) -> Optional[int]:
    """La camera che COLMAP usa già per uno di questi video."""
    con = sqlite3.connect(database)
    try:
        for vid in video_ids:
            row = con.execute("SELECT camera_id FROM images WHERE name LIKE ? LIMIT 1", (f"{vid}/%",)).fetchone()
            if row:
                return int(row[0])
    finally:
        con.close()
    return None


async def extract_selected(c: Ctx, vid: str, spec: dict, tmp: Path) -> int:
    """Estrae con un solo passaggio di ffmpeg i frame richiesti e li chiama col loro indice nel video."""
    out = c.paths.frames / vid
    wanted = [i for i in spec["frames"] if not (out / weak.extra_name(i)).exists()]
    if not wanted:
        return 0
    video = c.paths.input / c.project.video_by_id(vid).filename
    selected = weak.selected_indices(spec["windows"], spec["step"])
    ranges = "+".join(f"between(n\\,{lo}\\,{hi})" for lo, hi in spec["windows"])
    expr = f"select=({ranges})*not(mod(n\\,{spec['step']}))"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)

    def on_line(line: str) -> None:
        m = re.match(r"frame=(\d+)", line)
        if m:
            c.progress(None, f"{vid}: estrazione dei frame in più {m.group(1)} / {len(selected)}")

    base = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-progress", "pipe:1", "-nostats", "-y", "-i", str(video), "-an",
            "-vf", expr]
    tail = ["-frames:v", str(len(selected)), "-qscale:v", "2", str(tmp / "%06d.jpg")]
    rc = await c.cmd(base + ["-fps_mode", "vfr"] + tail, on_line=on_line, check=False)
    if rc != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True, exist_ok=True)
        await c.cmd(base + ["-vsync", "vfr"] + tail, on_line=on_line)
    want = set(wanted)
    moved = 0
    for k, index in enumerate(selected, start=1):        # il k-esimo file prodotto è il k-esimo indice selezionato
        f = tmp / f"{k:06d}.jpg"
        if index in want and f.exists():
            os.replace(f, out / weak.extra_name(index))
            moved += 1
    shutil.rmtree(tmp, ignore_errors=True)
    return moved


async def _features(c: Ctx, rdir: Path, new: dict[str, list[str]]) -> bool:
    """Feature delle immagini nuove (COLMAP salta quelle già nel database). Ritorna True se è comparsa una camera nuova."""
    by_device: dict[int, list] = {}
    for v in c.project.videos:
        by_device.setdefault(v.device, []).append(v)
    new_camera = False
    for device, vids in sorted(by_device.items()):
        names = [f"{v.id}/{n}" for v in vids for n in new.get(v.id, [])]
        if not names:
            continue
        list_file = rdir / f"images_device{device}.txt"
        list_file.write_text("\n".join(names) + "\n", encoding="utf-8")
        args = ["feature_extractor", "--database_path", str(c.paths.database), "--image_path", str(c.paths.frames),
                "--image_list_path", str(list_file)]
        camera = db_camera_id(c.paths.database, [v.id for v in vids if v.id not in new] + [v.id for v in vids])
        if camera is None:
            new_camera = True
            args += ["--ImageReader.single_camera", "1",
                     "--ImageReader.camera_model", vids[0].camera_model or c.s.camera_model]
        else:
            args += ["--ImageReader.existing_camera_id", str(camera)]
        c.progress(None, f"feature di {len(names)} immagini nuove")
        if await stages._colmap(c, args + ["--SiftExtraction.use_gpu", "1"]) != 0:
            if await stages._colmap(c, args + ["--SiftExtraction.use_gpu", "0"]) != 0:
                raise RuntimeError("Estrazione delle feature fallita: vedi logs/recover.log")
    return new_camera


async def _matching(c: Ctx, rdir: Path, new_names: set[str]) -> None:
    """Solo le coppie che coinvolgono le immagini nuove: le vicine nel tempo e quelle simili (vocab tree)."""
    marker = rdir / "matching.done"
    if marker.exists():
        return
    pairs: set[tuple[str, str]] = set()
    for v in c.project.videos:
        _f, step = video_params(c, v.id)
        ordered = sorted(weak.list_frames(c.paths.frames / v.id), key=lambda n: weak.source_index(n, step) or 0)
        pairs |= weak.temporal_pairs([f"{v.id}/{n}" for n in ordered], new_names)
    pair_file = rdir / "pairs.txt"
    pair_file.write_text("".join(f"{a} {b}\n" for a, b in sorted(pairs)), encoding="utf-8")
    query_file = rdir / "new_images.txt"
    query_file.write_text("\n".join(sorted(new_names)) + "\n", encoding="utf-8")
    c.progress(None, f"matching: {len(pairs)} coppie vicine nel tempo")
    await _colmap_gpu_or_cpu(c, ["matches_importer", "--database_path", str(c.paths.database), "--match_list_path",
                                 str(pair_file), "--match_type", "pairs"])
    c.progress(None, "matching: immagini simili in tutto il resto del video")
    import asyncio
    tree = await asyncio.to_thread(stages.ensure_vocab_tree)
    await _colmap_gpu_or_cpu(c, ["vocab_tree_matcher", "--database_path", str(c.paths.database),
                                 "--VocabTreeMatching.vocab_tree_path", str(tree),
                                 "--VocabTreeMatching.match_list_path", str(query_file),
                                 "--VocabTreeMatching.num_images", "60"])
    marker.write_text("ok")


async def _colmap_gpu_or_cpu(c: Ctx, args: list[str]) -> None:
    if await stages._colmap(c, args + ["--SiftMatching.use_gpu", "1"]) != 0:
        if await stages._colmap(c, args + ["--SiftMatching.use_gpu", "0"]) != 0:
            raise RuntimeError("Matching fallito: vedi logs/recover.log")


async def _register(c: Ctx, rdir: Path, new_count: int, registered_before: int, new_camera: bool) -> Path:
    """Il mapper di COLMAP CONTINUA il modello esistente; riprende dall'ultimo snapshot se era stato interrotto."""
    out, snaps_dir, marker = rdir / "model", rdir / "snapshots", rdir / "mapping.done"
    if marker.exists() and stages.model_dirs(out):
        return stages.pick_best_model(out)
    snaps_dir.mkdir(parents=True, exist_ok=True)
    snaps = sorted((d for d in snaps_dir.iterdir() if (d / "images.bin").exists()),
                   key=lambda d: int(d.name) if d.name.isdigit() else 0)
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)
    args = ["mapper", "--database_path", str(c.paths.database), "--image_path", str(c.paths.frames),
            "--input_path", str(snaps[-1] if snaps else c.paths.refined), "--output_path", str(out),
            "--Mapper.ba_global_function_tolerance=1e-6",
            "--Mapper.snapshot_path", str(snaps_dir), "--Mapper.snapshot_images_freq", "100"]
    if not new_camera:
        # le pose già calcolate restano ferme e la camera è già calibrata: si aggiungono solo le nuove
        args += ["--Mapper.fix_existing_images", "1", "--Mapper.ba_refine_focal_length", "0",
                 "--Mapper.ba_refine_extra_params", "0"]

    # il mapper riprova anche le foto di prima rimaste fuori: i frame nuovi possono fare da ponte
    left_out = max(0, len(stages.frame_files(c.paths)) - registered_before - new_count)
    target = new_count + left_out

    def on_line(line: str) -> None:
        m = re.search(r"Registering image #\d+ \((\d+)\)", line)
        if m:
            got = max(0, int(m.group(1)) - registered_before)
            c.progress(min(99.0, 100 * got / max(1, target)),
                       f"aggancio al modello: {got} immagini in più (da provare: {new_count} nuove e {left_out} rimaste fuori prima)")

    c.progress(None, "aggancio delle immagini nuove al modello")
    if await c.cmd(["colmap", *args], on_line=on_line, check=False) != 0:
        raise RuntimeError("La registrazione delle immagini nuove è fallita: vedi logs/recover.log")
    marker.write_text("ok")
    return stages.pick_best_model(out)


async def run_round(c: Ctx, plan: dict, state: dict) -> dict:
    """Un giro completo: estrazione, feature, matching, registrazione, controllo e sostituzione del modello."""
    number = len(state["rounds"]) + 1
    rdir = recovery_dir(c.paths) / f"round{number}"
    if state.get("current") != plan:
        shutil.rmtree(rdir, ignore_errors=True)
        state["current"] = plan
        save_state(c.paths, state)
    rdir.mkdir(parents=True, exist_ok=True)
    c.stage.metrics = {"plan": _plan_summary(plan, None)}
    before = read_observations(c.paths.refined / "images.bin")

    if plan["kind"] == "support":
        vid = plan["video"]
        await stages.extract_video(c, c.project.video_by_id(vid), plan["base_step"], plan["expected"])
        new = {vid: sorted(p.name for p in (c.paths.frames / vid).glob("frame_*.jpg"))}
    else:
        for vid, spec in plan["videos"].items():
            await extract_selected(c, vid, spec, rdir / f"tmp_{vid}")
        new = {vid: [weak.extra_name(i) for i in spec["frames"] if (c.paths.frames / vid / weak.extra_name(i)).exists()]
               for vid, spec in plan["videos"].items()}
    new_names = {f"{vid}/{n}" for vid, ns in new.items() for n in ns} - set(before)
    if not new_names:
        raise RuntimeError("Nessuna immagine nuova da aggiungere: controlla logs/recover.log")

    camera_marker = rdir / "new_camera"
    if await _features(c, rdir, new) or camera_marker.exists():
        camera_marker.write_text("1")
    await _matching(c, rdir, new_names)
    model = await _register(c, rdir, len(new_names), len(before), camera_marker.exists())

    after = read_observations(model / "images.bin")
    kept = len(set(before) & set(after)) / max(1, len(before))
    accepted = kept >= KEEP_RATIO and len(after) >= len(before)
    weak_before = weak.weak_seconds(analyze(c, before)["runs"])
    record = {"number": number, "kind": plan["kind"], "extra_frames": len(new_names),
              "new_registered": len(new_names & set(after)),
              "recovered": len(set(after) - set(before) - new_names),     # foto di prima che ora si agganciano
              "registered_before": len(before), "registered_after": len(after), "accepted": accepted,
              "weak_s_before": weak_before}
    if plan["kind"] == "support":
        record["video"] = plan["video"]
    else:
        record["step"] = {vid: spec["step"] for vid, spec in plan["videos"].items()}
        record["weak_before"] = plan["weak_before"]

    if accepted:
        staging = c.paths.colmap / "refined.new"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        for f in model.glob("*.bin"):
            shutil.copy(f, staging / f.name)
        shutil.rmtree(rdir / "before", ignore_errors=True)
        os.replace(c.paths.refined, rdir / "before")
        os.replace(staging, c.paths.refined)
        record["weak_s_after"] = weak.weak_seconds(analyze(c, after)["runs"])
        stages.write_features_signature(c.project, c.paths)
    else:
        record["weak_s_after"] = weak_before
    shutil.rmtree(rdir / "snapshots", ignore_errors=True)
    state["rounds"].append(record)
    state["current"] = None
    save_state(c.paths, state)
    return record


def _plan_summary(plan: dict, registered: Optional[int]) -> dict:
    out = {"kind": plan["kind"], "number": plan.get("number"),
           "extra_frames": plan.get("extra_frames", plan.get("expected")),
           "step": {vid: spec["step"] for vid, spec in plan.get("videos", {}).items()}}
    if registered is not None:
        out["est_seconds"] = round(weak.estimate_seconds(out["extra_frames"] or 0, registered))
    return out


async def _support_plan(c: Ctx, vid: str) -> dict:
    """Prepara un video di supporto: lettura, passo, coerenza con la camera dello stesso dispositivo."""
    video = c.project.video_by_id(vid)
    info = await stages._probe_one(c, c.paths.input / video.filename, f"probe_{vid}.json")
    probe = c.project.stage("probe").metrics.setdefault("videos", {})
    others = [v for v in c.project.videos if v.id != vid and v.id in probe]
    for other in others:
        if other.device == video.device and (probe[other.id]["width"], probe[other.id]["height"]) != (info["width"], info["height"]):
            raise RuntimeError(f"Il video di supporto ha risoluzione {info['width']}x{info['height']}, diversa dagli altri video del "
                               f"dispositivo {video.device}: ricaricalo assegnandogli un altro dispositivo.")
    stages.check_devices(c.project)
    step = video.frame_step or (probe[others[0].id]["step"] if others else c.s.frame_step)
    info["step"] = step
    info["expected_frames"] = -(-info["total_frames"] // step)
    probe[vid] = info
    c.flush()
    return {"kind": "support", "video": vid, "base_step": step, "expected": info["expected_frames"]}


def pending_support(c: Ctx, observations: dict[str, int], state: dict) -> list[str]:
    tried = {r.get("video") for r in state["rounds"] if r.get("kind") == "support"}
    return [v.id for v in c.project.videos
            if v.support and v.id not in tried and not any(n.startswith(v.id + "/") for n in observations)]


# ---------------------------------------------------------------- la fase

async def stage_recover(c: Ctx) -> dict:
    c.stage.metrics = {}
    finish_swap(c.paths)
    state = load_state(c.paths)
    if state.get("current"):                                  # un giro interrotto si porta a termine
        c.progress(None, "ripresa del giro di recupero interrotto")
        await run_round(c, state["current"], state)

    for vid in pending_support(c, read_observations(c.paths.refined / "images.bin"), state):
        record = await run_round(c, await _support_plan(c, vid), state)
        total = max(1, record["extra_frames"])
        if not record["accepted"] or record["new_registered"] / total < MIN_SUPPORT_RATIO:
            rep = analyze(c, read_observations(c.paths.refined / "images.bin"))
            c.stage.metrics = {**await _summary(c, rep, state), "decision": "new_video"}
            raise Blocked(
                f"Il video di supporto non si è agganciato al resto: solo {record['new_registered']} immagini su {total}. Serve un "
                "video che parta da una zona già ripresa bene e arrivi lentamente al punto da migliorare, con la stanza il più "
                "possibile com'era. Caricane un altro oppure continua senza.")

    while True:
        rep = analyze(c, read_observations(c.paths.refined / "images.bin"))
        if c.s.recovery == "off" or not weak.significant(rep["runs"], rep["total_seconds"]):
            c.stage.metrics = {}
            result = await _summary(c, rep, state)
            if c.s.recovery == "off" and not state["rounds"]:
                result["skipped"] = True
            return result
        plan = weak.plan_round(rep["seqs"], rep["runs"], rep["fps"], rep["steps"], state["rounds"])
        seconds = weak.weak_seconds(rep["runs"])
        if plan is None:
            c.stage.metrics = {**await _summary(c, rep, state), "decision": "new_video"}
            raise Blocked(
                f"Restano {seconds:.0f} s di video poco coperti e infittire i frame non basta più: per migliorarli serve un video "
                "nuovo di quei punti (vedi le miniature). Caricalo come video di supporto, oppure continua senza.")
        densify_done = sum(1 for r in state["rounds"] if r.get("kind") == "densify")
        if c.s.recovery == "ask" and c.project.recovery_approved <= densify_done:
            summary = _plan_summary(plan, rep["registered"])
            c.stage.metrics = {**await _summary(c, rep, state), "decision": "approve", "plan": summary}
            raise Blocked(
                f"Trovati {seconds:.0f} s di video poco coperti. Posso aggiungere {plan['extra_frames']} frame più fitti solo in "
                f"quei tratti (circa {max(1, round(summary['est_seconds'] / 60))} min, stima non ancora misurata) e agganciarli al "
                "modello già costruito.")
        await run_round(c, plan, state)
