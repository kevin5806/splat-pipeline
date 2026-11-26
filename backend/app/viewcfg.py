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


# ---------------------------------------------------------------- allineamento tra modelli diversi
def _model_frame(export, ply: str):
    """(centro del ritaglio, matrice COLMAP->Nerfstudio 3x4 o None, scala) di un modello esportato; None se mancano i dati."""
    import numpy as np
    stem = ply[: -len(".ply")]
    try:
        centre = np.array(json.loads((export / f"{stem}.json").read_text())["center"], dtype=float)
    except (OSError, ValueError, KeyError):
        return None
    matrix, scale = None, None
    try:
        d = json.loads((export / f"{stem}.dataparser.json").read_text())
        matrix, scale = np.array(d["transform"], dtype=float)[:3, :4], float(d["scale"])
    except (OSError, ValueError, KeyError):
        pass
    return centre, matrix, scale


def alignment(name: str, other: str, current: str = "splat_clean.ply") -> Optional[dict]:
    """Come disporre il modello `other` nelle coordinate di `current`: {position, quaternion (x,y,z,w), scale, exact}.

    Con le trasformazioni di Nerfstudio di entrambi l'allineamento è esatto (le camere di COLMAP sono le stesse);
    altrimenti si compensa solo la ricentratura ("exact": False).
    """
    import numpy as np
    export = ProjectPaths(name).export
    a, b = _model_frame(export, current), _model_frame(export, other)
    if a is None or b is None:
        return None
    (ca, ma, sa), (cb, mb, sb) = a, b
    if ma is None or mb is None:
        return {"position": (cb - ca).tolist(), "quaternion": [0.0, 0.0, 0.0, 1.0], "scale": 1.0, "exact": False}
    # per entrambi: x_file = s * (R x_colmap + t) - c. Qui si passa dalle coordinate di "other" a quelle di "current".
    ra, ta, rb, tb = ma[:, :3], ma[:, 3], mb[:, :3], mb[:, 3]
    rotation = ra @ rb.T
    scale = sa / sb
    position = sa * (ta - rotation @ tb) + scale * (rotation @ cb) - ca
    return {"position": position.tolist(), "quaternion": _quaternion(rotation), "scale": float(scale), "exact": True}


def _quaternion(m) -> list[float]:
    """Matrice di rotazione -> quaternione [x, y, z, w] (l'ordine di three.js)."""
    t = m[0][0] + m[1][1] + m[2][2]
    if t > 0:
        k = math.sqrt(t + 1.0) * 2
        q = [(m[2][1] - m[1][2]) / k, (m[0][2] - m[2][0]) / k, (m[1][0] - m[0][1]) / k, 0.25 * k]
    elif m[0][0] > m[1][1] and m[0][0] > m[2][2]:
        k = math.sqrt(1.0 + m[0][0] - m[1][1] - m[2][2]) * 2
        q = [0.25 * k, (m[0][1] + m[1][0]) / k, (m[0][2] + m[2][0]) / k, (m[2][1] - m[1][2]) / k]
    elif m[1][1] > m[2][2]:
        k = math.sqrt(1.0 + m[1][1] - m[0][0] - m[2][2]) * 2
        q = [(m[0][1] + m[1][0]) / k, 0.25 * k, (m[1][2] + m[2][1]) / k, (m[0][2] - m[2][0]) / k]
    else:
        k = math.sqrt(1.0 + m[2][2] - m[0][0] - m[1][1]) * 2
        q = [(m[0][2] + m[2][0]) / k, (m[1][2] + m[2][1]) / k, 0.25 * k, (m[1][0] - m[0][1]) / k]
    return [float(v) for v in q]
