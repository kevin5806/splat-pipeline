"""Lettura minima dei file binari di un modello COLMAP (senza dipendere da COLMAP o pycolmap).

Formato di images.bin (little-endian):
    uint64  numero di immagini
    per ogni immagine:
        uint32 image_id, 4 x double (quaternione), 3 x double (traslazione), uint32 camera_id,
        nome (stringa terminata da \\0), uint64 numero di punti 2D, poi N x (double x, double y, int64 point3D_id)

Si leggono solo i nomi e si salta il resto: su modelli da migliaia di camere il file pesa centinaia di MB.
"""
from __future__ import annotations

import struct
from pathlib import Path

_HEAD = struct.Struct("<I4d3dI")  # 4 + 32 + 24 + 4 = 64 byte
_POINT2D_BYTES = 24


def read_image_names(images_bin: Path) -> list[str]:
    names: list[str] = []
    with Path(images_bin).open("rb") as f:
        raw = f.read(8)
        if len(raw) < 8:
            return names
        n = struct.unpack("<Q", raw)[0]
        for _ in range(n):
            head = f.read(_HEAD.size)
            if len(head) < _HEAD.size:
                break
            chunk = f.read(512)
            end = chunk.find(b"\x00")
            if end < 0:
                raise ValueError("nome immagine senza terminatore: file danneggiato?")
            names.append(chunk[:end].decode("utf-8", errors="replace"))
            f.seek(-(len(chunk) - end - 1), 1)           # torna subito dopo il terminatore
            n2d = struct.unpack("<Q", f.read(8))[0]
            f.seek(n2d * _POINT2D_BYTES, 1)
    return names


def read_camera_count(cameras_bin: Path) -> int:
    with Path(cameras_bin).open("rb") as f:
        raw = f.read(8)
    return struct.unpack("<Q", raw)[0] if len(raw) == 8 else 0


def group_ranges(missing: list[int]) -> list[tuple[int, int]]:
    """[3,4,5,9,10] -> [(3,5),(9,10)]: intervalli di indici consecutivi."""
    out: list[tuple[int, int]] = []
    for i in sorted(missing):
        if out and i == out[-1][1] + 1:
            out[-1] = (out[-1][0], i)
        else:
            out.append((i, i))
    return out
