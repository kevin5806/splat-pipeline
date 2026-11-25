"""Monitor dei progetti 'esterni': legge il log di ns-train e aggiorna la fase di training."""
from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import Optional

from . import store
from .models import Project
from .stages import _rx_progress

_SPLIT = re.compile(r"[\r\n]+")
STALE_AFTER = 180  # secondi senza scritture nel log => il processo non sembra più attivo


def _tail(path: Path, n: int = 16384) -> list[str]:
    with path.open("rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - n))
        data = f.read().decode("utf-8", errors="replace")
    return [l.strip() for l in _SPLIT.split(data) if l.strip()]


def update_from_log(p: Project, now: Optional[float] = None) -> bool:
    """Aggiorna lo stato di `p` dal log. Ritorna True se qualcosa è cambiato."""
    now = now or time.time()
    log = Path((p.external or {}).get("train_log", ""))
    if not log.is_file():
        return False
    before = p.model_dump_json()
    st = p.stage("train")
    lines = _tail(log)
    exit_line = next((l for l in reversed(lines) if l.startswith("EXIT=")), None)
    prog = next((r for r in (_rx_progress(l) for l in reversed(lines)) if r), None)
    mtime = log.stat().st_mtime
    age = now - mtime
    if st.started is None:
        st.started = log.stat().st_ctime
    if exit_line == "EXIT=0":
        st.status, st.progress, st.detail, st.ended = "done", 100.0, "training finito", mtime
        p.status, p.current_stage, p.error = "done", None, None
    elif exit_line:
        st.status, st.detail, st.ended = "failed", f"il training è uscito con errore ({exit_line})", mtime
        p.status, p.error = "failed", st.detail
    elif age > STALE_AFTER:
        st.status, st.detail = "interrupted", f"nessun aggiornamento del log da {int(age // 60)} min"
        p.status, p.current_stage = "interrupted", "train"
    else:
        st.status, st.ended = "running", None
        if prog:
            st.progress = prog[0]
            if prog[1]:
                st.detail = f"ETA {prog[1]}"
        p.status, p.current_stage = "running", "train"
    return p.model_dump_json() != before


def sync_all() -> int:
    n = 0
    for p in store.list_projects():
        if p.external and update_from_log(p):
            store.save(p)
            n += 1
    return n


async def monitor_loop(interval: float = 3.0) -> None:
    while True:
        try:
            await asyncio.to_thread(sync_all)
        except Exception:  # noqa: BLE001 - il monitor non deve mai fermare il pannello
            pass
        await asyncio.sleep(interval)
