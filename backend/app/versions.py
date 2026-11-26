"""Versioni dei risultati: ogni volta che si rifà il training, il modello di prima diventa una versione e resta.

Nessuna versione viene sovrascritta: si cancella solo a mano. Di ciascuna si conservano
    export/versions/vN/     lo splat (.ply), le informazioni per allinearlo e info.json
    quality/versions/vN/    il confronto foto/modello e il punteggio
    outputs/versions/vN/    il modello di Nerfstudio (il più pesante: si può liberare da «Spazio su disco»)
Il risultato attuale è quello in export/splat_clean.ply e prende il numero successivo.

Tutto qui sono spostamenti di file, fatti dal sito quando si preme «Rifai da qui»: il lavoratore non è coinvolto.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Optional

from .store import ProjectPaths

SPLAT = "splat_clean.ply"
SIDE_FILES = (".json", ".dataparser.json")


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def export_dir(paths: ProjectPaths, number: int) -> Path:
    return paths.export / "versions" / f"v{number}"


def quality_dir(paths: ProjectPaths, number: int) -> Path:
    return paths.root / "quality" / "versions" / f"v{number}"


def model_dir(paths: ProjectPaths, number: int) -> Path:
    return paths.outputs / "versions" / f"v{number}"


def numbers(paths: ProjectPaths) -> list[int]:
    folder = paths.export / "versions"
    found = [int(m.group(1)) for d in folder.glob("v*") if (m := re.match(r"^v(\d+)$", d.name))] if folder.exists() else []
    return sorted(found)


def next_number(paths: ProjectPaths) -> int:
    """Il numero della prossima versione: non si riusa mai, nemmeno dopo averne cancellata una."""
    last = int(_read_json(paths.root / "versions.json").get("last", 0))
    return max([last, *numbers(paths)], default=0) + 1


def _remember(paths: ProjectPaths, number: int) -> None:
    (paths.root / "versions.json").write_text(json.dumps({"last": number}), encoding="utf-8")


def _training_info(run_dir: Path) -> dict:
    """Punteggi e impostazioni letti dai file del modello (restano anche quando le fasi vengono azzerate)."""
    out: dict = {}
    results = _read_json(run_dir / "eval.json").get("results") or {}
    for key, digits in (("psnr", 2), ("ssim", 3), ("lpips", 3)):
        if isinstance(results.get(key), (int, float)):
            out[key] = round(results[key], digits)
    try:
        config = (run_dir / "splatfacto" / "run1" / "config.yml").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    values = {k: m.group(1) for k in ("max_num_iterations", "densify_grad_thresh", "reset_alpha_every", "stop_split_at")
              if (m := re.search(rf"^\s*{k}:\s*([\d.]+)\s*$", config, re.M))}
    if "max_num_iterations" in values:
        out["steps"] = int(float(values["max_num_iterations"]))
    if "densify_grad_thresh" in values:
        out["quality"] = "high" if float(values["densify_grad_thresh"]) < 0.0008 else "standard"
    if "reset_alpha_every" in values:
        out["reset_every"] = int(float(values["reset_alpha_every"])) * 100
    return out


def describe(ply: Path, info_file: Path, run_dir: Path, quality: Path) -> dict:
    """Che cos'è un risultato: dimensione, gaussiane, punteggi, impostazioni."""
    out = _training_info(run_dir)
    crop = _read_json(info_file)
    if crop.get("kept"):
        out["gaussians"] = int(crop["kept"])
    views = _read_json(quality / "views.json")
    if views.get("count"):
        out.setdefault("psnr", views.get("mean"))
        out["views"] = views["count"]
    try:
        st = ply.stat()
        out["size_mb"] = round(st.st_size / 1e6, 1)
        out["created"] = st.st_mtime
    except OSError:
        pass
    return out


def archive(paths: ProjectPaths, ply: Path, side_stem: str, run_dir: Optional[Path], quality: Optional[Path]) -> int:
    """Sposta un risultato in una nuova versione e ne restituisce il numero."""
    number = next_number(paths)
    target = export_dir(paths, number)
    target.mkdir(parents=True, exist_ok=True)
    info = describe(ply, ply.with_name(f"{side_stem}.json"), run_dir or paths.outputs / "nessuno",
                    quality or paths.root / "nessuno")
    os.replace(ply, target / SPLAT)
    for suffix in SIDE_FILES:
        source = ply.with_name(f"{side_stem}{suffix}")
        if source.exists():
            os.replace(source, target / f"splat_clean{suffix}")
    if quality is not None and quality.exists():
        dest = quality_dir(paths, number)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(dest, ignore_errors=True)
        os.replace(quality, dest)
    if run_dir is not None and run_dir.exists():
        dest = model_dir(paths, number)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(dest, ignore_errors=True)
        os.replace(run_dir, dest)
    info.update(number=number, archived=time.time())
    (target / "info.json").write_text(json.dumps(info), encoding="utf-8")
    _remember(paths, number)
    return number


def migrate(paths: ProjectPaths) -> Optional[int]:
    """Il «modello precedente» salvato con il sistema di prima diventa una versione."""
    old = paths.export / "splat_clean_precedente.ply"
    if not old.exists():
        return None
    previous_model, previous_quality = paths.outputs / "main.prev", paths.root / "quality" / "previous"
    return archive(paths, old, "splat_clean_precedente",
                   previous_model if previous_model.exists() else None,
                   previous_quality if previous_quality.exists() else None)


def listing(name: str) -> dict:
    paths = ProjectPaths(name)
    migrate(paths)
    items = []
    for number in numbers(paths):
        folder = export_dir(paths, number)
        info = _read_json(folder / "info.json")
        info.update(number=number, file=f"versions/v{number}/{SPLAT}" if (folder / SPLAT).exists() else None,
                    has_comparison=(quality_dir(paths, number) / "views.json").exists(),
                    has_model=model_dir(paths, number).exists(), note=info.get("note", ""))
        items.append(info)
    current = None
    ply = paths.export / SPLAT
    if ply.exists():
        current = describe(ply, paths.export / "splat_clean.json", paths.outputs / "main", paths.root / "quality" / "final")
        current.update(number=next_number(paths), file=SPLAT, current=True,
                       has_comparison=(paths.root / "quality" / "final" / "views.json").exists())
    return {"current": current, "versions": items}


def set_note(name: str, number: int, note: str) -> None:
    folder = export_dir(ProjectPaths(name), number)
    if not folder.is_dir():
        raise KeyError(number)
    info = _read_json(folder / "info.json")
    info["note"] = note.strip()[:200]
    (folder / "info.json").write_text(json.dumps(info), encoding="utf-8")


def delete(name: str, number: int) -> None:
    paths = ProjectPaths(name)
    if number not in numbers(paths):
        raise KeyError(number)
    _remember(paths, max(number, next_number(paths) - 1))
    for folder in (export_dir(paths, number), quality_dir(paths, number), model_dir(paths, number)):
        shutil.rmtree(folder, ignore_errors=True)
