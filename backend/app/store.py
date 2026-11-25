"""Persistenza dello stato dei progetti su disco (un state.json per progetto)."""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Optional

from . import config
from .models import Project, Settings

_lock = threading.RLock()


class ProjectPaths:
    """Percorsi standard di un progetto."""

    def __init__(self, name: str, root: Optional[Path] = None):
        self.name = name
        self.root = (root or config.PROJECTS_DIR) / name
        self.input = self.root / "input"
        self.frames = self.root / "frames"
        self.colmap = self.root / "colmap"
        self.database = self.colmap / "database.db"
        self.sparse = self.colmap / "sparse"
        self.snapshots = self.colmap / "snapshots"
        self.refined = self.colmap / "refined"
        self.dataset = self.root / "dataset"          # transforms.json + images
        self.outputs = self.root / "outputs"
        self.logs = self.root / "logs"
        self.export = self.root / "export"
        self.state = self.root / "state.json"

    def ensure(self) -> None:
        for p in (self.input, self.frames, self.colmap, self.outputs, self.logs, self.export):
            p.mkdir(parents=True, exist_ok=True)


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def save(p: Project) -> None:
    with _lock:
        p.updated = time.time()
        paths = ProjectPaths(p.name)
        paths.ensure()
        _atomic_write(paths.state, p.model_dump_json(indent=2))


def load(name: str) -> Optional[Project]:
    paths = ProjectPaths(name)
    if not paths.state.exists():
        return None
    with _lock:
        return Project.model_validate(json.loads(paths.state.read_text(encoding="utf-8")))


def list_projects() -> list[Project]:
    out: list[Project] = []
    if not config.PROJECTS_DIR.exists():
        return out
    for d in sorted(config.PROJECTS_DIR.iterdir()):
        if (d / "state.json").exists():
            try:
                p = load(d.name)
                if p:
                    out.append(p)
            except Exception:
                continue
    return sorted(out, key=lambda x: x.created, reverse=True)


def create(name: str, settings: Optional[Settings] = None) -> Project:
    if load(name) is not None:
        raise FileExistsError(name)
    p = Project(name=name, settings=settings or Settings())
    save(p)
    return p


def delete(name: str) -> None:
    paths = ProjectPaths(name)
    if paths.root.exists():
        shutil.rmtree(paths.root)
