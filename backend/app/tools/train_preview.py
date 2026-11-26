"""ns-train con un'anteprima: ogni tanto salva alcune inquadrature fisse, per vedere il modello migliorare.

Uso: identico a ns-train (python -m app.tools.train_preview splatfacto ...). Variabili d'ambiente:
    SPLAT_PREVIEW_DIR    dove salvare (se manca, nessuna anteprima)
    SPLAT_PREVIEW_EVERY  ogni quanti passi (predefinito 1000)
    SPLAT_PREVIEW_VIEWS  quante inquadrature (predefinito 4, massimo 8)

L'anteprima non deve MAI far fallire il training: al primo errore si disattiva e il training continua.
Le inquadrature si rendono una alla volta. Non si usa fixed_indices_eval_dataloader di Nerfstudio, che sposta sulla GPU
tutte le immagini di controllo: su una scheda da 8 GB esaurirebbe la memoria.

File: la prima inquadratura è real.jpg + step_000200.jpg; le altre hanno il suffisso _a2, _a3…; views.json dice
quale immagine di controllo è ciascuna.
"""
from __future__ import annotations

import json
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


def suffix(k: int) -> str:
    return "" if k == 0 else f"_a{k + 1}"


def choose_views(camera_to_world, count: int) -> list[int]:
    """`count` camere il più possibile diverse tra loro, per posizione E direzione dello sguardo.

    Si parte da quella a metà e si aggiunge ogni volta la più lontana da quelle già scelte. A parità di dati la scelta
    è sempre la stessa: dopo una ripresa del training le anteprime continuano dalle stesse inquadrature.
    """
    import numpy as np
    m = np.asarray(camera_to_world, dtype=np.float64)
    n = len(m)
    if n == 0:
        return []
    position = m[:, :3, 3]
    spread = float(np.linalg.norm(position - position.mean(axis=0), axis=1).mean()) or 1.0
    forward = -m[:, :3, 2]                                   # in Nerfstudio la camera guarda lungo -Z
    # la direzione pesa di più della posizione: quello che si vuole sono angolazioni diverse
    features = np.concatenate([position / spread, forward * 3.0], axis=1)
    chosen = [n // 2]
    nearest = np.linalg.norm(features - features[chosen[0]], axis=1)
    while len(chosen) < min(count, n):
        k = int(nearest.argmax())
        if nearest[k] <= 1e-9:
            break
        chosen.append(k)
        nearest = np.minimum(nearest, np.linalg.norm(features - features[k], axis=1))
    return chosen


def render(trainer, step: int, out: Path) -> None:
    import torch
    dm, model = trainer.pipeline.datamanager, trainer.pipeline.model
    count = max(1, min(8, int(os.environ.get("SPLAT_PREVIEW_VIEWS", "4"))))
    indices = choose_views(dm.eval_dataset.cameras.camera_to_worlds.cpu().numpy(), count)
    out.mkdir(parents=True, exist_ok=True)
    try:        # i nomi servono solo per l'etichetta (video e minuto)
        names = [Path(dm.eval_dataset.image_filenames[i]).name for i in indices]
        (out / "views.json").write_text(json.dumps([{"suffix": suffix(k), "name": n} for k, n in enumerate(names)]))
    except (AttributeError, IndexError, OSError):
        pass
    was_training = model.training
    model.eval()
    try:
        for k, index in enumerate(indices):
            camera = dm.eval_dataset.cameras[index: index + 1].to(trainer.pipeline.device)
            with torch.no_grad():
                image = model.get_outputs_for_camera(camera)["rgb"].detach().cpu().numpy()
            real = out / f"real{suffix(k)}.jpg"
            if not real.exists():
                _save(dm.cached_eval[index]["image"].detach().cpu().numpy(), real)
            _save(image, out / f"step_{step:06d}{suffix(k)}.jpg")
    finally:
        model.train(was_training)


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
