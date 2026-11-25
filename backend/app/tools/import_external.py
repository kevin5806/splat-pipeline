"""Registra nel pannello un lavoro già in corso, gestito altrove, come progetto in sola lettura.

Uso:
  python -m app.tools.import_external NOME --train-log /percorso/train.log \
      [--metrics '{"refine": {"registered": 1421, "total": 1842}}']

Le fasi prima del training risultano completate (con le metriche indicate); la fase di training viene
aggiornata dal monitor leggendo il log.
"""
from __future__ import annotations

import argparse
import json
import time

from app import store
from app.models import Project


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--train-log", required=True)
    ap.add_argument("--metrics", default="{}", help="JSON: {id_fase: {metrica: valore}}")
    ap.add_argument("--replace", action="store_true")
    a = ap.parse_args()
    if store.load(a.name) is not None:
        if not a.replace:
            raise SystemExit(f"Il progetto '{a.name}' esiste già (usa --replace).")
        store.delete(a.name)
    p = Project(name=a.name, external={"train_log": a.train_log})
    metrics = json.loads(a.metrics)
    now = time.time()
    for st in p.stages:
        if st.id in ("train", "export"):
            continue
        st.status, st.progress, st.started, st.ended = "done", 100.0, now, now
        st.metrics = metrics.get(st.id, {})
    p.stage("export").detail = "da eseguire a mano dopo il training"
    p.status, p.current_stage = "running", "train"
    store.save(p)
    print(f"progetto '{a.name}' registrato")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
