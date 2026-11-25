"""Configurazione letta dalle variabili d'ambiente."""
from __future__ import annotations

import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
PROJECTS_DIR = DATA_DIR / "projects"
STATIC_DIR = Path(os.environ.get("STATIC_DIR", "/app/static"))

# Modalità finta: le fasi simulano il lavoro senza GPU (sviluppo del pannello, test).
FAKE = os.environ.get("SPLAT_FAKE") == "1"
FAKE_SPEED = float(os.environ.get("SPLAT_FAKE_SPEED", "1.0"))  # >1 = più veloce

# Se impostato, le API richiedono questo token (header X-Token o ?token=).
PANEL_TOKEN = os.environ.get("PANEL_TOKEN") or None

# Alla partenza, riprende da solo i progetti rimasti in esecuzione.
AUTO_RESUME = os.environ.get("AUTO_RESUME", "1") == "1"

VIEWER_PORT = int(os.environ.get("VIEWER_PORT", "7007"))
MAX_UPLOAD_GB = float(os.environ.get("MAX_UPLOAD_GB", "20"))

# Con la patch Pascal (GPU vecchie come la Tesla P4) torch.compile va disattivato.
if os.environ.get("PASCAL_PATCH") == "1":
    os.environ.setdefault("TORCHDYNAMO_DISABLE", "1")
