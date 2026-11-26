"""API HTTP del pannello di controllo."""
from __future__ import annotations

import asyncio
import re
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from pydantic import BaseModel, Field, ValidationError

from . import config, control, estimate, extras, history, jobs, monitor, store, system
from .models import NAME_RE, STAGES, Project, Settings, Video
from .pipeline import Busy, Manager
from .store import ProjectPaths

manager = Manager()
# La pagina HTML si ricontrolla sempre: dopo un aggiornamento del sito il browser la prende nuova senza F5.
NO_CACHE = {"Cache-Control": "no-cache"}
ALLOWED_VIDEO = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}


@asynccontextmanager
async def lifespan(_: FastAPI):
    config.PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    # Il lavoratore sta nello stesso processo solo se richiesto (sviluppo, un solo container) e mai in un pannello di sola
    # lettura; in produzione gira in un container a parte, così aggiornare il sito non interrompe i lavori.
    worker = asyncio.create_task(manager.worker_loop(embedded=True)) if config.EMBEDDED_WORKER and not config.READ_ONLY else None
    mon = asyncio.create_task(monitor.monitor_loop())
    yield
    mon.cancel()
    if worker:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


app = FastAPI(title="splat-pipeline", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def read_only_guard(request: Request, call_next):
    if config.READ_ONLY and request.url.path.startswith("/api/") and request.url.path != "/api/estimate" \
            and request.method in ("POST", "PUT", "DELETE", "PATCH"):
        return JSONResponse({"detail": "Pannello in sola lettura."}, status_code=403)
    return await call_next(request)


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
    d["active"] = p.status in ("queued", "running")
    d["queue_position"] = manager.queue_rank(p.name) if p.status == "queued" else None
    d["artifacts"] = _artifacts(p.name)
    d["size_mb"] = store.disk_mb(p.name)
    try:
        d["estimate"] = estimate.project_estimate(p)
    except Exception:  # noqa: BLE001 - la stima è un di più: non deve mai rompere la pagina
        d["estimate"] = None
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
    worker = control.worker_status()
    remote = worker.pop("system", None)
    if remote:      # lavoratore separato: GPU, CPU e RAM che contano sono quelle del suo container
        info.update({k: v for k, v in remote.items() if k != "fake"})
    info["viewer"] = control.viewer_status()
    info["worker"] = worker
    info["running"] = manager.any_active()
    info["viewer_port"] = config.VIEWER_PORT
    info["stages"] = [{"id": i, "label": l} for i, l in STAGES]
    info["read_only"] = config.READ_ONLY
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


class EstimateVideo(BaseModel):
    name: str = Field("video", max_length=120)
    width: int = Field(..., ge=16, le=16384)
    height: int = Field(..., ge=16, le=16384)
    fps: float = Field(30.0, gt=0, le=480)
    duration_s: float = Field(..., gt=0, le=6 * 86400)
    device: int = Field(1, ge=1, le=8)
    camera_model: Optional[str] = None
    frame_step: Optional[int] = Field(None, ge=1, le=300)


class EstimateIn(BaseModel):
    settings: Settings = Field(default_factory=Settings)
    videos: list[EstimateVideo] = Field(..., min_length=1, max_length=12)


@app.post("/api/estimate")
async def estimate_preview(body: EstimateIn):
    """Stima dei tempi per video non ancora caricati (non crea nulla)."""
    for v in body.videos:
        if v.camera_model is not None and v.camera_model not in CAMERA_MODELS:
            raise HTTPException(422, f"Modello di camera non valido. Usa: {', '.join(CAMERA_MODELS)}")
    return {"estimate": estimate.preview(body.settings, [v.model_dump() for v in body.videos])}


class Cleanup(BaseModel):
    status: str = "done"            # per ora solo i completati
    include_external: bool = False  # i progetti monitorati hanno i file altrove: di default non si toccano
    dry_run: bool = False           # solo l'elenco, senza cancellare


@app.post("/api/projects/cleanup")
async def cleanup(body: Cleanup):
    """Elimina in blocco i progetti completati (mai quelli in esecuzione o in coda)."""
    if body.status != "done":
        raise HTTPException(400, "Si possono rimuovere in blocco solo i progetti completati.")
    picked = [p for p in store.list_projects()
              if p.status == "done" and not manager.active(p.name) and (body.include_external or not p.external)]
    items = [{"name": p.name, "size_mb": store.disk_mb(p.name, ttl=0)} for p in picked]
    if not body.dry_run:
        for p in picked:
            store.delete(p.name)
    return {"deleted" if not body.dry_run else "would_delete": items, "freed_mb": round(sum(i["size_mb"] for i in items), 1)}


@app.get("/api/projects/{name}")
async def get_project(name: str):
    return _view(_get(name))


@app.delete("/api/projects/{name}")
async def delete_project(name: str):
    _get(name)
    if manager.active(name):
        raise HTTPException(409, "Ferma il progetto prima di eliminarlo.")
    if jobs.active(name):
        raise HTTPException(409, "C'è un lavoro (mesh) in corso su questo progetto: aspetta che finisca.")
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


CAMERA_MODELS = ("OPENCV", "OPENCV_FISHEYE", "SIMPLE_RADIAL")


@app.post("/api/projects/{name}/videos")
@app.post("/api/projects/{name}/video")  # nome della versione precedente, equivalente
async def add_video(name: str, file: UploadFile = File(...), device: int = Form(1), camera_model: Optional[str] = Form(None)):
    """Aggiunge un video normale: l'elaborazione ricomincia da capo."""
    await _store_video(name, file, device, camera_model, None, support=False)
    manager.reset_from(name, "probe", log=False)  # video cambiati = tutto da rifare
    history.add(name, "video_added")
    return _view(_get(name))


@app.post("/api/projects/{name}/support-videos")
async def add_support_video(name: str, file: UploadFile = File(...), device: int = Form(1),
                            camera_model: Optional[str] = Form(None), frame_step: Optional[int] = Form(None)):
    """Aggiunge un video di supporto: si aggancia al modello esistente, senza rifare la ricostruzione."""
    p = _get(name)
    if p.stage("refine").status != "done":
        raise HTTPException(400, "Il video di supporto si può aggiungere quando le camere sono già ricostruite.")
    if frame_step is not None and not 1 <= frame_step <= 300:
        raise HTTPException(422, "frame_step deve essere tra 1 e 300 (un frame ogni N).")
    await _store_video(name, file, device, camera_model, frame_step, support=True)
    manager.reopen_recovery(name)
    return _view(_get(name))


async def _store_video(name: str, file: UploadFile, device: int, camera_model: Optional[str], frame_step: Optional[int],
                       support: bool) -> None:
    p = _get(name)
    if p.external:
        raise HTTPException(400, "Progetto monitorato in sola lettura.")
    if manager.active(name):
        raise HTTPException(409, "Ferma il progetto prima di aggiungere un video.")
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_VIDEO:
        raise HTTPException(415, f"Formato non supportato ({ext or 'sconosciuto'}). Usa: {', '.join(sorted(ALLOWED_VIDEO))}")
    if not 1 <= device <= 8:
        raise HTTPException(422, "Il dispositivo deve essere un numero da 1 a 8.")
    if camera_model in ("", "null", "default"):
        camera_model = None
    if camera_model is not None and camera_model not in CAMERA_MODELS:
        raise HTTPException(422, f"Modello di camera non valido. Usa: {', '.join(CAMERA_MODELS)}")
    if len(p.videos) >= 12:
        raise HTTPException(400, "Massimo 12 video per progetto.")
    paths = ProjectPaths(name)
    paths.ensure()
    vid = p.next_video_id()
    dst = paths.input / f"{vid}{ext}"
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
        tmp.replace(dst)
    finally:
        tmp.unlink(missing_ok=True)
    if config.FAKE:
        from .fake import fake_info
        info = fake_info(len(p.videos))
    else:
        from .stages import ffprobe_info
        info = await ffprobe_info(dst)
    p = _get(name)                                  # riletto: il caricamento può durare minuti
    if manager.active(name):
        dst.unlink(missing_ok=True)
        raise HTTPException(409, "Il progetto è partito durante il caricamento: fermalo e ricarica il video.")
    p.videos.append(Video(id=vid, filename=dst.name, name=Path(file.filename or dst.name).name[:120],
                          size_mb=round(size / 1e6, 1), device=device, camera_model=camera_model, info=info,
                          frame_step=frame_step, support=support))
    store.save(p)


class VideoPatch(BaseModel):
    device: Optional[int] = None
    camera_model: Optional[str] = None  # null = quello del progetto
    frame_step: Optional[int] = None    # null = calcolato dal progetto


@app.patch("/api/projects/{name}/videos/{vid}")
async def patch_video(name: str, vid: str, body: VideoPatch):
    p = _get(name)
    if p.external or manager.active(name):
        raise HTTPException(409, "Ferma il progetto prima di modificare i video.")
    try:
        v = p.video_by_id(vid)
    except KeyError:
        raise HTTPException(404, "Video non trovato")
    if body.device is not None:
        if not 1 <= body.device <= 8:
            raise HTTPException(422, "Il dispositivo deve essere un numero da 1 a 8.")
        v.device = body.device
    if "camera_model" in body.model_fields_set:
        if body.camera_model not in (None, *CAMERA_MODELS):
            raise HTTPException(422, f"Modello di camera non valido. Usa: {', '.join(CAMERA_MODELS)}")
        v.camera_model = body.camera_model
    if "frame_step" in body.model_fields_set:
        if body.frame_step is not None and not 1 <= body.frame_step <= 300:
            raise HTTPException(422, "frame_step deve essere tra 1 e 300 (un frame ogni N).")
        v.frame_step = body.frame_step
    if v.support and _support_used(name, v.id):
        raise HTTPException(409, "Questo video di supporto è già stato usato: per cambiarlo usa «Rifai da qui» sul bundle adjustment.")
    store.save(p)
    if not v.support:
        manager.reset_from(name, "probe", log=False)
    return _view(_get(name))


@app.delete("/api/projects/{name}/videos/{vid}")
async def delete_video(name: str, vid: str):
    p = _get(name)
    if p.external or manager.active(name):
        raise HTTPException(409, "Ferma il progetto prima di togliere un video.")
    try:
        v = p.video_by_id(vid)
    except KeyError:
        raise HTTPException(404, "Video non trovato")
    paths = ProjectPaths(name)
    if v.support and _support_used(name, v.id):
        raise HTTPException(409, "Questo video di supporto è già nel modello: per toglierlo usa «Rifai da qui» sull'analisi dei video.")
    (paths.input / v.filename).unlink(missing_ok=True)
    shutil.rmtree(paths.frames / v.id, ignore_errors=True)
    p.videos = [x for x in p.videos if x.id != vid]
    store.save(p)
    if not v.support:
        manager.reset_from(name, "probe", log=False)
    return _view(_get(name))


def _support_used(name: str, vid: str) -> bool:
    from .recover import load_state
    state = load_state(ProjectPaths(name))
    current = state.get("current") or {}
    return current.get("video") == vid or any(r.get("video") == vid for r in state["rounds"])


@app.post("/api/projects/{name}/recover/approve")
async def approve_recovery(name: str):
    _get(name)
    try:
        manager.approve_recovery(name)
        await manager.start(name)
    except (ValueError, Busy) as e:
        raise HTTPException(400, str(e))
    return _view(_get(name))


@app.get("/api/projects/{name}/thumb/{file}")
async def thumbnail(name: str, file: str):
    _get(name)
    base = (ProjectPaths(name).colmap / "recovery" / "thumbs").resolve()
    target = (base / file).resolve()
    if target.parent != base or not target.is_file():
        raise HTTPException(404, "Miniatura non trovata")
    return FileResponse(target)


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
        await manager.request_viewer(name)
        return {"requested": True, "project": name, "port": config.VIEWER_PORT}
    except Busy as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/viewer")
async def stop_viewer():
    control.request_viewer("stop")
    return {"ok": True}


@app.get("/api/projects/{name}/files/{path:path}")
async def download(name: str, path: str):
    _get(name)
    base = ProjectPaths(name).export.resolve()
    target = (base / path).resolve()
    if base not in target.parents or not target.is_file():
        raise HTTPException(404, "File non trovato")
    return FileResponse(target, filename=target.name)


app.include_router(extras.router)


# ------------------------------------------------------------------ pannello (file statici)

@app.get("/{full_path:path}")
async def spa(full_path: str, request: Request):
    if full_path.startswith("api/"):
        raise HTTPException(404)
    root = config.STATIC_DIR.resolve()
    f = (root / full_path).resolve()
    if full_path and root in f.parents and f.is_file():
        # i file di SvelteKit hanno l'impronta nel nome: non cambiano mai, si possono tenere a lungo in cache
        headers = {"Cache-Control": "public, max-age=31536000, immutable"} if "/immutable/" in full_path else NO_CACHE
        return FileResponse(f, headers=headers)
    # cartelle con una pagina propria (es. /viewer/): serve index.html; senza "/" finale reindirizza,
    # altrimenti i percorsi relativi della pagina (./lib/...) si romperebbero
    if full_path and root in f.parents and f.is_dir() and (f / "index.html").is_file():
        if not full_path.endswith("/"):
            return RedirectResponse(f"/{full_path}/" + (f"?{request.url.query}" if request.url.query else ""), status_code=307)
        return FileResponse(f / "index.html", headers=NO_CACHE)
    for fallback in ("index.html", "200.html"):
        if (root / fallback).is_file():
            return FileResponse(root / fallback, headers=NO_CACHE)
    return PlainTextResponse("Pannello non compilato: esegui `npm run build` in web/ oppure usa l'immagine Docker.", status_code=404)
