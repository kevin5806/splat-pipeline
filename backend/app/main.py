"""API HTTP del pannello di controllo."""
from __future__ import annotations

import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, ValidationError

from . import config, store, system
from .models import NAME_RE, STAGES, Project, Settings
from .pipeline import Busy, Manager
from .store import ProjectPaths

manager = Manager()
ALLOWED_VIDEO = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}


@asynccontextmanager
async def lifespan(_: FastAPI):
    config.PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    await manager.recover()
    yield
    for name in list(manager.tasks):
        await manager.stop(name)
    await manager.stop_viewer()


app = FastAPI(title="splat-pipeline", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def token_guard(request: Request, call_next):
    if config.PANEL_TOKEN and request.url.path.startswith("/api/") and request.url.path != "/api/health":
        given = request.headers.get("x-token") or request.query_params.get("token")
        if given != config.PANEL_TOKEN:
            return JSONResponse({"detail": "Token mancante o errato."}, status_code=401)
    return await call_next(request)


def _get(name: str) -> Project:
    if not NAME_RE.match(name):
        raise HTTPException(404, "Progetto non trovato")
    p = store.load(name)
    if p is None:
        raise HTTPException(404, "Progetto non trovato")
    return p


def _view(p: Project) -> dict:
    d = p.model_dump()
    d["active"] = manager.active(p.name)
    d["artifacts"] = _artifacts(p.name)
    return d


def _artifacts(name: str) -> list[dict]:
    exp = ProjectPaths(name).export
    out = []
    if exp.exists():
        for f in sorted(exp.rglob("*")):
            if f.is_file():
                out.append({"path": str(f.relative_to(exp)).replace("\\", "/"), "size_mb": round(f.stat().st_size / 1e6, 1)})
    return out


# ------------------------------------------------------------------ API

@app.get("/api/health")
async def health():
    return {"ok": True, "fake": config.FAKE}


@app.get("/api/system")
async def system_info():
    info = system.info()
    info["viewer"] = {"project": manager.viewer_project, "port": config.VIEWER_PORT} if manager.viewer_project else None
    info["running"] = manager.any_active()
    info["viewer_port"] = config.VIEWER_PORT
    info["stages"] = [{"id": i, "label": l} for i, l in STAGES]
    return info


@app.get("/api/projects")
async def list_projects():
    return [_view(p) for p in store.list_projects()]


class NewProject(BaseModel):
    name: str
    settings: Optional[Settings] = None


@app.post("/api/projects", status_code=201)
async def create_project(body: NewProject):
    try:
        p = store.create(body.name, body.settings)
    except ValidationError as e:
        raise HTTPException(422, e.errors()[0]["msg"])
    except FileExistsError:
        raise HTTPException(409, "Esiste già un progetto con questo nome.")
    return _view(p)


@app.get("/api/projects/{name}")
async def get_project(name: str):
    return _view(_get(name))


@app.delete("/api/projects/{name}")
async def delete_project(name: str):
    _get(name)
    if manager.active(name):
        raise HTTPException(409, "Ferma il progetto prima di eliminarlo.")
    store.delete(name)
    return {"ok": True}


@app.put("/api/projects/{name}/settings")
async def update_settings(name: str, settings: Settings):
    p = _get(name)
    if manager.active(name):
        raise HTTPException(409, "Ferma il progetto prima di cambiare le impostazioni.")
    p.settings = settings
    store.save(p)
    return _view(p)


@app.post("/api/projects/{name}/video")
async def upload_video(name: str, file: UploadFile = File(...)):
    p = _get(name)
    if manager.active(name):
        raise HTTPException(409, "Ferma il progetto prima di cambiare il video.")
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_VIDEO:
        raise HTTPException(415, f"Formato non supportato ({ext or 'sconosciuto'}). Usa: {', '.join(sorted(ALLOWED_VIDEO))}")
    paths = ProjectPaths(name)
    paths.ensure()
    dst = paths.input / f"video{ext}"
    limit = int(config.MAX_UPLOAD_GB * 1e9)
    size = 0
    tmp = dst.with_suffix(".part")
    try:
        with tmp.open("wb") as f:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"File oltre il limite di {config.MAX_UPLOAD_GB:g} GB.")
                f.write(chunk)
        for old in paths.input.glob("video.*"):
            if old != tmp:
                old.unlink(missing_ok=True)
        tmp.replace(dst)
    finally:
        tmp.unlink(missing_ok=True)
    p.video = dst.name
    store.save(p)
    manager.reset_from(name, "probe")  # nuovo video = tutto da rifare
    return _view(_get(name))


@app.post("/api/projects/{name}/start")
async def start(name: str):
    _get(name)
    try:
        await manager.start(name)
    except Busy as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _view(_get(name))


@app.post("/api/projects/{name}/stop")
async def stop(name: str):
    _get(name)
    await manager.stop(name)
    return _view(_get(name))


@app.post("/api/projects/{name}/continue")
async def force_continue(name: str):
    _get(name)
    try:
        manager.force_continue(name)
        await manager.start(name)
    except (ValueError, Busy) as e:
        raise HTTPException(400, str(e))
    return _view(_get(name))


@app.post("/api/projects/{name}/reset/{stage}")
async def reset(name: str, stage: str):
    _get(name)
    try:
        manager.reset_from(name, stage)
    except Busy as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _view(_get(name))


@app.get("/api/projects/{name}/log/{stage}")
async def stage_log(name: str, stage: str, tail: int = 200):
    _get(name)
    if stage not in {s for s, _ in STAGES}:
        raise HTTPException(404, "Fase sconosciuta")
    f = ProjectPaths(name).logs / f"{stage}.log"
    if not f.exists():
        return PlainTextResponse("")
    lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
    return PlainTextResponse("\n".join(lines[-max(1, min(tail, 2000)):]))


@app.post("/api/projects/{name}/viewer")
async def start_viewer(name: str):
    _get(name)
    try:
        return await manager.start_viewer(name)
    except Busy as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/viewer")
async def stop_viewer():
    await manager.stop_viewer()
    return {"ok": True}


@app.get("/api/projects/{name}/files/{path:path}")
async def download(name: str, path: str):
    _get(name)
    base = ProjectPaths(name).export.resolve()
    target = (base / path).resolve()
    if base not in target.parents or not target.is_file():
        raise HTTPException(404, "File non trovato")
    return FileResponse(target, filename=target.name)


# ------------------------------------------------------------------ pannello (file statici)

@app.get("/{full_path:path}")
async def spa(full_path: str):
    if full_path.startswith("api/"):
        raise HTTPException(404)
    root = config.STATIC_DIR.resolve()
    f = (root / full_path).resolve()
    if full_path and root in f.parents and f.is_file():
        return FileResponse(f)
    for fallback in ("index.html", "200.html"):
        if (root / fallback).is_file():
            return FileResponse(root / fallback)
    return PlainTextResponse("Pannello non compilato: esegui `npm run build` in web/ oppure usa l'immagine Docker.", status_code=404)
