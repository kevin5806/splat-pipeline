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


def parse_cgroup_memory(current: str, limit: str, stat: str) -> tuple[float, Optional[float]]:
    """File del cgroup del container -> (GB usati senza la cache dei file, limite in GB o None se illimitato)."""
    cur = int(current.strip())
    inactive = 0
    for line in stat.splitlines():
        k, _, v = line.partition(" ")
        if k in ("inactive_file", "total_inactive_file") and v.strip().isdigit():
            inactive = int(v)
    raw = limit.strip()
    lim = int(raw) if raw.isdigit() and int(raw) < 2 ** 60 else None       # "max" (v2) o numero enorme (v1) = nessun limite
    return round(max(0, cur - inactive) / 1e9, 1), (round(lim / 1e9, 1) if lim else None)


def _read(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None


def memory() -> Optional[tuple[float, float, str]]:
    """(GB usati, GB totali, ambito).

    Dentro Docker /proc/meminfo mostra la RAM dell'intera macchina fisica (qui 264 GB invece dei 48 del CT): fuorviante e
    sbagliato per capire se il training ci sta. Si usa quindi il cgroup del container; come totale, il limite del cgroup
    oppure MEM_LIMIT_GB. Senza cgroup si ripiega su /proc/meminfo.
    """
    env = os.environ.get("MEM_LIMIT_GB")
    env_limit = float(env) if env and env.replace(".", "", 1).isdigit() else None
    for cur, lim, stat in (("/sys/fs/cgroup/memory.current", "/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory.stat"),
                           ("/sys/fs/cgroup/memory/memory.usage_in_bytes", "/sys/fs/cgroup/memory/memory.limit_in_bytes",
                            "/sys/fs/cgroup/memory/memory.stat")):
        c = _read(cur)
        if c is None:
            continue
        try:
            used, cg_limit = parse_cgroup_memory(c, _read(lim) or "max", _read(stat) or "")
        except ValueError:
            continue
        total = env_limit or cg_limit
        if total is None:
            mi = _read("/proc/meminfo")
            total = parse_meminfo(mi)[1] if mi else None
        if total:
            return used, total, "container"
    mi = _read("/proc/meminfo")
    if mi:
        try:
            used, total = parse_meminfo(mi)
            return used, env_limit or total, "sistema"
        except (KeyError, ValueError):
            return None
    return None


def memory_limit_gb() -> Optional[float]:
    m = memory()
    return m[1] if m else None


def _mem_fields() -> dict:
    m = memory()
    return {"mem_used_gb": m[0], "mem_total_gb": m[1], "mem_scope": m[2]} if m else {"mem_used_gb": None, "mem_total_gb": None, "mem_scope": None}


def info() -> dict:
    base = config.DATA_DIR if config.DATA_DIR.exists() else config.DATA_DIR.parent
    du = shutil.disk_usage(base)
    load = os.getloadavg()[0] if hasattr(os, "getloadavg") else None
    return {
        "gpus": gpu_info(),
        "cpu_count": os.cpu_count(),
        "load1": round(load, 2) if load is not None else None,
        "cpu_percent": cpu_percent(),
        **_mem_fields(),
        "disk_free_gb": round(du.free / 1e9, 1),
        "disk_total_gb": round(du.total / 1e9, 1),
        "fake": config.FAKE,
    }
