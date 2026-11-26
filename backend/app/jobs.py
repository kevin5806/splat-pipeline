"""Lavori a richiesta fuori dalla pipeline (per ora la mesh). Li esegue il lavoratore, uno alla volta e solo quando
nessun progetto è in esecuzione. Lo stato sta in <progetto>/jobs/<tipo>.json."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

from . import config, history, store, viewcfg
from .runner import run_cmd
from .store import ProjectPaths

KINDS = {"mesh": "Mesh"}
ACTIVE = ("queued", "running")


def _file(name: str, kind: str) -> Path:
    return ProjectPaths(name).root / "jobs" / f"{kind}.json"


def load(name: str, kind: str) -> Optional[dict]:
    try:
        return json.loads(_file(name, kind).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save(name: str, kind: str, data: dict) -> None:
    f = _file(name, kind)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, f)


def all_for(name: str) -> dict:
    return {kind: job for kind in KINDS if (job := load(name, kind))}


def active(name: str) -> bool:
    return any(j.get("status") in ACTIVE for j in all_for(name).values())


def request(name: str, kind: str, params: dict) -> dict:
    if kind not in KINDS:
        raise ValueError("Lavoro sconosciuto.")
    if (load(name, kind) or {}).get("status") in ACTIVE:
        raise ValueError("Questo lavoro è già in coda o in esecuzione.")
    job = {"status": "queued", "params": params, "detail": "in coda", "requested": time.time()}
    save(name, kind, job)
    return job


def next_queued() -> Optional[tuple[str, str]]:
    found = []
    for p in store.list_projects():
        for kind, job in all_for(p.name).items():
            if job.get("status") == "queued":
                found.append((job.get("requested", 0), p.name, kind))
    return (min(found)[1], min(found)[2]) if found else None


def recover() -> None:
    """Un lavoro rimasto "in esecuzione" è stato interrotto: torna in coda (con AUTO_RESUME) oppure risulta fallito."""
    for p in store.list_projects():
        for kind, job in all_for(p.name).items():
            if job.get("status") == "running":
                job["status"] = "queued" if config.AUTO_RESUME else "failed"
                job["detail"] = "interrotto da un riavvio"
                save(p.name, kind, job)


def mesh_command(name: str, params: dict) -> list[str]:
    paths = ProjectPaths(name)
    source = paths.export / params.get("source", "splat_clean.ply")
    cmd = [sys.executable, "-m", "app.tools.mesh", str(source), str(paths.export / "mesh.ply"), "--depth", str(int(params.get("depth", 10)))]
    view = viewcfg.load(name)
    if params.get("metric") and view.get("scale") and not view.get("stale"):
        cmd += ["--scale", repr(float(view["scale"]))]
    if params.get("level") and view.get("up") and not view.get("stale"):
        cmd += ["--up", ",".join(repr(float(v)) for v in view["up"])]
    dataparser = paths.outputs / "main" / "splatfacto" / "run1" / "dataparser_transforms.json"
    crop_info = paths.export / "splat_clean.json"
    if dataparser.exists() and crop_info.exists() and (paths.refined / "images.bin").exists():
        cmd += ["--model", str(paths.refined), "--dataparser", str(dataparser), "--crop", str(crop_info)]
    return cmd


async def run(name: str, kind: str) -> None:
    job = load(name, kind)
    if not job or job.get("status") != "queued":
        return
    job.update(status="running", started=time.time(), detail="avvio")
    save(name, kind, job)
    history.add(name, "job_start", job=kind)
    log = ProjectPaths(name).logs / f"{kind}.log"

    def on_line(line: str) -> None:
        job["detail"] = line[:200]
        save(name, kind, job)

    try:
        if config.FAKE:
            await asyncio.sleep(1.0 / config.FAKE_SPEED)
            export = ProjectPaths(name).export
            export.mkdir(parents=True, exist_ok=True)
            (export / "mesh.ply").write_bytes(b"ply\nend_header\n")
            result = {"vertices": 1000, "triangles": 2000}
        else:
            await run_cmd(mesh_command(name, job.get("params", {})), log, on_line=on_line,
                          cwd=Path(__file__).resolve().parent.parent)
            result = json.loads((ProjectPaths(name).export / "mesh.json").read_text())
        job.update(status="done", ended=time.time(), detail="completata", result=result)
    except asyncio.CancelledError:
        job.update(status="queued" if config.AUTO_RESUME else "failed", detail="interrotto")
        save(name, kind, job)
        raise
    except Exception as e:  # noqa: BLE001 - l'errore va mostrato nel pannello
        job.update(status="failed", ended=time.time(), detail=str(e).strip().splitlines()[-1][:300] if str(e).strip() else "errore")
    save(name, kind, job)
    history.add(name, "job_end", job=kind, status=job["status"], duration=round(job["ended"] - job["started"]))
