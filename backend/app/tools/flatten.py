"""Rende "piatto" il dataset di Nerfstudio quando i frame stanno in sottocartelle (un video per cartella).

Nerfstudio, con le immagini ridotte (downscale > 1), cerca ``images_<n>/<nome del file>`` ignorando le sottocartelle
(``NerfstudioDataParser._get_fname`` usa ``filepath.name``). Con più video i frame sono in ``v1/``, ``v2/`` e i nomi si
ripetono (``v1/frame_00001.jpg`` e ``v2/frame_00001.jpg``): servono quindi nomi UNICI in un'unica cartella
(``v1__frame_00001.jpg``) e ``transforms.json`` riscritto di conseguenza.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path


def link_file(src: Path, dst: Path) -> None:
    """Collegamento al file (simbolico; dove non si può, fisico o copia)."""
    try:
        os.symlink(src.resolve(), dst)
    except OSError:
        try:
            os.link(src, dst)
        except OSError:
            shutil.copy(src, dst)


def flat_name(relative: str) -> str:
    return relative.replace("\\", "/").replace("/", "__")


def flatten_dataset(dataset: Path, frames: Path) -> list[tuple[str, Path]]:
    """Riscrive transforms.json con nomi unici e crea ``dataset/images`` con un collegamento per ogni frame usato.

    Ritorna [(nome_unico, file_sorgente)]. Si può rilanciare: riparte dai percorsi originali o da quelli già piatti.
    """
    tf = dataset / "transforms.json"
    data = json.loads(tf.read_text(encoding="utf-8"))
    pairs: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for fr in data["frames"]:
        rel = Path(fr["file_path"]).as_posix()
        rel = rel[2:] if rel.startswith("./") else rel
        if not rel.startswith("images/"):
            raise ValueError(f"percorso inatteso in transforms.json: {fr['file_path']}")
        inner = rel[len("images/"):]
        if "/" not in inner and "__" in inner:                     # già piatto
            inner = inner.replace("__", "/", 1)
        source = frames / inner
        if not source.is_file():
            raise FileNotFoundError(f"frame mancante: {source}")
        name = flat_name(inner)
        if name in seen:
            raise ValueError(f"nome duplicato nel dataset: {name}")
        seen.add(name)
        fr["file_path"] = f"images/{name}"
        pairs.append((name, source))
    images = dataset / "images"
    if images.is_symlink():
        images.unlink()                     # toglie solo il collegamento: i frame originali non si toccano
    elif images.exists():
        shutil.rmtree(images)
    images.mkdir(parents=True)
    for name, source in pairs:
        link_file(source, images / name)
    tmp = tf.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, tf)
    return pairs


def missing_training_images(dataset: Path, downscale: int) -> list[str]:
    """I file che Nerfstudio andrà ad aprire e che non esistono (stessa regola del suo lettore del dataset)."""
    data = json.loads((dataset / "transforms.json").read_text(encoding="utf-8"))
    missing = []
    for fr in data["frames"]:
        p = Path(fr["file_path"])
        target = dataset / f"images_{downscale}" / p.name if downscale > 1 else dataset / p
        if not target.is_file():
            missing.append(str(target))
    return missing
