"""Mappa delle riprese: dove stava la camera, e dove i punti poco coperti, in 3D.

Legge il modello COLMAP rifinito, orienta la scena con il pavimento in basso (la media dell'"alto" delle camere) e
la restituisce compatta. Le camere non agganciate non hanno una posizione: si stima tra le vicine nel tempo.
"""
from __future__ import annotations

import json
import math
import os
import struct
from pathlib import Path

from . import weak
from .models import Project
from .store import ProjectPaths
from .tools.colmap_model import read_observations, read_poses

MAX_POINTS = 25000


def rotation_matrix(q: tuple[float, float, float, float]) -> list[list[float]]:
    """Quaternione (w, x, y, z) di COLMAP -> matrice mondo->camera."""
    n = math.sqrt(sum(v * v for v in q)) or 1.0
    w, x, y, z = (v / n for v in q)
    return [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]


def camera_center(q, t) -> list[float]:
    r = rotation_matrix(q)
    return [-(r[0][i] * t[0] + r[1][i] * t[1] + r[2][i] * t[2]) for i in range(3)]      # -R^T t


def camera_up(q) -> list[float]:
    r = rotation_matrix(q)
    return [-r[1][0], -r[1][1], -r[1][2]]       # in COLMAP l'asse y della camera punta in basso


def level_matrix(up: list[float]) -> list[list[float]]:
    """Rotazione che porta `up` sull'asse Z."""
    n = math.sqrt(sum(v * v for v in up))
    if n < 1e-9:
        return [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    z = [v / n for v in up]
    a = [1.0, 0.0, 0.0] if abs(z[0]) < 0.9 else [0.0, 1.0, 0.0]
    d = sum(i * j for i, j in zip(a, z))
    x = [a[i] - d * z[i] for i in range(3)]
    m = math.sqrt(sum(v * v for v in x))
    x = [v / m for v in x]
    y = [z[1] * x[2] - z[2] * x[1], z[2] * x[0] - z[0] * x[2], z[0] * x[1] - z[1] * x[0]]
    return [x, y, z]


def _apply(m, p) -> list[float]:
    return [m[i][0] * p[0] + m[i][1] * p[1] + m[i][2] * p[2] for i in range(3)]


def sample_points(points_bin: Path, limit: int = MAX_POINTS) -> list[tuple]:
    """[(x, y, z, r, g, b)] presi a intervalli regolari da points3D.bin."""
    out: list[tuple] = []
    try:
        f = Path(points_bin).open("rb")
    except OSError:
        return out
    with f:
        raw = f.read(8)
        if len(raw) < 8:
            return out
        n = struct.unpack("<Q", raw)[0]
        every = max(1, n // limit)
        head = struct.Struct("<Q3d3BdQ")                 # id, xyz, rgb, errore, lunghezza della traccia
        for i in range(n):
            chunk = f.read(head.size)
            if len(chunk) < head.size:
                break
            v = head.unpack(chunk)
            f.seek(v[-1] * 8, 1)
            if i % every == 0:
                out.append((v[1], v[2], v[3], v[4], v[5], v[6]))
    return out


def _percentile(values: list[float], q: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, max(0, int(q * (len(s) - 1))))]


def build(p: Project) -> dict:
    paths = ProjectPaths(p.name)
    images = paths.refined / "images.bin"
    if not images.exists():
        raise ValueError("La mappa è disponibile quando le camere sono ricostruite.")
    poses = read_poses(images)
    observations = read_observations(images)
    centers = {n: camera_center(q, t) for n, (q, t) in poses.items()}
    ups = [camera_up(q) for q, _t in poses.values()]
    up = [sum(u[i] for u in ups) / max(1, len(ups)) for i in range(3)]
    m = level_matrix(up)
    leveled = {n: _apply(m, c) for n, c in centers.items()}
    if not leveled:
        raise ValueError("Il modello non contiene camere.")
    mid = [_percentile([c[i] for c in leveled.values()], 0.5) for i in range(3)]

    def place(c):
        return [round(c[i] - mid[i], 3) for i in range(3)]

    probe = p.stage("probe").metrics.get("videos", {})
    cameras = []
    for v in p.videos:
        info = probe.get(v.id) or v.info or {}
        fps, step = float(info.get("fps") or 30.0), int(info.get("step") or p.settings.frame_step)
        seq = weak.sequence(weak.list_frames(paths.frames / v.id), v.id, step, observations)
        known = [k for k, s in enumerate(seq) if s[2]]
        pos = 0
        for k, (index, fname, registered, good) in enumerate(seq):
            if registered:
                c, state = place(leveled[f"{v.id}/{fname}"]), "good" if good else "weak"
            else:
                if fname.startswith("extra_"):
                    continue                       # un frame aggiunto e non agganciato non dice niente di nuovo
                while pos < len(known) and known[pos] < k:
                    pos += 1
                left = known[pos - 1] if pos > 0 else None
                right = known[pos] if pos < len(known) else None
                if left is None and right is None:
                    continue
                a = leveled[f"{v.id}/{seq[left if left is not None else right][1]}"]
                b = leveled[f"{v.id}/{seq[right if right is not None else left][1]}"]
                ia = seq[left if left is not None else right][0]
                ib = seq[right if right is not None else left][0]
                w = 0.0 if ib == ia else (index - ia) / (ib - ia)
                c, state = place([a[i] + w * (b[i] - a[i]) for i in range(3)]), "missing"
            cameras.append({"v": v.id, "t": round(index / fps, 2), "p": c, "q": state, "f": fname})
    points = sample_points(paths.refined / "points3D.bin")
    radius = _percentile([math.dist((0, 0, 0), c["p"]) for c in cameras] or [1.0], 0.95) or 1.0
    flat, colors = [], []
    for x, y, z, r, g, b in points:
        q = place(_apply(m, (x, y, z)))
        if math.dist((0, 0, 0), q) <= radius * 3.5:      # via i punti lontanissimi, che schiaccerebbero la vista
            flat += q
            colors += [r, g, b]
    return {"videos": [{"id": v.id, "name": v.name or v.filename} for v in p.videos], "cameras": cameras,
            "points": flat, "colors": colors, "radius": round(radius, 3)}


def cached(p: Project) -> Path:
    """La mappa in JSON, ricalcolata solo quando cambia il modello."""
    paths = ProjectPaths(p.name)
    images = paths.refined / "images.bin"
    out = paths.colmap / "map.json"
    if out.exists() and images.exists() and out.stat().st_mtime >= images.stat().st_mtime:
        return out
    data = build(p)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, out)
    return out
