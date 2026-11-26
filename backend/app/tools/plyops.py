"""Operazioni sugli splat .ply che non toccano l'originale: ritaglio a scatola e scala in metri.

Uso:  python -m app.tools.plyops scale <in.ply> <out.ply> <metri_per_unità>
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

from .crop import read_ply


def write_ply(path: Path, header: list[str], data: np.ndarray) -> None:
    new_header = [f"element vertex {len(data)}" if h.startswith("element vertex") else h for h in header]
    tmp = path.with_suffix(".part")
    with tmp.open("wb") as f:
        f.write(("\n".join(new_header) + "\n").encode("ascii"))
        f.write(data.tobytes())
    tmp.replace(path)


def level_basis(up) -> np.ndarray:
    """Matrice (righe = nuovi assi) che porta `up` sull'asse Z: la stessa regola usata dal viewer."""
    from ..mapdata import level_matrix
    return np.array(level_matrix(list(up)), dtype=np.float64)


def box_crop(src: Path, dst: Path, lo, hi, up=None) -> dict:
    """Tiene solo le gaussiane il cui centro sta nella scatola, misurata nel sistema raddrizzato (Z = `up`)."""
    header, data = read_ply(src)
    xyz = np.stack([data["x"], data["y"], data["z"]], axis=1).astype(np.float64)
    if up is not None:
        xyz = xyz @ level_basis(up).T
    keep = np.all((xyz >= np.array(lo)) & (xyz <= np.array(hi)), axis=1)
    if not keep.any():
        raise ValueError("Con questa scatola non resterebbe niente.")
    write_ply(dst, header, np.array(data[keep]))
    return {"total": int(len(data)), "kept": int(keep.sum())}


def scale_ply(src: Path, dst: Path, factor: float) -> dict:
    """Moltiplica posizioni e dimensioni delle gaussiane: nello splat esportato 1 unità diventa 1 metro.

    Le dimensioni nel file sono logaritmi (scale_0..2): scalare vuol dire sommarci il logaritmo del fattore.
    """
    if not (factor > 0 and math.isfinite(factor)):
        raise ValueError("Fattore di scala non valido.")
    header, data = read_ply(src)
    out = np.array(data)
    for axis in "xyz":
        out[axis] = out[axis] * factor
    for name in ("scale_0", "scale_1", "scale_2"):
        if name in out.dtype.names:
            out[name] = out[name] + math.log(factor)
    write_ply(dst, header, out)
    return {"points": int(len(out)), "factor": factor}


if __name__ == "__main__":
    if len(sys.argv) != 5 or sys.argv[1] != "scale":
        print(__doc__)
        sys.exit(2)
    print(scale_ply(Path(sys.argv[2]), Path(sys.argv[3]), float(sys.argv[4])))
