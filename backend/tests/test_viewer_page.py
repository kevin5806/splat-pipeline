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
    # tutto il caricamento (mesh o splat) sta dentro la promessa: un errore lanciato subito diventa un rifiuto
    start = html.index("Promise.resolve().then(async () => {")
    block = html[start: html.index("loading.then(", start)]
    assert "openMesh()" in block and "viewer.addSplatScene(FILE" in block
    assert ".catch(e => { if (FILE) fail(e); });" in html
    assert "addEventListener('unhandledrejection'" in html
    assert "shownName" in html                                             # nei messaggi non compare il token


def test_viewer_can_draw_meshes_too():
    """Regressione: «apri nel viewer» su mesh.ply dava uno schermo vuoto, perché il viewer disegna solo splat."""
    html = PAGE.read_text(encoding="utf-8")
    module = (PAGE.parent / "mesh.js").read_text(encoding="utf-8")
    assert "from './mesh.js'" in html and "looksLikeMesh(FILE)" in html
    assert "renderer.render(meshModel.scene, camera)" in html
    assert "export async function loadMesh" in module and "e.name === 'face'" in module
