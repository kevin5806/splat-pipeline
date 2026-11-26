"""Impostazioni del viewer salvate per progetto (view.json): verticale, scala reale, punto di partenza, ritaglio.

Stanno in un file a parte e non in state.json: si possono salvare anche mentre il progetto è in esecuzione.
Valgono per UN modello: coordinate e scala di uno splat riallenato possono cambiare, quindi si ricorda a quale
file si riferiscono e il viewer le ignora (avvisando) se il file è cambiato.
"""
from __future__ import annotations

import json
import math
import os
from typing import Optional

from .store import ProjectPaths


def _file(name: str):
    return ProjectPaths(name).root / "view.json"


def fingerprint(name: str, rel: str = "splat_clean.ply") -> Optional[str]:
    try:
        st = (ProjectPaths(name).export / rel).stat()
    except OSError:
        return None
    return f"{st.st_size}-{int(st.st_mtime)}"


def load(name: str) -> dict:
    try:
        data = json.loads(_file(name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    data["stale"] = bool(data.get("model") and data["model"] != fingerprint(name))
    return data


def _vec(v, what: str) -> list[float]:
    if not (isinstance(v, list) and len(v) == 3 and all(isinstance(x, (int, float)) and math.isfinite(x) for x in v)):
        raise ValueError(f"{what}: servono tre numeri.")
    return [float(x) for x in v]


def validate(data: dict) -> dict:
    out: dict = {}
    if data.get("up") is not None:
        up = _vec(data["up"], "verticale")
        n = math.sqrt(sum(x * x for x in up))
        if n < 1e-6:
            raise ValueError("verticale: vettore nullo.")
        out["up"] = [x / n for x in up]
    if data.get("reference") is not None:
        r = data["reference"]
        a, b = _vec(r.get("a"), "primo punto"), _vec(r.get("b"), "secondo punto")
        meters = r.get("meters")
        if not isinstance(meters, (int, float)) or not math.isfinite(meters) or not 0.001 <= meters <= 10000:
            raise ValueError("La distanza reale deve essere tra 0,001 e 10000 metri.")
        units = math.dist(a, b)
        if units < 1e-6:
            raise ValueError("I due punti coincidono.")
        out["reference"] = {"a": a, "b": b, "meters": float(meters)}
        out["scale"] = float(meters) / units                 # metri per unità del modello
    if data.get("start") is not None:
        s = data["start"]
        out["start"] = {"pos": _vec(s.get("pos"), "partenza"), "yaw": float(s.get("yaw", 0)), "pitch": float(s.get("pitch", 0))}
    if data.get("box") is not None:
        b = data["box"]
        lo, hi = _vec(b.get("min"), "ritaglio (minimo)"), _vec(b.get("max"), "ritaglio (massimo)")
        if any(h <= l for l, h in zip(lo, hi)):
            raise ValueError("Ritaglio: il massimo deve superare il minimo su ogni asse.")
        out["box"] = {"min": lo, "max": hi}
    return out


def save(name: str, data: dict) -> dict:
    out = validate(data)
    out["model"] = fingerprint(name)
    f = _file(name)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(out), encoding="utf-8")
    os.replace(tmp, f)
    return load(name)
