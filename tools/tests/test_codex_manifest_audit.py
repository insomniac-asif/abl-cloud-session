"""Tests for codex_manifest_audit.py against synthetic manifests of every accepted shape."""
from __future__ import annotations

import datetime as dt
import json
import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import codex_manifest_audit as cma  # noqa: E402


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "abl"
    (root / "odysseus/src").mkdir(parents=True)
    (root / "odysseus/src/model_router.py").write_text("# router")
    (root / "odysseus/src/llm_core.py").write_text("# core")
    (root / "tools").mkdir()
    (root / "tools/unique_helper.sh").write_text("#!/bin/sh")
    return root


TASKS = [
    {"id": "T1", "title": "Model router", "status": "done", "files": ["odysseus/src/model_router.py"]},
    {"id": "T2", "title": "Autoresponder", "status": "completed",
     "files": ["odysseus/src/autoresponder.py"], "priority": "P1"},
    {"id": "T3", "title": "Wire llm_core fallback", "status": "in progress",
     "notes": "edit odysseus/src/llm_core.py so the egress fallback hits :8085"},
    {"id": "T4", "title": "Bind 35B to 127.0.0.1 (security: exposed on 0.0.0.0)", "status": "todo"},
    {"id": "T5", "title": "Helper", "state": "open", "description": "see unique_helper.sh"},
    {"id": "T6", "title": "Old idea", "status": "superseded"},
    {"id": "T7", "title": "Write routes unlock", "status": "awaiting approval"},
    {"id": "T8", "title": "Docs", "done": True},
    {"id": "T9", "title": "Half", "status": "wip",
     "artifacts": [{"path": "odysseus/src/model_router.py"}, {"path": "odysseus/src/nope.py"}]},
]


@pytest.mark.parametrize("shape", ["list", "tasks", "items", "idmap", "nested_idmap"])
def test_load_shapes(tmp_path, shape):
    if shape == "list":
        data = TASKS
    elif shape in ("tasks", "items"):
        data = {shape: TASKS, "version": 2}
    elif shape == "idmap":
        data = {t["id"]: {k: v for k, v in t.items() if k != "id"} for t in TASKS}
    else:
        data = {"tasks": {t["id"]: {k: v for k, v in t.items() if k != "id"} for t in TASKS}}
    p = tmp_path / "m.json"
    p.write_text(json.dumps(data))
    tasks = cma.load_tasks(p)
    assert len(tasks) == len(TASKS) and {t.get("id") for t in tasks} == {t["id"] for t in TASKS}


def test_load_rejects_garbage(tmp_path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"version": 1}))
    with pytest.raises(ValueError):
        cma.load_tasks(p)


@pytest.mark.parametrize("raw,want", [
    ("done", "done"), ("Completed ✅", "done"), ("in-progress", "pending"), ("WIP", "pending"),
    ("blocked on Asif", "blocked"), ("superseded by T9", "dropped"), ("", "unknown"),
    (True, "done"), (False, "pending"), (100, "done"), (40, "pending"), ("partially done", "pending"), ("not done", "pending"), ("mostly done", "pending"),
])
def test_status_normalisation(raw, want):
    assert cma.normalise_status(raw) == want


def test_extract_artifacts_and_ports():
    arts, ports = cma.extract_artifacts({
        "files": ["a/b.py"], "notes": "touch ~/abl/odysseus/src/x.py and `routes/y_routes.py`; "
                                      "health on :7800 and port 8085; see README.md http://x.io/a.py"})
    assert {"a/b.py", "~/abl/odysseus/src/x.py", "routes/y_routes.py", "README.md"} <= set(arts)
    assert not any(a.startswith("http") or "x.io" in a for a in arts)
    assert ports == [7800, 8085]


def test_audit_verdicts(tree):
    r = cma.Resolver([str(tree), str(tree / "odysseus")])
    audits = {a.id: a for a in cma.audit(TASKS, r, today=dt.date(2026, 11, 3))}
    assert audits["T1"].verdict == "VERIFIED_DONE"
    assert audits["T2"].verdict == "DONE_MISSING"
    assert audits["T3"].verdict == "MAYBE_DONE"
    assert audits["T4"].verdict == "PENDING"
    assert audits["T5"].verdict == "MAYBE_DONE"          # unique bare filename resolved by index
    assert audits["T6"].verdict == "DROPPED"
    assert audits["T7"].verdict == "BLOCKED"
    assert audits["T8"].verdict == "DONE_UNVERIFIABLE"
    assert audits["T9"].verdict == "PARTIAL"


def test_scoring_orders_security_and_missing_first(tree):
    r = cma.Resolver([str(tree)])
    audits = cma.audit(TASKS, r, today=dt.date(2026, 11, 3))
    todo = sorted((a for a in audits if a.verdict in cma.OPEN_VERDICTS), key=lambda a: -a.score)
    assert todo[0].id in ("T2", "T4")
    t4 = next(a for a in audits if a.id == "T4")
    assert any("security" in w or "exposed" in w or "0.0.0.0" in w for w in t4.score_why)
    t7 = next(a for a in audits if a.id == "T7")
    assert any("write" in w for w in t7.score_why)


def test_port_probe(tree):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        tasks = [{"id": "P", "status": "todo", "notes": f"service on :{port} and :{port + 1 if port < 65535 else 1025}"}]
        a = cma.audit(tasks, cma.Resolver([str(tree)]), probe_ports=True)[0]
        assert port in a.ports_up and a.ports_down
    finally:
        srv.close()


def test_render_and_main(tree, tmp_path, capsys):
    m = tmp_path / "manifest.json"
    m.write_text(json.dumps({"tasks": TASKS}))
    out = tmp_path / "reports"
    rc = cma.main(["--manifest", str(m), "--roots", str(tree), "--out-dir", str(out)])
    assert rc == 0
    md = next(out.glob("codex_todo_*.md")).read_text()
    assert "Prioritised TODO" in md and "DONE_MISSING" in md and "T2" in md
    js = json.loads(next(out.glob("codex_audit_*.json")).read_text())
    assert js["tasks"] == len(TASKS) and js["verdicts"]["VERIFIED_DONE"] == 1


def test_main_missing_manifest(tmp_path):
    assert cma.main(["--manifest", str(tmp_path / "nope.json")]) == 2


@pytest.mark.parametrize("url,ok", [
    ("http://127.0.0.1:7800/build", True), ("http://localhost:7800/x", True),
    ("http://192.168.1.208:7800/x", True), ("http://100.117.30.114:7800/x", True),
    ("https://api.example.com/x", False), ("http://192.168.evil.com/x", False),
])
def test_push_is_local_only(url, ok):
    assert cma.is_local_url(url) is ok


def test_push_refuses_remote():
    ok, msg = cma.push("https://example.com/x", {})
    assert not ok and "non-local" in msg
