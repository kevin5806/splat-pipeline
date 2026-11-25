"""Progetti monitorati in sola lettura e modalità READ_ONLY."""
import os
import time

import pytest
from fastapi.testclient import TestClient

from app import monitor, store
from app.main import app
from app.models import Project


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def _external(name, log):
    p = Project(name=name, external={"train_log": str(log)})
    for st in p.stages:
        if st.id not in ("train", "export"):
            st.status = "done"
    store.save(p)
    return p


def test_monitor_reads_progress_from_log(tmp_path):
    log = tmp_path / "train.log"
    log.write_text("rumore\n9840 (32.80%)       195.389 ms           1 h, 5 m, 39 s       10.63 M\n", encoding="utf-8")
    p = _external("ext1", log)
    assert monitor.update_from_log(p) is True
    st = p.stage("train")
    assert st.status == "running" and st.progress == 32.8 and "1 h, 5 m, 39 s" in st.detail
    assert p.status == "running" and p.current_stage == "train"
    assert monitor.update_from_log(p) is False  # nessun cambiamento: non riscrive


def test_monitor_done_failed_and_stale(tmp_path):
    log = tmp_path / "t.log"
    log.write_text("29999 (100.00%)  1 ms  0 s  1 M\nEXIT=0\n", encoding="utf-8")
    p = _external("ext2", log)
    monitor.update_from_log(p)
    assert p.stage("train").status == "done" and p.status == "done"

    log.write_text("100 (1.00%)  1 ms  1 h  1 M\nEXIT=1\n", encoding="utf-8")
    p = _external("ext3", log)
    monitor.update_from_log(p)
    assert p.status == "failed" and "EXIT=1" in p.stage("train").detail

    log.write_text("100 (1.00%)  1 ms  1 h  1 M\n", encoding="utf-8")
    old = time.time() - 3600
    os.utime(log, (old, old))
    p = _external("ext4", log)
    monitor.update_from_log(p)
    assert p.status == "interrupted"


def test_external_project_cannot_be_started_or_reset(client, tmp_path):
    log = tmp_path / "t.log"
    log.write_text("1 (0.01%)  1 ms  9 h  1 M\n", encoding="utf-8")
    _external("ext5", log)
    assert client.post("/api/projects/ext5/start").status_code == 400
    assert client.post("/api/projects/ext5/reset/train").status_code == 400
    assert client.get("/api/projects/ext5").json()["external"]["train_log"] == str(log)


def test_read_only_blocks_mutations(client, monkeypatch):
    monkeypatch.setattr("app.config.READ_ONLY", True)
    assert client.post("/api/projects", json={"name": "nuovo"}).status_code == 403
    assert client.delete("/api/projects/qualunque").status_code == 403
    assert client.get("/api/projects").status_code == 200
    assert client.get("/api/system").json()["read_only"] is True
