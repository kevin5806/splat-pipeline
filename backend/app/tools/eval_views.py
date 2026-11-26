"""Trasforma le immagini di valutazione di Nerfstudio in un confronto consultabile.

Uso:  python -m app.tools.eval_views <cartella_render> <dataset> <parametri.json> <cartella_uscita>

ns-eval scrive eval_img_0000.png… con la foto vera a sinistra e il modello a destra. Qui si calcola il PSNR di ogni
vista, la si ricollega al video e al secondo, e si conservano come JPEG le peggiori, due tipiche e le migliori.

Le viste di valutazione sono quelle che Nerfstudio non usa per il training: con i nomi in ordine, la frazione
di training predefinita (0,9) distribuisce ceil(n*0,9) indici a distanza regolare; le altre sono di controllo.
"""
from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

import numpy as np

TRAIN_FRACTION = 0.9
KEEP_WORST, KEEP_MIDDLE, KEEP_BEST = 12, 3, 3
_NAME = re.compile(r"^(v\d+)__(.+)$")


def eval_indices(n: int, fraction: float = TRAIN_FRACTION) -> list[int]:
    """Stessa regola di nerfstudio.data.utils.dataparsers_utils.get_train_eval_split_fraction."""
    train = np.linspace(0, n - 1, math.ceil(n * fraction), dtype=int)
    return [int(i) for i in np.setdiff1d(np.arange(n), train)]


def frame_time(filename: str, params: dict) -> tuple[str, float]:
    """'v2__extra_000123.jpg' -> ('v2', secondi nel video)."""
    from ..weak import source_index
    m = _NAME.match(filename)
    if not m:
        return "", 0.0
    vid, inner = m.group(1), m.group(2)
    info = params.get(vid) or {}
    index = source_index(inner, int(info.get("step") or 1))
    return vid, round((index or 0) / float(info.get("fps") or 30.0), 2)


def psnr(a: np.ndarray, b: np.ndarray) -> float:
    mse = float(((a.astype(np.float32) / 255 - b.astype(np.float32) / 255) ** 2).mean())
    return -10 * math.log10(max(mse, 1e-10))


def process(renders: Path, dataset: Path, params: dict, out: Path, width: int = 1100) -> dict:
    from PIL import Image
    names = sorted(Path(f["file_path"]).name for f in json.loads((dataset / "transforms.json").read_text())["frames"])
    expected = eval_indices(len(names))
    files = sorted(renders.glob("eval_img_*.jpg")) or sorted(renders.glob("eval_img_*.png"))
    try:        # PSNR calcolati sulle immagini intere al momento del salvataggio (vedi eval_run.py)
        scores = json.loads((renders / "scores.json").read_text())
    except (OSError, ValueError):
        scores = {}
    if len(files) != len(expected):
        raise ValueError(f"Attese {len(expected)} viste di controllo, trovate {len(files)}: non si possono ricollegare ai video.")
    views = []
    for k, (path, index) in enumerate(zip(files, expected)):
        if path.stem in scores:
            value = float(scores[path.stem])
        else:
            img = np.asarray(Image.open(path).convert("RGB"))
            half = img.shape[1] // 2
            value = psnr(img[:, :half], img[:, half:2 * half])
        vid, t = frame_time(names[index], params)
        views.append({"n": k, "name": names[index], "video": vid, "t": t, "psnr": round(value, 2)})
    ranked = sorted(views, key=lambda v: v["psnr"])
    middle = len(ranked) // 2
    chosen = {v["n"]: "worst" for v in ranked[:KEEP_WORST]}
    for v in ranked[max(0, middle - 1): middle - 1 + KEEP_MIDDLE]:
        chosen.setdefault(v["n"], "middle")
    for v in ranked[-KEEP_BEST:]:
        chosen.setdefault(v["n"], "best")
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("view_*.jpg"):
        old.unlink()
    for v in views:
        if v["n"] in chosen:
            im = Image.open(files[v["n"]]).convert("RGB")
            if im.width > width:
                im = im.resize((width, round(im.height * width / im.width)))
            v["file"], v["group"] = f"view_{v['n']:04d}.jpg", chosen[v["n"]]
            im.save(out / v["file"], quality=82)
    values = [v["psnr"] for v in views]
    summary = {"count": len(views), "mean": round(float(np.mean(values)), 2), "median": round(float(np.median(values)), 2),
               "p10": round(float(np.percentile(values, 10)), 2), "p90": round(float(np.percentile(values, 90)), 2),
               "views": views}
    (out / "views.json").write_text(json.dumps(summary), encoding="utf-8")
    return summary


def main(renders: str, dataset: str, params_file: str, out: str) -> int:
    params = json.loads(Path(params_file).read_text(encoding="utf-8"))
    s = process(Path(renders), Path(dataset), params, Path(out))
    print(f"viste confrontate: {s['count']} | PSNR medio {s['mean']} | 10% peggiore sotto {s['p10']}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 5:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(*sys.argv[1:]))
