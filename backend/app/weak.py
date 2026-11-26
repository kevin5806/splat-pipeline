"""Tratti deboli di un video e come infittirli. Logica pura: nessun COLMAP, nessun ffmpeg.

Un frame è "debole" se COLMAP non ha agganciato la sua camera, o se l'ha agganciata con pochissimi punti 3D.

Nomi dei frame:
    frame_00012.jpg   estrazione normale (un frame ogni `passo`): indice nel video = (12 - 1) * passo
    extra_000123.jpg  frame aggiunto per infittire: il numero È l'indice nel video
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

MIN_OBS = 60            # punti 3D sotto i quali una camera è agganciata "per un filo" (mediana tipica: ~500)
MERGE_S = 1.0           # due tratti deboli più vicini di così sono lo stesso tratto
MIN_RUN_S = 0.5         # un singolo frame mancato non è un tratto debole
PAD_S = 1.5             # margine ai lati: i frame nuovi hanno bisogno di appigli già agganciati
SIGNIFICANT_RUN_S = 2.0
SIGNIFICANT_RATIO = 0.08
MAX_ROUNDS = 2
MAX_EXTRA = 2000        # tetto di frame nuovi per giro: oltre, si alza il passo
IMPROVED = 0.8          # un tratto rimasto almeno all'80% della durata di prima non è migliorato

_BASE = re.compile(r"^frame_(\d+)\.jpg$")
_EXTRA = re.compile(r"^extra_(\d+)\.jpg$")


def extra_name(index: int) -> str:
    return f"extra_{index:06d}.jpg"


def source_index(name: str, base_step: int) -> Optional[int]:
    """L'indice del frame nel video originale, dal nome del file."""
    m = _BASE.match(name)
    if m:
        return (int(m.group(1)) - 1) * base_step
    m = _EXTRA.match(name)
    return int(m.group(1)) if m else None


def sequence(files: list[str], vid: str, base_step: int, observations: dict[str, int], min_obs: int = MIN_OBS) -> list[tuple]:
    """[(indice nel video, nome file, agganciata, buona)] in ordine di tempo."""
    out = []
    for f in files:
        i = source_index(f, base_step)
        if i is None:
            continue
        o = observations.get(f"{vid}/{f}")
        out.append((i, f, o is not None, o is not None and o >= min_obs))
    return sorted(out)


def weak_runs(seq: list[tuple], fps: float, base_step: int) -> list[dict]:
    """Tratti consecutivi di frame non buoni, in indici e in secondi."""
    runs: list[list] = []                       # [primo, ultimo, fine (indice escluso), [nomi]]
    cur = None
    for k, (idx, name, _reg, good) in enumerate(seq):
        if good:
            cur = None
            continue
        following = seq[k + 1][0] if k + 1 < len(seq) else idx + base_step
        end = min(following, idx + base_step)
        if cur is None:
            cur = [idx, idx, end, [name]]
            runs.append(cur)
        else:
            cur[1], cur[2] = idx, end
            cur[3].append(name)
    merged: list[list] = []
    for r in runs:
        if merged and (r[0] - merged[-1][2]) / fps < MERGE_S:
            merged[-1][1], merged[-1][2] = r[1], r[2]
            merged[-1][3] += r[3]
        else:
            merged.append([r[0], r[1], r[2], list(r[3])])
    out = []
    for a, b, end, names in merged:
        seconds = (end - a) / fps
        if seconds >= MIN_RUN_S:
            out.append({"a": a, "b": b, "start_s": round(a / fps, 1), "end_s": round(end / fps, 1), "seconds": round(seconds, 2),
                        "frames": len(names), "sample": names[len(names) // 2]})
    return out


def significant(runs_by_video: dict[str, list[dict]], total_seconds: float) -> bool:
    """Vale la pena intervenire? Un buco lungo, oppure tanti buchi piccoli."""
    runs = [r for rs in runs_by_video.values() for r in rs]
    if not runs:
        return False
    if max(r["seconds"] for r in runs) >= SIGNIFICANT_RUN_S:
        return True
    return total_seconds > 0 and sum(r["seconds"] for r in runs) / total_seconds >= SIGNIFICANT_RATIO


def weak_seconds(runs_by_video: dict[str, list[dict]]) -> float:
    return round(sum(r["seconds"] for rs in runs_by_video.values() for r in rs), 1)


def is_hopeless(vid: str, run: dict, previous_round: Optional[dict]) -> bool:
    """Infittire non ha aiutato: il tratto è rimasto quasi lungo come prima dell'ultimo giro."""
    if not previous_round:
        return False
    for old in previous_round.get("weak_before", {}).get(vid, []):
        if run["a"] <= old["b"] and run["b"] >= old["a"] and run["seconds"] >= IMPROVED * old["seconds"]:
            return True
    return False


def windows_for(runs: list[dict], fps: float, last_index: int) -> list[list[int]]:
    """Finestre (in indici) da infittire: i tratti deboli con un margine, unite se si toccano."""
    pad = int(round(PAD_S * fps))
    out: list[list[int]] = []
    for r in sorted(runs, key=lambda x: x["a"]):
        lo, hi = max(0, r["a"] - pad), min(last_index, r["b"] + pad)
        if out and lo <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], hi)
        else:
            out.append([lo, hi])
    return out


def selected_indices(windows: list[list[int]], step: int) -> list[int]:
    """Tutti gli indici delle finestre che sono multipli del passo (anche quelli già estratti)."""
    return [i for lo, hi in windows for i in range(((lo + step - 1) // step) * step, hi + 1, step)]


def plan_round(seqs: dict[str, list[tuple]], runs_by_video: dict[str, list[dict]], fps: dict[str, float],
               base_steps: dict[str, int], rounds: list[dict]) -> Optional[dict]:
    """Il prossimo giro di infittimento, o None se non c'è più niente da tentare in automatico.

    1° giro: densità doppia nei tratti deboli; 2° giro: quadrupla, solo dove il primo ha aiutato.
    """
    done = [r for r in rounds if r.get("kind") == "densify"]
    if len(done) >= MAX_ROUNDS or (done and not done[-1].get("accepted", True)):
        return None
    previous = done[-1] if done else None
    todo = {vid: [r for r in runs if not is_hopeless(vid, r, previous)] for vid, runs in runs_by_video.items()}
    todo = {vid: runs for vid, runs in todo.items() if runs}
    if not todo:
        return None
    factor = 2 ** (len(done) + 1)
    coarser = 1
    while True:
        videos: dict[str, dict] = {}
        for vid, runs in todo.items():
            base = base_steps[vid]
            step = max(1, (base // factor) * coarser)
            if step >= base:
                continue
            windows = windows_for(runs, fps[vid], seqs[vid][-1][0])
            registered = {i for i, _n, reg, _g in seqs[vid] if reg}
            frames = [i for i in selected_indices(windows, step) if i % base != 0 and i not in registered]
            if frames:
                videos[vid] = {"step": step, "windows": windows, "frames": frames}
        total = sum(len(v["frames"]) for v in videos.values())
        if total == 0:
            return None
        if total <= MAX_EXTRA:
            break
        coarser *= 2
    return {"kind": "densify", "number": len(done) + 1, "videos": videos, "extra_frames": total, "weak_before": todo}


def temporal_pairs(names_by_time: list[str], new: set[str]) -> set[tuple[str, str]]:
    """Coppie da confrontare per le immagini nuove: le 10 vicine nel tempo per lato, più alcune a distanza crescente."""
    offsets = list(range(1, 11)) + [15, 20, 30, 45]
    pairs: set[tuple[str, str]] = set()
    for i, name in enumerate(names_by_time):
        if name not in new:
            continue
        for d in offsets:
            for j in (i - d, i + d):
                if 0 <= j < len(names_by_time):
                    pairs.add(tuple(sorted((name, names_by_time[j]))))
    return pairs


def estimate_seconds(new_frames: int, registered: int) -> float:
    """Stima di un giro. IPOTESI non ancora misurata: feature + matching mirato + registrazione nel modello esistente."""
    grow = (((registered + new_frames) / 828.0) ** 2 - (registered / 828.0) ** 2) * 1080 * 0.5
    return 150 + new_frames * (0.15 + 0.9) + max(0.0, grow) + 300


def list_frames(d: Path) -> list[str]:
    return sorted(p.name for p in d.glob("*.jpg")) if d.exists() else []
