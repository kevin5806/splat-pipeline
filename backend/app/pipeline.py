"""Orchestrazione: coda (un progetto alla volta: la GPU è una), stop, ripresa, blocco sul controllo di qualità.

Chi esegue i progetti è il *lavoratore* (`Manager.worker_loop`). Può stare nello stesso processo del sito oppure in un
container a parte (`python -m app.worker`): il sito si limita a scrivere lo stato e a lasciare richieste in
`control/` (vedi control.py). Così aggiornare o riavviare il sito non interrompe un lavoro in corso.
"""
from __future__ import annotations

import asyncio
import os
import signal
import sys
import time
from typing import Optional

from . import config, control, fake, stages, store
from .models import STAGES, Project
from .stages import Blocked, Ctx
from .store import ProjectPaths

ACTIVE = ("queued", "running")


class Busy(Exception):
    pass


class Manager:
    def __init__(self) -> None:
        self.tasks: dict[str, asyncio.Task] = {}          # solo nel processo del lavoratore
        self._cancel_reason: dict[str, str] = {}          # "user" | "shutdown"
        self.viewer: Optional[asyncio.subprocess.Process] = None
        self.viewer_project: Optional[str] = None
        self._embedded = False

    # ------------------------------------------------------------ stato (leggibile da qualunque processo)
    def active(self, name: str) -> bool:
        p = store.load(name)
        return bool(p and p.status in ACTIVE)

    def any_active(self) -> Optional[str]:
        return next((p.name for p in store.list_projects() if p.status in ACTIVE), None)

    # ------------------------------------------------------------ comandi (li usa il sito)
    async def start(self, name: str) -> Project:
        p = store.load(name)
        if p is None:
            raise KeyError(name)
        if p.status in ACTIVE:
            raise Busy(f"'{name}' è già in esecuzione o in coda.")
        if p.external:
            raise ValueError("Progetto monitorato in sola lettura: il lavoro non è gestito da questo pannello.")
        if not p.videos:
            raise ValueError("Carica prima almeno un video.")
        p.status, p.error, p.queued_at = "queued", None, time.time()
        store.save(p)
        return p

    async def stop(self, name: str, timeout: float = 25.0) -> None:
        p = store.load(name)
        if p is None or p.status not in ACTIVE:
            return
        if not control.worker_status()["alive"]:
            self._mark_stopped(p)        # nessun lavoratore: lo stato lo aggiorniamo noi
            return
        control.request_stop(name)
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            await asyncio.sleep(0.1)
            q = store.load(name)
            if q is None or q.status not in ACTIVE:
                return

    @staticmethod
    def _mark_stopped(p: Project, status: str = "stopped", detail: str = "interrotto dall'utente") -> None:
        for st in p.stages:
            if st.status == "running":
                st.status, st.detail = "interrupted", detail
        p.status = status
        store.save(p)

    def reset_from(self, name: str, stage_id: str) -> Project:
        """Riporta a 'pending' la fase indicata e tutte le successive (per rifarle da capo)."""
        if self.active(name):
            raise Busy("Ferma prima il progetto.")
        p = store.load(name)
        if p is None:
            raise KeyError(name)
        if p.external:
            raise ValueError("Progetto monitorato in sola lettura.")
        ids = [s for s, _ in STAGES]
        if stage_id not in ids:
            raise ValueError(f"Fase sconosciuta: {stage_id}")
        for st in p.stages:
            if ids.index(st.id) >= ids.index(stage_id):
                st.status, st.progress, st.detail, st.started, st.ended, st.metrics = "pending", 0.0, "", None, None, {}
        stages.reset_markers(ProjectPaths(name), stage_id)     # rifare = da zero: niente ripresa di un lavoro vecchio
        if ids.index(stage_id) <= ids.index("recover"):
            p.recovery_approved = 0
        p.status, p.error, p.current_stage = "idle", None, None
        store.save(p)
        return p

    def approve_recovery(self, name: str) -> Project:
        """L'utente accetta il giro di infittimento proposto."""
        p = store.load(name)
        if p is None:
            raise KeyError(name)
        st = p.stage("recover")
        if st.status != "blocked" or st.metrics.get("decision") != "approve":
            raise ValueError("Non c'è nessun infittimento in attesa di conferma.")
        done = sum(1 for r in st.metrics.get("rounds", []) if r.get("kind") == "densify")
        p.recovery_approved = done + 1
        st.status, st.progress, st.detail, st.started, st.ended = "pending", 0.0, "", None, None
        p.status, p.error = "idle", None
        store.save(p)
        return p

    def reopen_recovery(self, name: str) -> Project:
        """Si torna al recupero (per un video di supporto) TENENDO il lavoro fatto: modello delle camere e giri precedenti.
        Si rifanno solo le fasi successive; il modello allenato di prima resta da parte."""
        if self.active(name):
            raise Busy("Ferma prima il progetto.")
        p = store.load(name)
        if p is None:
            raise KeyError(name)
        if p.stage("refine").status != "done":
            raise ValueError("Il video di supporto si può aggiungere quando le camere sono già ricostruite.")
        ids = [s for s, _ in STAGES]
        for st in p.stages:
            if ids.index(st.id) >= ids.index("recover"):
                st.status, st.progress, st.detail, st.started, st.ended, st.metrics = "pending", 0.0, "", None, None, {}
        stages.keep_previous_results(ProjectPaths(name), "convert")
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

    @staticmethod
    def queue_rank(name: str) -> Optional[int]:
        """Posizione in coda (1 = il prossimo)."""
        queued = sorted((p for p in store.list_projects() if p.status == "queued" and not p.external),
                        key=lambda p: (p.queued_at or 0.0, p.created))
        for i, p in enumerate(queued, start=1):
            if p.name == name:
                return i
        return None

    async def request_viewer(self, name: str) -> None:
        if self.any_active():
            raise Busy("La GPU è occupata da una pipeline in esecuzione: il viewer live è già disponibile durante il training.")
        if not control.worker_status()["alive"]:
            raise Busy("Il lavoratore non risponde: il viewer parte quando torna attivo.")
        if not config.FAKE and not (ProjectPaths(name).outputs / "main" / "splatfacto" / "run1" / "config.yml").exists():
            raise ValueError("Il progetto non ha ancora un modello allenato.")
        control.request_viewer("start", name)

    # ------------------------------------------------------------ lavoratore
    async def worker_loop(self, embedded: bool = False) -> None:
        self._embedded = embedded
        await self.recover()
        control.beat(None, embedded)
        try:
            while True:
                try:
                    await self._tick()
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001 - il lavoratore non deve morire per un errore in un giro
                    print(f"[lavoratore] errore: {e!r}", file=sys.stderr, flush=True)
                await asyncio.sleep(config.WORKER_POLL)
        finally:
            await self.shutdown()
            control.worker_gone()

    async def _tick(self) -> None:
        busy = next((n for n, t in self.tasks.items() if not t.done()), None)
        control.beat(busy, self._embedded)
        for name in control.pop_stops():
            t = self.tasks.get(name)
            if t and not t.done():
                self._cancel_reason[name] = "user"
                t.cancel()
            else:
                p = store.load(name)
                if p and p.status == "queued":
                    self._mark_stopped(p)
        for name in [n for n, t in self.tasks.items() if t.done()]:
            self.tasks.pop(name, None)
        if self.tasks:
            return                                   # un progetto alla volta
        await self._handle_viewer()
        nxt = self._next_queued()
        if nxt:
            self.tasks[nxt] = asyncio.create_task(self._run(nxt))

    @staticmethod
    def _next_queued() -> Optional[str]:
        queued = [p for p in store.list_projects() if p.status == "queued" and not p.external]
        queued.sort(key=lambda p: (p.queued_at or 0.0, p.created))
        return queued[0].name if queued else None

    async def recover(self) -> list[str]:
        """All'avvio del lavoratore: ciò che risultava in esecuzione è stato interrotto (riavvio, spegnimento).
        Con AUTO_RESUME torna in coda e riparte da solo, dall'ultimo punto salvato della sua fase."""
        resumed: list[str] = []
        for p in store.list_projects():
            # "running": il processo è morto di colpo (corrente, crash); "interrupted": chiusura ordinata. In entrambi i casi
            # l'interruzione l'ha causata il sistema, non l'utente (che invece produce "stopped").
            if p.status in ("running", "interrupted"):
                for st in p.stages:
                    if st.status == "running":
                        st.status, st.detail = "interrupted", "interrotto da un riavvio"
                p.status = "interrupted"
                if config.AUTO_RESUME and p.videos and not p.external:
                    p.status, p.queued_at = "queued", time.time()
                    resumed.append(p.name)
                store.save(p)
        return resumed

    async def shutdown(self) -> None:
        """Chiusura ordinata: ferma i processi figli (COLMAP, ffmpeg, training) e lascia lo stato pronto per ripartire."""
        running = [(n, t) for n, t in self.tasks.items() if not t.done()]
        for name, t in running:
            self._cancel_reason[name] = "shutdown"
            t.cancel()
        if running:
            await asyncio.wait([t for _, t in running], timeout=config.SHUTDOWN_GRACE)
        await self.stop_viewer()

    # ------------------------------------------------------------ esecuzione di un progetto
    async def _run(self, name: str) -> None:
        try:
            p = store.load(name)
            if p is None or p.status != "queued":
                return                                # fermato o cambiato mentre aspettava
            await self.stop_viewer()
            p.status = "running"
            store.save(p)
            table = fake.FAKE if config.FAKE else stages.REAL
            for st in p.stages:
                if st.status == "done":
                    continue
                p.current_stage = st.id
                st.status, st.progress, st.detail, st.started, st.ended = "running", 0.0, "", time.time(), None
                st.metrics = {}
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
                    if self._cancel_reason.pop(name, "user") == "shutdown":
                        st.status, st.detail = "interrupted", "interrotto da uno spegnimento: riparte da solo"
                        p.status = "interrupted"
                    else:
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
            self._cancel_reason.pop(name, None)

    # ------------------------------------------------------------ viewer (vive nel processo del lavoratore)
    async def _handle_viewer(self) -> None:
        req = control.pop_viewer_request()
        if req and req.get("action") == "stop":
            await self.stop_viewer()
        elif req and req.get("action") == "start" and req.get("project"):
            try:
                await self.start_viewer(req["project"])
            except Exception as e:  # noqa: BLE001
                print(f"[lavoratore] viewer non avviato: {e!r}", file=sys.stderr, flush=True)
        if self.viewer is not None and self.viewer.returncode is not None:      # il viewer è morto da solo
            self.viewer, self.viewer_project = None, None
        if self.viewer_project:
            control.set_viewer(self.viewer_project, config.VIEWER_PORT)         # battito del viewer
        else:
            control.set_viewer(None)

    async def start_viewer(self, name: str) -> dict:
        await self.stop_viewer()
        cfg = ProjectPaths(name).outputs / "main" / "splatfacto" / "run1" / "config.yml"
        if config.FAKE:
            self.viewer_project = name
        else:
            if not cfg.exists():
                raise ValueError("Il progetto non ha ancora un modello allenato.")
            log = open(ProjectPaths(name).logs / "viewer.log", "ab")
            self.viewer = await asyncio.create_subprocess_exec(
                "ns-viewer", "--load-config", str(cfg), "--viewer.websocket-port", str(config.VIEWER_PORT),
                stdout=log, stderr=asyncio.subprocess.STDOUT, start_new_session=hasattr(os, "setsid"))
            self.viewer_project = name
        control.set_viewer(name, config.VIEWER_PORT)
        return {"port": config.VIEWER_PORT, "project": name}

    async def stop_viewer(self) -> None:
        if self.viewer and self.viewer.returncode is None:
            try:
                os.killpg(os.getpgid(self.viewer.pid), signal.SIGTERM) if hasattr(os, "killpg") else self.viewer.terminate()
                await asyncio.wait_for(self.viewer.wait(), timeout=10)
            except (ProcessLookupError, asyncio.TimeoutError):
                pass
        self.viewer, self.viewer_project = None, None
        control.set_viewer(None)


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
