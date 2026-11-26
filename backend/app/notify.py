"""Notifiche quando serve una decisione, quando un lavoro fallisce o finisce.

Destinazioni: ntfy (anche ospitato in casa), un webhook di Home Assistant, un bot Telegram o un webhook qualsiasi.
La configurazione sta in DATA_DIR/notify.json. I segreti (token) non vengono mai rimandati al browser.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import Optional

from . import config

KINDS = ("off", "ntfy", "homeassistant", "telegram", "webhook")
EVENTS = ("blocked", "failed", "done")
DEFAULTS = {"kind": "off", "url": "", "token": "", "chat_id": "", "panel_url": "", "events": list(EVENTS)}
SECRET = "•••• salvato"
TIMEOUT = 10


def _file():
    return config.DATA_DIR / "notify.json"


def load() -> dict:
    try:
        data = json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return {**DEFAULTS, **{k: v for k, v in data.items() if k in DEFAULTS}}


def public() -> dict:
    """La configurazione da mostrare: il token e l'indirizzo di un webhook (che è esso stesso un segreto) sono mascherati."""
    c = load()
    if c["token"]:
        c["token"] = SECRET
    if c["kind"] in ("homeassistant", "webhook") and c["url"]:
        u = urllib.parse.urlsplit(c["url"])
        c["url"] = f"{u.scheme}://{u.netloc}/{SECRET}"
    return c


def _check_url(url: str) -> str:
    u = urllib.parse.urlsplit(url)
    if u.scheme not in ("http", "https") or not u.netloc:
        raise ValueError("L'indirizzo deve iniziare con http:// o https://")
    return url


def save(new: dict) -> dict:
    old = load()
    c = {**old, **{k: v for k, v in new.items() if k in DEFAULTS and v is not None}}
    if c["kind"] not in KINDS:
        raise ValueError(f"Destinazione sconosciuta. Usa: {', '.join(KINDS)}")
    if SECRET in str(c["token"]):                 # il campo non è stato cambiato: resta quello salvato
        c["token"] = old["token"]
    if SECRET in str(c["url"]):
        c["url"] = old["url"]
    c["events"] = [e for e in c["events"] if e in EVENTS]
    if c["kind"] in ("ntfy", "homeassistant", "webhook"):
        _check_url(c["url"])
    if c["kind"] == "ntfy" and not urllib.parse.urlsplit(c["url"]).path.strip("/"):
        raise ValueError("Per ntfy serve l'indirizzo completo di argomento, es. https://ntfy.sh/il-mio-argomento")
    if c["kind"] == "telegram" and not (c["token"] and c["chat_id"]):
        raise ValueError("Per Telegram servono il token del bot e il chat id.")
    if c["panel_url"]:
        _check_url(c["panel_url"])
    f = _file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(c), encoding="utf-8")
    os.replace(tmp, f)
    try:
        os.chmod(f, 0o600)
    except OSError:
        pass
    return public()


def build_request(c: dict, title: str, message: str, link: str = "") -> urllib.request.Request:
    headers = {"Content-Type": "application/json", "User-Agent": "splat-pipeline"}
    kind = c["kind"]
    if kind == "ntfy":
        # pubblicazione in JSON all'indirizzo base: titoli con accenti senza problemi di intestazioni
        u = urllib.parse.urlsplit(c["url"])
        base_path, _, topic = u.path.rstrip("/").rpartition("/")
        url = urllib.parse.urlunsplit((u.scheme, u.netloc, base_path or "/", "", ""))
        body = {"topic": topic, "title": title, "message": message}
        if link:
            body["click"] = link
        if c["token"]:
            headers["Authorization"] = f"Bearer {c['token']}"
    elif kind == "telegram":
        url = f"https://api.telegram.org/bot{c['token']}/sendMessage"
        body = {"chat_id": c["chat_id"], "text": "\n".join(x for x in (title, message, link) if x)}
    elif kind in ("homeassistant", "webhook"):
        url = c["url"]
        body = {"title": title, "message": message, "url": link}
        if c["token"]:
            headers["Authorization"] = f"Bearer {c['token']}"
    else:
        raise ValueError("Notifiche spente.")
    return urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")


def send(title: str, message: str, link: str = "", c: Optional[dict] = None) -> None:
    c = c or load()
    try:
        with urllib.request.urlopen(build_request(c, title, message, link), timeout=TIMEOUT) as r:
            r.read(200)
    except ValueError:
        raise
    except Exception as e:  # noqa: BLE001
        # il messaggio d'errore può contenere l'indirizzo (e quindi il token del bot): si riporta solo il tipo e il codice
        code = getattr(e, "code", None)
        raise RuntimeError(f"Invio non riuscito ({type(e).__name__}{f' {code}' if code else ''}).") from None


def project_message(p) -> Optional[tuple[str, str, str]]:
    """(titolo, messaggio, link) per lo stato attuale del progetto, o None se non c'è niente da notificare."""
    c = load()
    if c["kind"] == "off" or p.status not in c["events"]:
        return None
    detail = ""
    if p.status == "blocked":
        st = next((s for s in p.stages if s.status == "blocked"), None)
        title, detail = f"{p.name}: serve una tua decisione", f"{st.label}: {st.detail}" if st else ""
    elif p.status == "failed":
        st = next((s for s in p.stages if s.status == "failed"), None)
        title, detail = f"{p.name}: errore", f"{st.label}: {st.detail}" if st else ""
    else:
        title, detail = f"{p.name}: completato", "Il modello è pronto."
    base = c["panel_url"].rstrip("/")
    return title, detail[:400], f"{base}/p/{p.name}" if base else ""


def project_event(p) -> None:
    """Chiamata dal lavoratore: non solleva mai errori."""
    try:
        m = project_message(p)
        if m:
            send(*m)
    except Exception as e:  # noqa: BLE001
        print(f"[notifiche] {e}", flush=True)
