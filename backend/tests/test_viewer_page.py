"""Regressione: il viewer riceve il file con ?token=... e la libreria riconosce il formato solo dall'estensione."""
from pathlib import Path

PAGE = Path(__file__).resolve().parents[2] / "web" / "static" / "viewer" / "index.html"


def test_viewer_forces_the_format_ignoring_the_query_string():
    html = PAGE.read_text(encoding="utf-8")
    # il formato si deduce dal nome del file senza query, e si passa esplicitamente alla libreria
    assert "FILE.split('?')[0]" in html
    assert "format: FORMAT" in html
    assert "GS.SceneFormat.Ply" in html


def test_viewer_shows_errors_instead_of_hanging_on_loading():
    html = PAGE.read_text(encoding="utf-8")
    assert "Promise.resolve().then(() => viewer.addSplatScene" in html     # un errore lanciato subito diventa un rifiuto
    assert "addEventListener('unhandledrejection'" in html
    assert "shownName" in html                                             # nei messaggi non compare il token
