"""Lavoratore indipendente dal sito: esegue i progetti in coda.

    python -m app.worker

Si ferma con SIGTERM/SIGINT chiudendo in modo ordinato i processi figli (COLMAP, ffmpeg, training): lo stato resta
"interrotto" e, con AUTO_RESUME=1, alla ripartenza il progetto torna in coda e riprende dall'ultimo punto salvato.
"""
from __future__ import annotations

import asyncio
import signal

from . import config
from .pipeline import Manager


async def main() -> None:
    config.PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    manager = Manager()
    task = asyncio.create_task(manager.worker_loop(embedded=False))
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, task.cancel)
        except NotImplementedError:   # Windows
            pass
    print("[lavoratore] avviato", flush=True)
    try:
        await task
    except asyncio.CancelledError:
        pass
    print("[lavoratore] fermato", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
