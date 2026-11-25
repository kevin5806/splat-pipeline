"""Informazioni sul sistema: GPU, disco, carico."""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from typing import Optional

from . import config


def _gpu_nvml() -> list[dict]:
    """NVML via nvidia-ml-py: funziona anche nei container dove manca nvidia-smi."""
    try:
        import pynvml  # type: ignore

        pynvml.nvmlInit()
    except Exception:  # noqa: BLE001 - libreria o driver assenti
        return []
    try:
        gpus = []
        for i in range(pynvml.nvmlDeviceGetCount()):
            h = pynvml.nvmlDeviceGetHandleByIndex(i)
            name = pynvml.nvmlDeviceGetName(h)
            mem = pynvml.nvmlDeviceGetMemoryInfo(h)
            gpus.append({
                "name": name.decode() if isinstance(name, bytes) else name,
                "util": int(pynvml.nvmlDeviceGetUtilizationRates(h).gpu),
                "mem_used_mb": int(mem.used // 2**20),
                "mem_total_mb": int(mem.total // 2**20),
                "temp_c": int(pynvml.nvmlDeviceGetTemperature(h, pynvml.NVML_TEMPERATURE_GPU)),
            })
        return gpus
    except Exception:  # noqa: BLE001
        return []
    finally:
        try:
            pynvml.nvmlShutdown()
        except Exception:  # noqa: BLE001
            pass


def gpu_info() -> list[dict]:
    gpus = _gpu_nvml()
    if gpus:
        return gpus
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if out.returncode != 0:
        return []
    gpus = []
    for line in out.stdout.strip().splitlines():
        name, util, used, total, temp = [x.strip() for x in line.split(",")]
        gpus.append({"name": name, "util": int(util), "mem_used_mb": int(used), "mem_total_mb": int(total), "temp_c": int(temp)})
    return gpus


def parse_proc_stat(text: str) -> tuple[int, int]:
    """Prima riga di /proc/stat -> (tempo occupato, tempo totale) in tick."""
    first = text.splitlines()[0].split()
    vals = [int(x) for x in first[1:]]
    idle = vals[3] + (vals[4] if len(vals) > 4 else 0)   # idle + iowait
    total = sum(vals[:8]) if len(vals) >= 8 else sum(vals)
    return total - idle, total


def cpu_percent_between(a: tuple[int, int], b: tuple[int, int]) -> float:
    busy, total = b[0] - a[0], b[1] - a[1]
    return round(100.0 * busy / total, 1) if total > 0 else 0.0


_cpu_prev: dict = {}


def cpu_percent() -> Optional[float]:
    """Uso medio di tutti i core dall'ultima chiamata (None dove manca /proc/stat, es. Windows)."""
    try:
        with open("/proc/stat") as f:
            cur = parse_proc_stat(f.read())
    except (OSError, ValueError, IndexError):
        return None
    prev = _cpu_prev.get("v")
    if prev is None or cur[1] - prev[1] < 50:   # prima lettura o intervallo troppo breve: campiona 0,25 s
        time.sleep(0.25)
        try:
            with open("/proc/stat") as f:
                nxt = parse_proc_stat(f.read())
        except (OSError, ValueError, IndexError):
            return None
        pct = cpu_percent_between(prev or cur, nxt) if prev else cpu_percent_between(cur, nxt)
        _cpu_prev["v"] = nxt
        return pct
    pct = cpu_percent_between(prev, cur)
    _cpu_prev["v"] = cur
    return pct


def parse_meminfo(text: str) -> tuple[float, float]:
    """/proc/meminfo -> (GB usati, GB totali). 'Usati' = totale - disponibile (la cache dei file non conta)."""
    kv = {}
    for line in text.splitlines():
        k, _, rest = line.partition(":")
        parts = rest.split()
        if parts:
            kv[k] = int(parts[0])
    total = kv["MemTotal"]
    avail = kv.get("MemAvailable", kv.get("MemFree", 0))
    return round((total - avail) / 1e6, 1), round(total / 1e6, 1)


def memory() -> Optional[tuple[float, float]]:
    try:
        with open("/proc/meminfo") as f:
            return parse_meminfo(f.read())
    except (OSError, KeyError, ValueError):
        return None


def info() -> dict:
    base = config.DATA_DIR if config.DATA_DIR.exists() else config.DATA_DIR.parent
    du = shutil.disk_usage(base)
    load = os.getloadavg()[0] if hasattr(os, "getloadavg") else None
    return {
        "gpus": gpu_info(),
        "cpu_count": os.cpu_count(),
        "load1": round(load, 2) if load is not None else None,
        "cpu_percent": cpu_percent(),
        "mem_used_gb": (memory() or (None, None))[0],
        "mem_total_gb": (memory() or (None, None))[1],
        "disk_free_gb": round(du.free / 1e9, 1),
        "disk_total_gb": round(du.total / 1e9, 1),
        "fake": config.FAKE,
    }
