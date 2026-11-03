"""Tests for the abl1 fix scripts. Filesystem-only; no systemd calls are made."""
from __future__ import annotations

import json
import py_compile
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _common  # noqa: E402
import diagnose_unit as du  # noqa: E402
import fix_errands_path as fe  # noqa: E402
import fix_fleet_ip as ff  # noqa: E402
import fix_one_project_context as fo  # noqa: E402


# ── _common ──────────────────────────────────────────────────────────────────

def test_backup_never_overwrites(tmp_path):
    f = tmp_path / "a.json"
    f.write_text("original")
    b = _common.backup(f)
    f.write_text("changed")
    _common.backup(f)
    assert b.read_text() == "original" and b.name == "a.json.bak-cloudcc-20261103"


def test_atomic_write_restores_on_failed_validation(tmp_path):
    f = tmp_path / "a.json"
    f.write_text('{"ok": 1}')
    with pytest.raises(ValueError):
        _common.atomic_write(f, "{broken", validate=lambda p: json.loads(p.read_text()))
    assert json.loads(f.read_text()) == {"ok": 1}


def test_forbidden_paths():
    assert _common.is_forbidden(Path("~/.dsh/.credentials.yaml"))
    assert _common.is_forbidden(Path("/x/node_modules/@deepseek-ai/dsh-api/index.js"))
    assert not _common.is_forbidden(Path("/x/node_modules/left-pad/index.js"))


# ── fleet IP ─────────────────────────────────────────────────────────────────

@pytest.fixture
def fleet(tmp_path):
    r = tmp_path / "abl"
    (r / "fleet").mkdir(parents=True)
    (r / "fleet/registry.json").write_text(json.dumps(
        {"nodes": [{"name": "desktop", "ip": "100.127.203.4", "url": "http://100.127.203.4:7961"},
                   {"name": "other", "ip": "100.127.203.40"}]}, indent=1))
    (r / "odysseus/core").mkdir(parents=True)
    (r / "odysseus/core/egress_gateway.py").write_text("# verified: 100.127.203.4 -> is_private=False\n")
    (r / "odysseus/tests").mkdir()
    (r / "odysseus/tests/t.json").write_text('["100.127.203.4"]')
    con = sqlite3.connect(r / "fleet/fleet.db")
    con.execute("CREATE TABLE nodes (name TEXT, addr TEXT)")
    con.execute("INSERT INTO nodes VALUES ('desktop', '100.127.203.4:7961')")
    con.commit()
    con.close()
    return r


def test_fleet_scan_separates_data_code_db(fleet):
    data, code, db = ff.scan([str(fleet)])
    assert [p.name for p, _ in data] == ["registry.json"]
    assert {p.name for p, _ in code} == {"egress_gateway.py", "t.json"}
    assert [p.name for p in db] == ["fleet.db"]


def test_fleet_apply_is_token_exact(fleet):
    data, _, _ = ff.scan([str(fleet)])
    ff.apply_fix(data)
    reg = json.loads((fleet / "fleet/registry.json").read_text())
    assert reg["nodes"][0]["ip"] == "100.117.30.114"
    assert reg["nodes"][0]["url"] == "http://100.117.30.114:7961"
    assert reg["nodes"][1]["ip"] == "100.127.203.40"          # not a substring hit
    assert (fleet / "fleet/registry.json.bak-cloudcc-20261103").exists()
    assert "100.127.203.4 " in (fleet / "odysseus/core/egress_gateway.py").read_text()  # code untouched


def test_fleet_sqlite_hint(fleet):
    hints = ff.sqlite_suggestions(fleet / "fleet/fleet.db", ff.OLD, ff.NEW)
    assert any("UPDATE" in h and "addr" in h and "1 row" in h for h in hints)


def test_fleet_main_dry_run_writes_nothing(fleet, capsys):
    before = (fleet / "fleet/registry.json").read_text()
    assert ff.main(["--roots", str(fleet)]) == 0
    assert (fleet / "fleet/registry.json").read_text() == before
    assert "DRY RUN" in capsys.readouterr().out


# ── errands path ─────────────────────────────────────────────────────────────

SRC = '''"""Errands scanner.

Reads /mnt/c/Users/Asif/Desktop/ABL_INBOX/errands (docstring mention, untouched).
"""
from __future__ import annotations
from pathlib import Path

ERRANDS = Path("/mnt/c/Users/Asif/Desktop/ABL_INBOX/errands")  # comment /mnt/c/Users/Asif/Desktop/ABL_INBOX
INBOX = '/mnt/c/Users/Asif/Desktop/ABL_INBOX'
DONE = r"/mnt/c/Users/Asif/Desktop/ABL_INBOX/errands/done"
LABEL = f"/mnt/c/Users/Asif/Desktop/ABL_INBOX/{1}"
'''


def test_errands_rewrite(tmp_path, monkeypatch):
    new, done, skipped = fe.rewrite_source(SRC)
    assert len(done) == 3 and len(skipped) == 1                 # f-string reported, not rewritten
    assert "import os  # added by fix_errands_path" in new
    assert new.index("from __future__") < new.index("import os")
    assert "docstring mention, untouched" in new and "# comment /mnt/c/Users/Asif" in new
    f = tmp_path / "scanner.py"
    f.write_text(new)
    py_compile.compile(str(f), doraise=True)
    monkeypatch.setenv("ABL_INBOX_DIR", "/srv/inbox")
    ns: dict = {}
    exec(compile(new.replace("LABEL = f", "LABEL = ''#"), "scanner", "exec"), ns)
    assert str(ns["ERRANDS"]) == "/srv/inbox/errands"
    assert ns["INBOX"] == "/srv/inbox"
    assert ns["DONE"] == "/srv/inbox/errands/done"


def test_errands_default_and_wsl_override(monkeypatch):
    new, _, _ = fe.rewrite_source('import os\nP = "/mnt/c/Users/Asif/Desktop/ABL_INBOX/errands"\n')
    assert new.count("import os") == 1
    monkeypatch.delenv("ABL_INBOX_DIR", raising=False)
    ns: dict = {}
    exec(new, ns)
    assert ns["P"].endswith("/abl/inbox/errands")
    monkeypatch.setenv("ABL_INBOX_DIR", fe.PREFIX)
    exec(new, ns)
    assert ns["P"] == fe.PREFIX + "/errands"                    # WSL desktop keeps old behaviour


def test_errands_main_apply(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    d = tmp_path / "odysseus"
    d.mkdir()
    (d / "errands_scanner.py").write_text('P = "/mnt/c/Users/Asif/Desktop/ABL_INBOX/errands"\n')
    (d / "tests").mkdir()
    (d / "tests/test_x.py").write_text('P = "/mnt/c/Users/Asif/Desktop/ABL_INBOX/errands"\n')
    assert fe.main(["--roots", str(d), "--apply"]) == 0
    assert "os.environ.get" in (d / "errands_scanner.py").read_text()
    assert "os.environ" not in (d / "tests/test_x.py").read_text()   # tests untouched
    assert (tmp_path / "home/abl/inbox/errands").is_dir()


# ── one-project-context ──────────────────────────────────────────────────────

def test_opc_discovery_and_dropin(tmp_path):
    plugins = tmp_path / "local-plugins"
    pd = plugins / "one-project-context-v1"
    (pd / "state").mkdir(parents=True)
    (pd / "state/.ready").write_text("1")
    (pd / "server.js").write_text("fs.writeFileSync(path.join(dir, 'state/.ready'), '1')\n")
    (pd / "ready-docs.md").write_text("doc")
    state = tmp_path / "dsh-state" / "one-project-context-v1"
    state.mkdir(parents=True)
    (state / "ready").write_text("1")
    (tmp_path / "dsh-state" / "other-ready").write_text("1")   # not this plugin

    deep_tmp = tmp_path / "tmpdir" / "a" / "b" / "one-project-context-v1"     # beyond /tmp depth
    deep_tmp.mkdir(parents=True)
    (deep_tmp / "ready").write_text("1")
    assert fo.find_markers([], [(str(tmp_path / "tmpdir"), 2)]) == []

    dirs = fo.find_plugin_dirs(plugins)
    markers = fo.find_markers(dirs, [str(tmp_path / "dsh-state")])
    names = sorted(str(m.relative_to(tmp_path)) for m in markers)
    assert names == ["dsh-state/one-project-context-v1/ready", "local-plugins/one-project-context-v1/state/.ready"]
    hints = fo.source_hints(dirs)
    assert hints and "writeFileSync" in hints[0][2]
    text = fo.render_dropin(markers)
    assert text.count("/bin/rm -f") == 2 and "ExecStartPre=-" in text and "ExecStopPost=-" in text


@pytest.mark.parametrize("bad", ["relative/ready", "/etc/passwd", "/tmp/my ready", "/tmp/x;rm -rf ~/ready"])
def test_opc_dropin_refuses_unsafe(bad):
    with pytest.raises(ValueError):
        fo.render_dropin([Path(bad)])


# ── diagnose_unit ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("log,key,needle", [
    ("Traceback (most recent call last):\nModuleNotFoundError: No module named 'websockets'\n", "module_missing", "websockets"),
    ("OSError: [Errno 98] Address already in use :8097\n", "addr_in_use", "8097"),
    ("FileNotFoundError: [Errno 2] No such file or directory: '/mnt/c/Users/Asif/Desktop/ABL_INBOX/x.json'\n", "wsl_path", "/mnt/c/"),
    ("ConnectionRefusedError: [Errno 111] Connection refused 127.0.0.1:8090\n", "conn_refused", "8090"),
    ("abl-market-bridge.service: Main process exited, code=exited, status=203/EXEC\n", "exec_203", "EXEC"),
    ("KeyError: 'POLYGON_API_KEY'\n", "env_missing", "POLYGON_API_KEY"),
    ("kernel: Out of memory: Killed process 1234 (python3)\n", "oom", "OOM"),
    ("Start request repeated too quickly.\n", "start_limit", "start limit"),
])
def test_classify_log_signatures(log, key, needle):
    f = du.classify_log(log)
    assert f and f[0].key == key
    assert needle.lower() in (f[0].title + f[0].fix + f[0].evidence).lower()


def test_classify_wsl_path_suppresses_generic_file_missing():
    log = "FileNotFoundError: [Errno 2] No such file or directory: '/mnt/c/Users/Asif/data.json'\n"
    keys = [f.key for f in du.classify_log(log)]
    assert "wsl_path" in keys and "file_missing" not in keys


def test_classify_traceback_is_context_only_when_alone():
    assert [f.key for f in du.classify_log("Traceback (most recent call last):\n  x\n")] == ["traceback"]
    ks = [f.key for f in du.classify_log("Traceback (most recent call last):\nKeyError: 'API_TOKEN'\n")]
    assert ks == ["env_missing"]


def test_exec_checks(tmp_path):
    props = {"ExecStart": f"{{ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 {tmp_path}/missing_bridge.py ; }}",
             "WorkingDirectory": str(tmp_path / "nope")}
    keys = {f.key for f in du.exec_checks(props)}
    assert keys == {"exec_path", "wd_missing"}


def test_run_missing_binary_does_not_raise():
    r = _common.run(["definitely-not-a-binary-xyz"])
    assert r.returncode == 127
