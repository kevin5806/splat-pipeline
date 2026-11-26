"""Cronologia di un progetto: una riga JSON per evento, in <progetto>/history.jsonl.

La scrivono sia il lavoratore (fasi) sia il sito (decisioni dell'utente): si aggiunge soltanto, una riga alla volta.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .store import ProjectPaths

MAX_READ = 500


def path(name: str) -> Path:
    return ProjectPaths(name).root / "history.jsonl"


def add(name: str, event: str, **data) -> None:
    """Annota un evento. Un errore qui non deve mai fermare il lavoro."""
    try:
        f = path(name)
        if not f.parent.exists():
            return
        row = {"ts": time.time(), "event": event, **{k: v for k, v in data.items() if v is not None}}
        with f.open("a", encoding="utf-8") as out:
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
    except (OSError, TypeError, ValueError):
        pass


def read(name: str, limit: int = MAX_READ) -> list[dict]:
    try:
        lines = path(name).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out = []
    for line in lines[-limit:]:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out
