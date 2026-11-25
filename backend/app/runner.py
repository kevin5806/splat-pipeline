"""Esecuzione di comandi esterni con log su file, callback per riga e arresto pulito."""
from __future__ import annotations

import asyncio
import os
import re
import signal
import time
from pathlib import Path
from typing import Callable, Optional, Sequence

_SPLIT = re.compile(r"[\r\n]+")


class CmdError(RuntimeError):
    def __init__(self, args: Sequence[str], code: int, tail: str):
        self.code = code
        self.tail = tail
        super().__init__(f"Comando fallito (codice {code}): {' '.join(map(str, args))[:200]}\n{tail}")


def _kill_tree(proc: asyncio.subprocess.Process, sig: int) -> None:
    try:
        if hasattr(os, "killpg"):
            os.killpg(os.getpgid(proc.pid), sig)
        else:  # sviluppo su Windows
            proc.terminate()
    except ProcessLookupError:
        pass


async def run_cmd(
    args: Sequence[str],
    log_path: Path,
    *,
    on_line: Optional[Callable[[str], None]] = None,
    cwd: Optional[Path] = None,
    env: Optional[dict] = None,
    check: bool = True,
    progress_log_every: float = 5.0,
) -> int:
    """Esegue `args`, scrive l'output in `log_path` e chiama `on_line` per ogni riga.

    Le righe di avanzamento (terminate da \\r, come quelle di rich/tqdm) vengono
    scritte nel log al massimo ogni `progress_log_every` secondi, per non gonfiarlo.
    Se la task viene cancellata, il processo (e i suoi figli) vengono terminati.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    full_env = {**os.environ, **(env or {})}
    kwargs: dict = {}
    if hasattr(os, "setsid"):
        kwargs["start_new_session"] = True
    proc = await asyncio.create_subprocess_exec(
        *map(str, args),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(cwd) if cwd else None,
        env=full_env,
        **kwargs,
    )
    tail: list[str] = []
    last_write = 0.0
    buf = ""
    with log_path.open("a", encoding="utf-8", errors="replace") as log:
        log.write(f"\n$ {' '.join(map(str, args))}\n")
        try:
            assert proc.stdout is not None
            while True:
                chunk = await proc.stdout.read(4096)
                if not chunk:
                    break
                buf += chunk.decode("utf-8", errors="replace")
                parts = _SPLIT.split(buf)
                # l'ultimo pezzo può essere incompleto
                buf = parts.pop() if not buf.endswith(("\n", "\r")) else ""
                for line in parts:
                    line = line.rstrip()
                    if not line:
                        continue
                    if on_line:
                        on_line(line)
                    tail.append(line)
                    del tail[:-30]
                    now = time.monotonic()
                    if now - last_write >= progress_log_every or not _looks_like_progress(line):
                        log.write(line + "\n")
                        log.flush()
                        last_write = now
            code = await proc.wait()
        except asyncio.CancelledError:
            _kill_tree(proc, signal.SIGTERM)
            try:
                await asyncio.wait_for(proc.wait(), timeout=10)
            except asyncio.TimeoutError:
                _kill_tree(proc, signal.SIGKILL)
                await proc.wait()
            log.write("\n[interrotto]\n")
            raise
        log.write(f"[uscita {code}]\n")
    if check and code != 0:
        raise CmdError(args, code, "\n".join(tail[-12:]))
    return code


def _looks_like_progress(line: str) -> bool:
    return bool(re.search(r"\d+ \(\d+(\.\d+)?%\)|\bProcessed file\b|Registering image|frame=\d+", line))
