"""Ritaglia e ricentra uno splat .ply di Nerfstudio.

Gli splat da COLMAP contengono spesso gaussiane isolate lontanissime (anche 20-25 volte il
raggio della stanza): i viewer inquadrano tutto il contenuto e la scena appare come un puntino.
Qui si tiene tutto ciò che sta entro `fattore` x (90° percentile della distanza dal centro) e si
sposta il centro nell'origine. Nessuna gaussiana viene modificata: solo scartata o traslata,
quindi la qualità del colore (armoniche sferiche) resta intatta.

Uso:  python -m app.tools.crop <in.ply> <out.ply> [fattore=3.5]
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

_TYPES = {"float": "<f4", "float32": "<f4", "double": "<f8", "uchar": "u1", "uint8": "u1",
          "int": "<i4", "int32": "<i4", "uint": "<u4", "uint32": "<u4", "short": "<i2", "ushort": "<u2"}


def read_ply(path: Path):
    with path.open("rb") as f:
        header: list[str] = []
        while True:
            line = f.readline().decode("ascii", errors="replace").rstrip("\r\n")
            header.append(line)
            if line == "end_header":
                break
            if len(header) > 500:
                raise ValueError("Intestazione PLY non valida")
        offset = f.tell()
    if "format binary_little_endian 1.0" not in header:
        raise ValueError("Solo PLY binary_little_endian supportato")
    n = int(next(h for h in header if h.startswith("element vertex")).split()[-1])
    fields = [(h.split()[2], _TYPES[h.split()[1]]) for h in header if h.startswith("property")]
    data = np.memmap(path, dtype=np.dtype(fields), mode="r", offset=offset, shape=(n,))
    return header, data


def crop(src: Path, dst: Path, factor: float = 3.5) -> dict:
    header, data = read_ply(src)
    xyz = np.stack([data["x"], data["y"], data["z"]], axis=1).astype(np.float64)
    ok = np.isfinite(xyz).all(axis=1)
    center = np.median(xyz[ok], axis=0)
    dist = np.linalg.norm(xyz - center, axis=1)
    p90 = float(np.percentile(dist[ok], 90))
    keep = ok & (dist <= factor * p90)
    out = np.array(data[keep])  # copia in memoria dei soli elementi tenuti
    for i, axis in enumerate("xyz"):
        out[axis] = (out[axis] - center[i]).astype(out[axis].dtype)
    new_header = [f"element vertex {len(out)}" if h.startswith("element vertex") else h for h in header]
    with dst.open("wb") as f:
        f.write(("\n".join(new_header) + "\n").encode("ascii"))
        f.write(out.tobytes())
    info = {"total": int(len(data)), "kept": int(len(out)), "removed": int(len(data) - len(out)),
            "center": [round(float(c), 4) for c in center], "p90": round(p90, 4), "radius": round(factor * p90, 4)}
    print(info)
    return info


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    crop(Path(sys.argv[1]), Path(sys.argv[2]), float(sys.argv[3]) if len(sys.argv) > 3 else 3.5)
