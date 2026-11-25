"""Informazioni sul sistema: GPU, disco, carico."""
from __future__ import annotations

import os
import shutil
import subprocess

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


def info() -> dict:
    base = config.DATA_DIR if config.DATA_DIR.exists() else config.DATA_DIR.parent
    du = shutil.disk_usage(base)
    load = os.getloadavg()[0] if hasattr(os, "getloadavg") else None
    return {
        "gpus": gpu_info(),
        "cpu_count": os.cpu_count(),
        "load1": round(load, 2) if load is not None else None,
        "disk_free_gb": round(du.free / 1e9, 1),
        "disk_total_gb": round(du.total / 1e9, 1),
        "fake": config.FAKE,
    }
