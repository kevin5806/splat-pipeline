"""Modelli dei dati: impostazioni, fasi e progetto."""
from __future__ import annotations

import re
import time
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

StageStatus = Literal["pending", "running", "done", "failed", "blocked", "interrupted"]
ProjectStatus = Literal["idle", "queued", "running", "blocked", "failed", "stopped", "interrupted", "done"]

# (id, etichetta). L'ordine è quello di esecuzione.
STAGES: list[tuple[str, str]] = [
    ("probe", "Analisi del video"),
    ("frames", "Estrazione dei frame"),
    ("features", "Feature COLMAP"),
    ("matching", "Matching COLMAP"),
    ("mapping", "Ricostruzione delle camere"),
    ("refine", "Bundle adjustment"),
    ("convert", "Preparazione del dataset"),
    ("test", "Test rapido di qualità"),
    ("train", "Training dello splat"),
    ("export", "Esportazione e pulizia"),
]

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$")


class Settings(BaseModel):
    """Parametri di un progetto. I default sono quelli verificati sul campo."""

    frame_step: int = Field(16, ge=1, le=300, description="Un frame ogni N del video")
    camera_model: Literal["OPENCV", "OPENCV_FISHEYE", "SIMPLE_RADIAL"] = "OPENCV"
    matcher: Literal["vocab_tree", "sequential", "exhaustive"] = "vocab_tree"
    downscale: int = Field(2, ge=1, le=8, description="Fattore di riduzione delle immagini per il training")
    steps: int = Field(30000, ge=1000, le=100000)
    test_steps: int = Field(4000, ge=500, le=20000)
    skip_test: bool = False
    gate_psnr: float = Field(18.5, ge=0, le=50, description="PSNR minimo del test per far partire il training")
    crop_radius: float = Field(3.5, gt=0, le=50, description="Raggio di ritaglio dello splat (unità normalizzate)")
    cache_images: Literal["cpu", "gpu"] = "cpu"


class StageState(BaseModel):
    id: str
    label: str
    status: StageStatus = "pending"
    progress: float = 0.0  # 0-100
    detail: str = ""
    started: Optional[float] = None
    ended: Optional[float] = None
    metrics: dict = Field(default_factory=dict)


class Project(BaseModel):
    name: str
    created: float = Field(default_factory=time.time)
    settings: Settings = Field(default_factory=Settings)
    video: Optional[str] = None  # nome del file dentro input/
    status: ProjectStatus = "idle"
    current_stage: Optional[str] = None
    error: Optional[str] = None
    stages: list[StageState] = Field(default_factory=lambda: [StageState(id=i, label=l) for i, l in STAGES])
    updated: float = Field(default_factory=time.time)

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        if not NAME_RE.match(v):
            raise ValueError("Nome non valido: usa lettere, numeri, '-' e '_' (max 40 caratteri).")
        return v

    def stage(self, sid: str) -> StageState:
        for s in self.stages:
            if s.id == sid:
                return s
        raise KeyError(sid)
