"""Orchestrazione: coda GPU (un progetto alla volta), stop, ripresa, blocco sul controllo di qualità."""
from __future__ import annotations

import asyncio
import os
import signal
import time
from pathlib import Path
from typing import Optional

from . import config, fake, stages, store
from .models import STAGES, Project
from .stages import Blocked, Ctx
from .store import ProjectPaths


class Busy(Exception):
    pass


class Manager:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()               # la GPU è una sola: un progetto alla volta
        self.tasks: dict[str, asyncio.Task] = {}
        self.viewer: Optional[asyncio.subprocess.Process] = None
        self.viewer_project: Optional[str] = None

    # ------------------------------------------------------------ stato
    def active(self, name: str) -> bool:
        t = self.tasks.get(name)
        return t is not None and not t.done()

    def any_active(self) -> Optional[str]:
        return next((n for n, t in self.tasks.items() if not t.done()), None)

    # ------------------------------------------------------------ comandi
    async def start(self, name: str) -> Project:
        if self.active(name):
            raise Busy(f"'{name}' è già in esecuzione o in coda.")
        p = store.load(name)
        if p is None:
            raise KeyError(name)
        if not p.video:
            raise ValueError("Carica prima un video.")
        p.status, p.error = "queued", None
        store.save(p)
        self.tasks[name] = asyncio.create_task(self._run(name))
        return p

    async def stop(self, name: str) -> None:
        t = self.tasks.get(name)
        if t and not t.done():
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass

    def reset_from(self, name: str, stage_id: str) -> Project:
        """Riporta a 'pending' la fase indicata e tutte le successive (per rifarle)."""
        if self.active(name):
            raise Busy("Ferma prima il progetto.")
        p = store.load(name)
        if p is None:
            raise KeyError(name)
        ids = [s for s, _ in STAGES]
        if stage_id not in ids:
            raise ValueError(f"Fase sconosciuta: {stage_id}")
        for st in p.stages:
            if ids.index(st.id) >= ids.index(stage_id):
                st.status, st.progress, st.detail, st.started, st.ended, st.metrics = "pending", 0.0, "", None, None, {}
        p.status, p.error, p.current_stage = "idle", None, None
        store.save(p)
        return p

    def force_continue(self, name: str) -> Project:
        """Accetta un blocco (controllo di qualità non superato) e segna la fase come superata."""
        p = store.load(name)
        if p is None:
            raise KeyError(name)
        blocked = next((s for s in p.stages if s.status == "blocked"), None)
        if blocked is None:
            raise ValueError("Nessuna fase bloccata.")
        blocked.status, blocked.progress = "done", 100.0
        blocked.metrics = {**blocked.metrics, "forced": True}
        blocked.detail = "forzato dall'utente"
        p.status, p.error = "idle", None
        store.save(p)
        return p

    # ------------------------------------------------------------ esecuzione
    async def _run(self, name: str) -> None:
        try:
            async with self.lock:
                await self.stop_viewer()
                p = store.load(name)
                assert p is not None
                p.status = "running"
                store.save(p)
                table = fake.FAKE if config.FAKE else stages.REAL
                for st in p.stages:
                    if st.status == "done":
                        continue
                    p.current_stage = st.id
                    st.status, st.progress, st.detail, st.started, st.ended = "running", 0.0, "", time.time(), None
                    store.save(p)
                    ctx = _Ctx(p, ProjectPaths(name), st, lambda: store.save(p))
                    try:
                        metrics = await table[st.id](ctx)
                        st.metrics = {**st.metrics, **(metrics or {})}
                        st.status, st.progress, st.ended = "done", 100.0, time.time()
                        store.save(p)
                    except Blocked as e:
                        st.status, st.detail, st.ended = "blocked", str(e), time.time()
                        p.status = "blocked"
                        store.save(p)
                        return
                    except asyncio.CancelledError:
                        st.status, st.detail = "interrupted", "interrotto dall'utente"
                        p.status = "stopped"
                        store.save(p)
                        raise
                    except Exception as e:  # noqa: BLE001 - qualsiasi errore di fase va mostrato all'utente
                        st.status, st.detail, st.ended = "failed", _short(e), time.time()
                        p.status, p.error = "failed", str(e)[-2000:]
                        store.save(p)
                        return
                p.status, p.current_stage = "done", None
                store.save(p)
        finally:
            self.tasks.pop(name, None)

    # ------------------------------------------------------------ viewer
    async def start_viewer(self, name: str) -> dict:
        if self.lock.locked():
            raise Busy("La GPU è occupata da una pipeline in esecuzione: il viewer live è già disponibile durante il training.")
        await self.stop_viewer()
        cfg = ProjectPaths(name).outputs / "main" / "splatfacto" / "run1" / "config.yml"
        if not config.FAKE and not cfg.exists():
            raise ValueError("Il progetto non ha ancora un modello allenato.")
        if config.FAKE:
            self.viewer_project = name
            return {"port": config.VIEWER_PORT, "project": name}
        log = open(ProjectPaths(name).logs / "viewer.log", "ab")
        self.viewer = await asyncio.create_subprocess_exec(
            "ns-viewer", "--load-config", str(cfg), "--viewer.websocket-port", str(config.VIEWER_PORT),
            stdout=log, stderr=asyncio.subprocess.STDOUT, start_new_session=hasattr(os, "setsid"))
        self.viewer_project = name
        return {"port": config.VIEWER_PORT, "project": name}

    async def stop_viewer(self) -> None:
        if self.viewer and self.viewer.returncode is None:
            try:
                os.killpg(os.getpgid(self.viewer.pid), signal.SIGTERM) if hasattr(os, "killpg") else self.viewer.terminate()
                await asyncio.wait_for(self.viewer.wait(), timeout=10)
            except (ProcessLookupError, asyncio.TimeoutError):
                pass
        self.viewer, self.viewer_project = None, None

    # ------------------------------------------------------------ avvio
    async def recover(self) -> list[str]:
        """Dopo un riavvio: segna come interrotto ciò che girava e, se richiesto, lo riprende."""
        resumed: list[str] = []
        for p in store.list_projects():
            if p.status in ("running", "queued"):
                for st in p.stages:
                    if st.status == "running":
                        st.status, st.detail = "interrupted", "interrotto da un riavvio"
                p.status = "interrupted"
                store.save(p)
                if config.AUTO_RESUME and p.video:
                    await self.start(p.name)
                    resumed.append(p.name)
        return resumed


def _short(e: Exception) -> str:
    return str(e).strip().splitlines()[0][:300] if str(e).strip() else e.__class__.__name__


class _Ctx(Ctx):
    """Ctx con salvataggio limitato a una volta al secondo (le righe di avanzamento sono molte)."""

    def __init__(self, project: Project, paths: ProjectPaths, stage, flush) -> None:
        super().__init__(project, paths, stage, flush)
        self._last = 0.0
        self._detail = ""

    def progress(self, pct: Optional[float] = None, detail: Optional[str] = None) -> None:
        if pct is not None:
            self.stage.progress = max(0.0, min(100.0, pct))
        if detail is not None:
            self.stage.detail = detail
        now = time.monotonic()
        if now - self._last >= 1.0 or (detail is not None and detail != self._detail):
            self._last, self._detail = now, detail or self._detail
            self.flush()
