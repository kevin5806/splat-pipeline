"""API delle funzioni aggiuntive: confronto del test, cronologia, anteprime, mappa, spazio su disco, viewer, mesh,
ritaglio, scala e notifiche. Il controllo del token e la sola lettura valgono anche qui (middleware di main.py)."""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import disk, history, jobs, mapdata, notify, store, versions, viewcfg
from .models import NAME_RE, Project
from .store import ProjectPaths

router = APIRouter()
_IMAGE = re.compile(r"^[A-Za-z0-9_]+\.jpg$")
_PLY = re.compile(r"^(?:[A-Za-z0-9_]+/){0,2}[A-Za-z0-9_]+\.ply$")
QUALITY_KINDS = ("test", "final")
_VERSION_KIND = re.compile(r"^v(\d{1,4})$")


def _get(name: str) -> Project:
    p = store.load(name) if NAME_RE.match(name) else None
    if p is None:
        raise HTTPException(404, "Progetto non trovato")
    return p


def _inside(base: Path, *parts: str) -> Path:
    base = base.resolve()
    target = base.joinpath(*parts).resolve()
    if base not in target.parents or not target.is_file():
        raise HTTPException(404, "File non trovato")
    return target


# ------------------------------------------------------------------ qualità
def _quality_folder(name: str, kind: str) -> Optional[Path]:
    paths = ProjectPaths(name)
    if kind in QUALITY_KINDS:
        return paths.root / "quality" / kind
    m = _VERSION_KIND.match(kind)
    return versions.quality_dir(paths, int(m.group(1))) if m else None


@router.get("/api/projects/{name}/quality")
async def quality(name: str):
    _get(name)
    paths = ProjectPaths(name)
    versions.migrate(paths)
    out = {}
    for kind in (*QUALITY_KINDS, *(f"v{n}" for n in reversed(versions.numbers(paths)))):
        try:
            out[kind] = json.loads((_quality_folder(name, kind) / "views.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    return out


@router.get("/api/projects/{name}/quality/{kind}/{file}")
async def quality_image(name: str, kind: str, file: str):
    _get(name)
    folder = _quality_folder(name, kind)
    if folder is None or not _IMAGE.match(file):
        raise HTTPException(404, "File non trovato")
    return FileResponse(_inside(folder, file))


# ------------------------------------------------------------------ versioni del modello
@router.get("/api/projects/{name}/versions")
async def list_versions(name: str):
    _get(name)
    return versions.listing(name)


class VersionNote(BaseModel):
    note: str = ""


@router.patch("/api/projects/{name}/versions/{number}")
async def annotate_version(name: str, number: int, body: VersionNote):
    _get(name)
    try:
        versions.set_note(name, number, body.note)
    except KeyError:
        raise HTTPException(404, "Versione non trovata")
    return {"ok": True}


@router.delete("/api/projects/{name}/versions/{number}")
async def delete_version(name: str, number: int):
    _get(name)
    try:
        versions.delete(name, number)
    except KeyError:
        raise HTTPException(404, "Versione non trovata")
    history.add(name, "version_deleted", number=number)
    return {"ok": True}


# ------------------------------------------------------------------ anteprime del training
def preview_angles(project: Project) -> list[dict]:
    """Le inquadrature con almeno un'anteprima: {suffix, real, steps, video, t}."""
    from .tools.eval_views import frame_time
    folder = ProjectPaths(project.name).root / "preview"
    try:
        names = {v["suffix"]: v["name"] for v in json.loads((folder / "views.json").read_text(encoding="utf-8"))}
    except (OSError, ValueError, KeyError, TypeError):
        names = {}
    probe = project.stage("probe").metrics.get("videos", {})
    out = []
    for suffix in ["", *[f"_a{k}" for k in range(2, 9)]]:
        pattern = re.compile(rf"^step_(\d+){suffix}\.jpg$")
        steps = sorted(int(m.group(1)) for f in folder.glob("step_*.jpg") if (m := pattern.match(f.name)))
        if not steps:
            continue
        item = {"suffix": suffix, "real": (folder / f"real{suffix}.jpg").exists(), "steps": steps, "video": None, "t": None}
        if suffix in names:
            vid, seconds = frame_time(names[suffix], probe)
            numbers = [v.id for v in project.videos]
            if vid in numbers:
                item.update(video=numbers.index(vid) + 1, t=seconds)
        out.append(item)
    return out


@router.get("/api/projects/{name}/preview")
async def preview(name: str):
    angles = preview_angles(_get(name))
    first = angles[0] if angles else {"steps": [], "real": False}
    return {"steps": first["steps"], "real": first["real"], "angles": angles}


@router.get("/api/projects/{name}/preview/{file}")
async def preview_image(name: str, file: str):
    _get(name)
    if not _IMAGE.match(file):
        raise HTTPException(404, "File non trovato")
    return FileResponse(_inside(ProjectPaths(name).root / "preview", file))


# ------------------------------------------------------------------ cronologia e mappa
@router.get("/api/projects/{name}/history")
async def project_history(name: str):
    _get(name)
    return history.read(name)


@router.get("/api/projects/{name}/map")
async def camera_map(name: str):
    p = _get(name)
    try:
        path = await asyncio.to_thread(mapdata.cached, p)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return FileResponse(path, media_type="application/json", headers={"Cache-Control": "no-cache"})


@router.get("/api/projects/{name}/frame/{vid}/{file}")
async def frame_thumbnail(name: str, vid: str, file: str):
    """Miniatura di un frame (per la mappa), creata al primo uso."""
    _get(name)
    if not re.match(r"^v\d+$", vid) or not _IMAGE.match(file):
        raise HTTPException(404, "File non trovato")
    paths = ProjectPaths(name)
    source = _inside(paths.frames, vid, file)
    cache = paths.colmap / "framethumbs" / f"{vid}_{file}"
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source), "-vf", "scale=360:-2", "-qscale:v", "5",
                str(cache))
            await asyncio.wait_for(proc.wait(), timeout=30)
        except (OSError, asyncio.TimeoutError):
            pass
    if not cache.exists():
        raise HTTPException(404, "Miniatura non disponibile")
    return FileResponse(cache)


# ------------------------------------------------------------------ spazio su disco
@router.get("/api/projects/{name}/disk")
async def disk_usage(name: str):
    return await asyncio.to_thread(disk.usage, _get(name))


class Clean(BaseModel):
    ids: list[str]


@router.post("/api/projects/{name}/disk/clean")
async def disk_clean(name: str, body: Clean):
    p = _get(name)
    if p.status in ("queued", "running") or jobs.active(name):
        raise HTTPException(409, "Ferma il progetto prima di cancellare file.")
    if p.external:
        raise HTTPException(400, "Progetto monitorato in sola lettura.")
    try:
        result = await asyncio.to_thread(disk.clean, p, body.ids)
    except ValueError as e:
        raise HTTPException(400, str(e))
    store.disk_mb(name, ttl=0)
    history.add(name, "disk_clean", items=body.ids, freed_mb=result["freed_mb"])
    return result


# ------------------------------------------------------------------ viewer: impostazioni, ritaglio, scala
@router.get("/api/projects/{name}/view")
async def get_view(name: str):
    _get(name)
    view = viewcfg.load(name)
    export = ProjectPaths(name).export
    models = sorted(f.relative_to(export).as_posix() for f in export.rglob("*.ply") if f.name != "mesh.ply") \
        if export.exists() else []
    aligned = {}
    for model in models:
        if model.startswith("versions/"):
            found = await asyncio.to_thread(viewcfg.alignment, name, model)
            if found:
                aligned[model] = found
    return {"view": view, "models": models, "alignment": aligned}


@router.put("/api/projects/{name}/view")
async def put_view(name: str, data: dict):
    _get(name)
    try:
        return {"view": viewcfg.save(name, data)}
    except (ValueError, TypeError, AttributeError) as e:
        raise HTTPException(422, str(e))


class BoxCrop(BaseModel):
    source: str = "splat_clean.ply"
    min: list[float]
    max: list[float]
    up: Optional[list[float]] = None


@router.post("/api/projects/{name}/crop")
async def crop_box(name: str, body: BoxCrop):
    """Scrive export/splat_ritaglio.ply. L'originale non si tocca."""
    _get(name)
    if not _PLY.match(body.source) or body.source == "splat_ritaglio.ply":
        raise HTTPException(422, "File di partenza non valido.")
    export = ProjectPaths(name).export
    source = _inside(export, *body.source.split("/"))
    if len(body.min) != 3 or len(body.max) != 3 or any(h <= l for l, h in zip(body.min, body.max)):
        raise HTTPException(422, "Scatola non valida.")
    from .tools.plyops import box_crop
    try:
        result = await asyncio.to_thread(box_crop, source, export / "splat_ritaglio.ply", body.min, body.max, body.up)
    except (ValueError, KeyError) as e:
        raise HTTPException(422, str(e))
    history.add(name, "crop", kept=result["kept"], total=result["total"])
    return {**result, "file": "splat_ritaglio.ply"}


@router.post("/api/projects/{name}/scale-export")
async def scale_export(name: str):
    """Scrive export/splat_in_metri.ply usando la scala misurata nel viewer."""
    _get(name)
    view = viewcfg.load(name)
    if not view.get("scale") or view.get("stale"):
        raise HTTPException(400, "Prima imposta la scala nel viewer (due punti e la distanza vera) sul modello attuale.")
    export = ProjectPaths(name).export
    source = _inside(export, "splat_clean.ply")
    from .tools.plyops import scale_ply
    await asyncio.to_thread(scale_ply, source, export / "splat_in_metri.ply", view["scale"])
    history.add(name, "scale_export", meters_per_unit=round(view["scale"], 5))
    return {"file": "splat_in_metri.ply", "meters_per_unit": view["scale"]}


# ------------------------------------------------------------------ lavori a richiesta (mesh)
@router.get("/api/projects/{name}/jobs")
async def project_jobs(name: str):
    _get(name)
    return jobs.all_for(name)


class MeshRequest(BaseModel):
    depth: int = 10
    metric: bool = True
    level: bool = True


@router.post("/api/projects/{name}/mesh")
async def request_mesh(name: str, body: MeshRequest):
    _get(name)
    if not (ProjectPaths(name).export / "splat_clean.ply").is_file():
        raise HTTPException(400, "La mesh si può creare quando il modello è esportato.")
    if not 7 <= body.depth <= 11:
        raise HTTPException(422, "Il dettaglio va da 7 a 11.")
    try:
        return jobs.request(name, "mesh", body.model_dump())
    except ValueError as e:
        raise HTTPException(409, str(e))


# ------------------------------------------------------------------ notifiche
@router.get("/api/notify")
async def get_notify():
    return notify.public()


@router.put("/api/notify")
async def put_notify(data: dict):
    try:
        return notify.save(data)
    except (ValueError, TypeError) as e:
        raise HTTPException(422, str(e))


@router.post("/api/notify/test")
async def test_notify():
    c = notify.load()
    if c["kind"] == "off":
        raise HTTPException(400, "Le notifiche sono spente: scegli una destinazione e salva.")
    try:
        await asyncio.to_thread(notify.send, "splat-pipeline: prova", "Se leggi questo, le notifiche funzionano.",
                                c["panel_url"], c)
    except (RuntimeError, ValueError) as e:
        raise HTTPException(502, str(e))
    return {"ok": True}
