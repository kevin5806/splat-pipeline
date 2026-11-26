"""ns-eval, ma le immagini di confronto si salvano in fretta.

Uso: identico a ns-eval (python -m app.tools.eval_run --load-config ... --render-output-path ...).

Nerfstudio salva per ogni vista un PNG a piena risoluzione: con centinaia di viste su una Tesla P4 la valutazione
passa da 1 a 8 minuti, per poi conservarne una ventina. Qui si sostituisce SOLO quel salvataggio: il PSNR si calcola
sull'immagine intera (prima di ridurla) e si scrive un JPEG ridotto, più scores.json. Se la sostituzione non riesce
(un Nerfstudio diverso) restano i PNG di prima: più lento, ma funziona.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

WIDTH = 1100


class FastImages:
    """Prende il posto di torchvision.utils per la sola funzione save_image."""

    def __init__(self, original=None) -> None:
        self.original = original
        self.scores: dict[str, float] = {}

    def __getattr__(self, name):
        return getattr(self.original, name)

    def save_image(self, image, path, **kwargs) -> None:
        from PIL import Image
        a = np.clip(np.asarray(image.detach().cpu().numpy(), dtype=np.float32), 0, 1)   # canali, righe, colonne
        a = a.transpose(1, 2, 0)
        path = Path(path)
        half = a.shape[1] // 2
        mse = float(((a[:, :half] - a[:, half:2 * half]) ** 2).mean())            # foto vera a sinistra, modello a destra
        self.scores[path.stem] = round(-10 * math.log10(max(mse, 1e-10)), 4)
        im = Image.fromarray((a[..., :3] * 255 + 0.5).astype(np.uint8))
        if im.width > WIDTH:
            im = im.resize((WIDTH, round(im.height * WIDTH / im.width)))
        im.save(path.with_suffix(".jpg"), quality=85)
        (path.parent / "scores.json").write_text(json.dumps(self.scores))


def install() -> None:
    from nerfstudio.pipelines import base_pipeline
    base_pipeline.vutils = FastImages(base_pipeline.vutils)


def main() -> None:
    try:
        install()
    except Exception as e:  # noqa: BLE001
        print(f"[confronto] salvataggio veloce non disponibile: {type(e).__name__}: {e}", flush=True)
    from nerfstudio.scripts.eval import entrypoint
    sys.argv[0] = "ns-eval"
    sys.exit(entrypoint())


if __name__ == "__main__":
    main()
