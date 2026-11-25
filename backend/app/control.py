"""Canale di controllo tra il sito (API) e il lavoratore che esegue i progetti.

Il sito e il lavoratore possono essere processi (o container) diversi: comunicano solo tramite file in DATA_DIR/control,
così aggiornare o riavviare il sito non tocca il lavoro in corso.

    control/<progetto>.stop   richiesta di fermare un progetto (la crea il sito, la consuma il lavoratore)
    control/viewer.request    {"action": "start"|"stop", "project": nome}
    control/viewer.json       viewer attivo, aggiornato dal lavoratore ad ogni giro
    control/worker.json       battito del lavoratore: se non si aggiorna da qualche secondo, il lavoratore non c'è
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

from . import config

HEARTBEAT_STALE = 10.0  # secondi


def _dir() -> Path:
    d = config.DATA_DIR / "control"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


def _read(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------- stop
def request_stop(name: str) -> None:
    (_dir() / f"{name}.stop").write_text(str(time.time()))


def pop_stops() -> list[str]:
    out = []
    for f in _dir().glob("*.stop"):
        out.append(f.name[: -len(".stop")])
        f.unlink(missing_ok=True)
    return out


# ---------------------------------------------------------------- viewer
def request_viewer(action: str, project: Optional[str] = None) -> None:
    _write(_dir() / "viewer.request", {"action": action, "project": project, "ts": time.time()})


def pop_viewer_request() -> Optional[dict]:
    f = _dir() / "viewer.request"
    req = _read(f)
    f.unlink(missing_ok=True)
    return req


def set_viewer(project: Optional[str], port: Optional[int] = None) -> None:
    f = _dir() / "viewer.json"
    if project is None:
        f.unlink(missing_ok=True)
    else:
        _write(f, {"project": project, "port": port, "ts": time.time()})


def viewer_status() -> Optional[dict]:
    d = _read(_dir() / "viewer.json")
    if not d or time.time() - d.get("ts", 0) > HEARTBEAT_STALE:
        return None
    return {"project": d["project"], "port": d["port"]}


# ---------------------------------------------------------------- battito del lavoratore
def beat(busy: Optional[str], embedded: bool) -> None:
    _write(_dir() / "worker.json", {"pid": os.getpid(), "ts": time.time(), "busy": busy, "embedded": embedded})


def worker_gone() -> None:
    (_dir() / "worker.json").unlink(missing_ok=True)


def worker_status() -> dict:
    d = _read(_dir() / "worker.json")
    if not d:
        return {"alive": False, "busy": None, "embedded": False}
    alive = time.time() - d.get("ts", 0) <= HEARTBEAT_STALE
    return {"alive": alive, "busy": d.get("busy") if alive else None, "embedded": bool(d.get("embedded"))}
