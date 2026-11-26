"""ns-train con un'anteprima: ogni tanto salva l'immagine di UNA inquadratura fissa, per vedere il modello migliorare.

Uso: identico a ns-train (python -m app.tools.train_preview splatfacto ...). Variabili d'ambiente:
    SPLAT_PREVIEW_DIR    dove salvare (se manca, nessuna anteprima)
    SPLAT_PREVIEW_EVERY  ogni quanti passi (predefinito 1000)

L'anteprima non deve MAI far fallire il training: al primo errore si disattiva e il training continua.
Si rende una sola inquadratura. Non si usa fixed_indices_eval_dataloader di Nerfstudio, che sposta sulla GPU
tutte le immagini di controllo: su una scheda da 8 GB esaurirebbe la memoria.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

WIDTH = 960
_disabled = False


def due(step: int, every: int) -> bool:
    return step == 200 or (step > 0 and step % every == 0)


def _save(array, path: Path) -> None:
    import numpy as np
    from PIL import Image
    a = np.asarray(array)
    if a.dtype != np.uint8:
        a = (np.clip(a, 0, 1) * 255).astype(np.uint8)
    im = Image.fromarray(a[..., :3])
    if im.width > WIDTH:
        im = im.resize((WIDTH, round(im.height * WIDTH / im.width)))
    tmp = path.with_suffix(".part.jpg")
    im.save(tmp, quality=85)
    tmp.replace(path)


def render(trainer, step: int, out: Path) -> None:
    import torch
    dm, model = trainer.pipeline.datamanager, trainer.pipeline.model
    index = len(dm.eval_dataset) // 2
    out.mkdir(parents=True, exist_ok=True)
    camera = dm.eval_dataset.cameras[index: index + 1].to(trainer.pipeline.device)
    was_training = model.training
    model.eval()
    try:
        with torch.no_grad():
            rgb = model.get_outputs_for_camera(camera)["rgb"]
        image = rgb.detach().cpu().numpy()
    finally:
        model.train(was_training)
    real = out / "real.jpg"
    if not real.exists():
        _save(dm.cached_eval[index]["image"].detach().cpu().numpy(), real)
    _save(image, out / f"step_{step:06d}.jpg")


def install() -> None:
    out = os.environ.get("SPLAT_PREVIEW_DIR")
    if not out:
        return
    every = max(100, int(os.environ.get("SPLAT_PREVIEW_EVERY", "1000")))
    from nerfstudio.engine.trainer import Trainer
    original = Trainer.eval_iteration

    def eval_iteration(self, step: int):
        global _disabled
        result = original(self, step)
        if not _disabled and due(step, every):
            try:
                render(self, step, Path(out))
            except Exception as e:  # noqa: BLE001
                _disabled = True
                print(f"[anteprima] disattivata, il training continua: {type(e).__name__}: {e}", flush=True)
        return result

    Trainer.eval_iteration = eval_iteration


def main() -> None:
    try:
        install()
    except Exception as e:  # noqa: BLE001
        print(f"[anteprima] non disponibile: {type(e).__name__}: {e}", flush=True)
    from nerfstudio.scripts.train import entrypoint
    sys.argv[0] = "ns-train"
    sys.exit(entrypoint())


if __name__ == "__main__":
    main()
