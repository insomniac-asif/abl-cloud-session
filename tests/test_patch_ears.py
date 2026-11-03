"""Tests for patches/patch_ears.py: static rewrites across plausible ears.py shapes, plus
an end-to-end run where a patched ears.py talks to fake Odysseus servers through the
real voice_bridge (tailnet down → SSH-tunnel fallback, owner token attached)."""
from __future__ import annotations

import ast
import importlib.util
import shutil
import socket
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "patches"))
sys.path.insert(0, str(ROOT / "tests"))
import patch_ears as pe  # noqa: E402

from test_voice_bridge import TOKEN, WAV, FakeOdysseus  # noqa: E402


def patched(src: str):
    new, rep = pe.patch_source(textwrap.dedent(src))
    ast.parse(new)
    return new, rep


def test_constant_url_requests_post():
    new, rep = patched('''
        """ears: mic -> ABL."""
        import requests
        ABL_URL = "http://localhost:7800/api/abl/voice/heard"

        def send(wav):
            return requests.post(ABL_URL, files={"audio": ("c.wav", wav)}, timeout=30)
    ''')
    assert "requests.post(_abl_vb.live(ABL_URL), files=" in new
    assert "timeout=30, headers=_abl_vb.auth_headers())" in new
    assert rep.wrapped and rep.headers_added and not rep.manual
    assert new.index('"""ears: mic -> ABL."""') < new.index(pe.MARKER)      # import after docstring
    assert 'ABL_URL = "http://localhost:7800/api/abl/voice/heard"' in new   # literal kept: live() remaps per call


def test_fstring_base_and_header_merge():
    new, rep = patched('''
        import requests
        BASE = "http://127.0.0.1:7800"
        def send(f):
            requests.post(f"{BASE}/api/abl/voice/heard", files=f, headers={"X-Src": "ears"})
    ''')
    assert 'requests.post(_abl_vb.live(f"{BASE}/api/abl/voice/heard"),' in new
    assert 'headers={**({"X-Src": "ears"} or {}), **_abl_vb.auth_headers()})' in new
    assert rep.headers_merged


def test_derived_name_and_urllib_request():
    new, rep = patched('''
        import urllib.request
        HOST = "http://localhost:7800"
        HEARD = HOST + "/api/abl/voice/heard"
        def send(body, ct):
            req = urllib.request.Request(HEARD, data=body, headers={"Content-Type": ct})
            return urllib.request.urlopen(req, timeout=30)
    ''')
    assert "urllib.request.Request(_abl_vb.live(HEARD), data=body" in new
    assert '**_abl_vb.auth_headers()}' in new
    assert not rep.manual


def test_unrelated_hosts_untouched_no_token_leak():
    new, _ = patched('''
        import requests
        ABL = "http://localhost:7800/api/abl/voice/heard"
        def other():
            requests.get("https://api.weather.example/today")
            requests.post("http://localhost:7962/status")
        def send(w):
            requests.post(ABL, data=w)
    ''')
    assert 'requests.get("https://api.weather.example/today")' in new
    assert 'requests.post("http://localhost:7962/status")' in new          # ears' own port: not ABL
    assert new.count("auth_headers()") == 1


def test_requests_request_url_is_second_arg_and_kwargs_reported():
    new, rep = patched('''
        import requests
        U = "http://localhost:7800/api/abl/voice/heard"
        def a(w): requests.request("POST", U, data=w)
        def b(w, **kw): requests.post(U, **kw)
    ''')
    assert 'requests.request("POST", _abl_vb.live(U), data=w, headers=_abl_vb.auth_headers())' in new
    assert "requests.post(_abl_vb.live(U), **kw)" in new
    assert any("**kwargs" in why for _, why in rep.manual)


def test_session_attribute_and_multiline_trailing_comma():
    new, rep = patched('''
        import requests
        class Ears:
            def __init__(self):
                self.session = requests.Session()
                self.url = "http://localhost:7800/api/abl/voice/heard"
            def send(self, w):
                return self.session.post(
                    self.url,
                    files={"audio": w},
                    timeout=20,
                )
    ''')
    assert "_abl_vb.live(self.url)" in new
    assert "headers=_abl_vb.auth_headers()" in new and rep.headers_added


def test_literal_passed_to_custom_helper_is_reported_manual():
    _, rep = patched('''
        TARGET = "http://localhost:7800/api/abl/voice/heard"
        def post_it(url, w): ...
        def go(w): post_it(TARGET, w)
    ''')
    assert any("not passed to a recognised HTTP call" in why for _, why in rep.manual)


def test_idempotent():
    once, _ = patched('''
        import requests
        U = "http://localhost:7800/api/abl/voice/heard"
        def s(w): requests.post(U, data=w)
    ''')
    twice, rep = pe.patch_source(once)
    assert twice == once and rep.already_patched


def test_future_import_ordering():
    new, _ = patched('''
        from __future__ import annotations
        import requests
        U = "http://localhost:7800/x"
        def s(): requests.get(U)
    ''')
    assert new.index("from __future__") < new.index(pe.MARKER)


def test_main_dry_run_apply_and_compile_guard(tmp_path, capsys):
    ears = tmp_path / "ears.py"
    ears.write_text('import requests\nU = "http://localhost:7800/api/abl/voice/heard"\n'
                    'def s(w):\n    return requests.post(U, data=w)\n')
    before = ears.read_text()
    assert pe.main([str(ears)]) == 0 and ears.read_text() == before
    assert "+import voice_bridge as _abl_vb" in capsys.readouterr().out
    assert pe.main([str(ears), "--apply"]) == 0
    assert pe.MARKER in ears.read_text() and (tmp_path / "ears.py.bak-cloudcc-20261103").read_text() == before
    assert pe.main([str(ears), "--apply"]) == 0                         # second run: no-op


def test_main_no_target_found(tmp_path):
    ears = tmp_path / "ears.py"
    ears.write_text("print('no http here')\n")
    assert pe.main([str(ears)]) == 1


# ── end to end ───────────────────────────────────────────────────────────────

def _dead():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{p}"


def test_end_to_end_tailnet_down_falls_back_to_tunnel(tmp_path, monkeypatch):
    pytest.importorskip("requests")
    tunnel = FakeOdysseus(reply="It's 3pm.")
    try:
        ears_dir = tmp_path / "speaker"
        ears_dir.mkdir()
        shutil.copy(ROOT / "src" / "voice_bridge.py", ears_dir / "voice_bridge.py")
        (ears_dir / "ears.py").write_text(textwrap.dedent('''
            """Desktop ears (shape of the real one: requests + constant URL)."""
            import requests
            ABL_URL = "http://localhost:7800/api/abl/voice/heard"

            def send_chunk(wav_bytes):
                r = requests.post(ABL_URL, files={"audio": ("chunk.wav", wav_bytes, "audio/wav")}, timeout=10)
                r.raise_for_status()
                return r.json()
        '''))
        assert pe.main([str(ears_dir / "ears.py"), "--apply"]) == 0

        # tailnet primary is dead; the "SSH tunnel" fallback is the fake Odysseus
        monkeypatch.setenv("ABL_VOICE_TARGETS", f"{_dead()},{tunnel.url}")
        monkeypatch.setenv("ABL_TOKEN", TOKEN)
        monkeypatch.setenv("ABL_VOICE_HEALTH_INTERVAL", "0.2")
        sys.modules.pop("voice_bridge", None)
        spec = importlib.util.spec_from_file_location("ears_under_test", ears_dir / "ears.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        try:
            out = mod.send_chunk(WAV)
            assert out["action"] == "speak" and out["reply"] == "It's 3pm."
            assert tunnel.heard_hits == 1 and tunnel.auth_seen == [f"Bearer {TOKEN}"]
        finally:
            sys.modules["voice_bridge"]._reset_singleton_for_test()
            sys.modules.pop("voice_bridge", None)
    finally:
        tunnel.close()


def test_live_passthrough_for_foreign_urls(monkeypatch):
    monkeypatch.setenv("ABL_VOICE_TARGETS", "http://127.0.0.1:1")
    monkeypatch.setenv("ABL_TOKEN", TOKEN)
    from src import voice_bridge as vb
    vb._reset_singleton_for_test()
    try:
        assert vb.live("https://api.weather.example/x?q=1") == "https://api.weather.example/x?q=1"
        assert vb.live("http://localhost:7800/api/abl/voice/heard?x=1") == "http://127.0.0.1:1/api/abl/voice/heard?x=1"
        assert vb.auth_headers() == {"Authorization": f"Bearer {TOKEN}"}
    finally:
        vb._reset_singleton_for_test()
