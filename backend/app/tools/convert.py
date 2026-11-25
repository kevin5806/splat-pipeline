"""Converte un modello COLMAP (cartella con cameras/images/points3D .bin) nel formato di Nerfstudio.

Uso:  python -m app.tools.convert <modello_colmap> <cartella_dataset> <cartella_frame>

Nota: i moduli di questo pacchetto NON devono avere nomi di librerie terze (cv2, json, ...):
uno script chiamato cv2.py ha già rotto un import di Nerfstudio.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _relink(link: Path, target: Path) -> None:
    if link.is_symlink() or link.exists():
        link.unlink()
    link.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(target.resolve(), link)


def main(model: str, dataset: str, frames: str) -> int:
    from nerfstudio.process_data import colmap_utils  # import tardivo: lento e richiede l'immagine di Nerfstudio

    model_dir, ds, fr = Path(model), Path(dataset), Path(frames)
    sparse0 = ds / "colmap" / "sparse" / "0"
    _relink(sparse0, model_dir)
    _relink(ds / "images", fr)
    n = colmap_utils.colmap_to_json(recon_dir=sparse0, output_dir=ds)
    print(f"frames in transforms.json: {n}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(*sys.argv[1:]))
