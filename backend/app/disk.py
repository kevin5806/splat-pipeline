"""Quanto occupa ogni parte di un progetto e cosa si può cancellare senza perdere il risultato.

Video originali, frame, database, modello rifinito, training principale ed esportazioni non sono mai cancellabili da qui.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from .models import Project
from .store import ProjectPaths


def _size(p: Path) -> int:
    if p.is_symlink():
        return 0                                  # un collegamento non occupa il file che indica
    if p.is_file():
        return p.stat().st_size
    total = 0
    for dirpath, _dirs, files in os.walk(p):
        for f in files:
            try:
                st = os.lstat(os.path.join(dirpath, f))
                total += st.st_size
            except OSError:
                pass
    return total


def _recovery_extras(paths: ProjectPaths) -> list[Path]:
    """Copie di lavoro dei giri di recupero. Il PRIMO modello "before" resta: serve al confronto prima/dopo."""
    rounds = sorted((paths.colmap / "recovery").glob("round*"), key=lambda d: int(d.name[5:]) if d.name[5:].isdigit() else 0)
    baseline = next((r / "before" for r in rounds if (r / "before" / "images.bin").exists()), None)
    out = []
    for r in rounds:
        for sub in ("model", "snapshots", "before"):
            d = r / sub
            if d.exists() and d != baseline:
                out.append(d)
    return out


def cleanable(p: Project, paths: ProjectPaths) -> dict[str, dict]:
    """Le voci cancellabili adesso, con la conseguenza di ciascuna."""
    done = {s.id for s in p.stages if s.status == "done"}
    recovery_running = p.stage("recover").status in ("running", "interrupted")
    items: dict[str, dict] = {}

    def add(key: str, label: str, effect: str, targets: list[Path]) -> None:
        targets = [t for t in targets if t.exists()]
        if targets:
            items[key] = {"label": label, "effect": effect, "targets": targets, "bytes": sum(_size(t) for t in targets)}

    if "test" in done or "train" in done:
        add("test", "Modello del test rapido", "I risultati del test restano; per rivederlo andrà rifatta la fase del test.",
            [paths.outputs / "test"])
    add("previous", "Training interrotto prima dell'esportazione", "È un modello mai esportato, tenuto per sicurezza.",
        [paths.outputs / "main.prev"])
    add("version_models", "Modelli di Nerfstudio delle versioni vecchie",
        "Delle versioni restano splat, confronto e punteggi; non si potrà più riesportarle né aprirle col viewer di Nerfstudio.",
        [paths.outputs / "versions"])
    if "refine" in done:
        add("sparse", "Modelli intermedi della ricostruzione",
            "Per rifare il bundle adjustment andrà prima rifatta la «Ricostruzione delle camere».",
            [paths.sparse, paths.snapshots])
    if not recovery_running and not (p.status in ("running", "queued")):
        add("recovery", "Copie di lavoro del recupero",
            "Resta il modello di partenza per il confronto prima/dopo; non si può più tornare ai giri intermedi.",
            _recovery_extras(paths))
    return items


def usage(p: Project) -> dict:
    paths = ProjectPaths(p.name)
    groups = [("Video originali", [paths.input]), ("Frame estratti", [paths.frames]),
              ("Dati di COLMAP", [paths.colmap]), ("Dataset per il training", [paths.dataset]),
              ("Training e modelli", [paths.outputs]), ("Risultati esportati", [paths.export]),
              ("Log, confronti e anteprime", [paths.logs, paths.root / "quality", paths.root / "preview"])]
    parts = [{"label": label, "mb": round(sum(_size(d) for d in dirs if d.exists()) / 1e6, 1)} for label, dirs in groups]
    free = shutil.disk_usage(paths.root).free if paths.root.exists() else 0
    return {"parts": parts, "total_mb": round(sum(x["mb"] for x in parts), 1), "free_gb": round(free / 1e9, 1),
            "cleanable": [{"id": k, "label": v["label"], "effect": v["effect"], "mb": round(v["bytes"] / 1e6, 1)}
                          for k, v in cleanable(p, paths).items()]}


def clean(p: Project, ids: list[str]) -> dict:
    paths = ProjectPaths(p.name)
    available = cleanable(p, paths)
    unknown = [i for i in ids if i not in available]
    if unknown:
        raise ValueError(f"Non cancellabile adesso: {', '.join(unknown)}")
    root = paths.root.resolve()
    freed = 0
    for key in ids:
        for t in available[key]["targets"]:
            if root not in t.resolve().parents:      # mai fuori dalla cartella del progetto
                continue
            freed += _size(t)
            if t.is_dir() and not t.is_symlink():
                shutil.rmtree(t, ignore_errors=True)
            else:
                t.unlink(missing_ok=True)
    return {"freed_mb": round(freed / 1e6, 1), "removed": ids}
