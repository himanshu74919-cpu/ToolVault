# -*- coding: utf-8 -*-
"""v2.8.5 — upstream ka privacy + waste-call fix, aur dashboard-provider-settings.

Sab OFFLINE: koi network call nahi (upstream_call ko hum call hi nahi karte,
sirf gate/placeholder logic check karte hain).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main  # noqa: E402


# ---------------------------------------------------------------- base/key
def _fresh_state(monkeypatch, **kw):
    """_UPSTREAM_STATE module-level hai -> tests ke beech pollute hota hai.
    Har gate-test apni nayi copy par chale."""
    st = {"key_ok": None, "checked_at": 0.0, "error": None, "skips": 0,
          "fails": 0, "auto_off": False}
    st.update(kw)
    monkeypatch.setattr(main, "_UPSTREAM_STATE", st)
    return st


def test_no_foreign_default_base():
    """Bina UPSTREAM_BASE set kiye hub kisi ANJAAN server par query na bheje."""
    assert main.DEFAULT_UPSTREAM == "" or main.DEFAULT_UPSTREAM.startswith(("http://", "https://"))
    # aur sabse zaroori: default me koi teesre-paksh ka hub hardcoded NA ho
    assert "osint-apis-hub" not in Path(main.__file__).with_name("main.py").read_text(
        encoding="utf-8").split("DEFAULT_UPSTREAM = ")[1].split("\n")[0]


def test_no_demo_key_default():
    src = Path(main.__file__).with_name("main.py").read_text(encoding="utf-8")
    line = src.split('DEFAULT_UPSTREAM_KEY = os.environ.get("UPSTREAM_KEY"')[1].split("\n")[0]
    assert '"Demo"' not in line, line


def test_placeholder_detects_junk_keys():
    p = main.upstream_key_is_placeholder
    for junk in ("", "   ", "Demo", "demo", "KEY", "k", "changeme", "test", "none",
                 "your_key", "GST/abc", "key: 12345", "abcd", "12345678901"):
        assert p(junk) is True, junk
    for real in ("a1b2c3d4e5f6a7b8c9d0", "AKIAIOSFODNN7EXAMPLE",
                 "sk-or-v1-9f3a1b2c3d4e5f6a7b8c9d0e1f2a3b4c", "XyZ_1234567890abcdef"):
        assert p(real) is False, real


def test_gate_skips_when_base_missing(monkeypatch):
    _fresh_state(monkeypatch)
    monkeypatch.setattr(main, "get_setting", lambda k, d="": {"upstream_enabled": "1",
                                                              "upstream_base": "",
                                                              "upstream_key": "a1b2c3d4e5f6a7b8c9d0"}.get(k, d))
    assert main.upstream_available() is False      # base nahi = kisi ko kuch nahi bheja


def test_gate_skips_placeholder_key(monkeypatch):
    _fresh_state(monkeypatch)
    monkeypatch.setattr(main, "get_setting", lambda k, d="": {"upstream_enabled": "1",
                                                              "upstream_base": "https://example.test",
                                                              "upstream_key": "GST/abc"}.get(k, d))
    assert main.upstream_available() is False


def test_gate_respects_disable_switch(monkeypatch):
    monkeypatch.setattr(main, "get_setting", lambda k, d="": {"upstream_enabled": "0",
                                                              "upstream_base": "https://example.test",
                                                              "upstream_key": "a1b2c3d4e5f6a7b8c9d0"}.get(k, d))
    assert main.upstream_available() is False


def test_gate_allows_real_setup(monkeypatch):
    _fresh_state(monkeypatch)
    monkeypatch.setattr(main, "get_setting", lambda k, d="": {"upstream_enabled": "1",
                                                              "upstream_base": "https://example.test",
                                                              "upstream_key": "a1b2c3d4e5f6a7b8c9d0"}.get(k, d))
    assert main.upstream_available() is True


def test_auto_off_after_three_fails(monkeypatch):
    st = dict(main._UPSTREAM_STATE)
    st.update({"fails": 0, "auto_off": False, "key_ok": None})
    monkeypatch.setattr(main, "_UPSTREAM_STATE", st)
    for _ in range(3):
        main._note_upstream_result({"error": "Invalid API key (upstream)"})
    assert main._UPSTREAM_STATE["fails"] == 3
    assert main._UPSTREAM_STATE["auto_off"] is True


def test_status_reports_auto_off_honestly(monkeypatch):
    monkeypatch.setattr(main, "_UPSTREAM_STATE", {"key_ok": False, "checked_at": 1.0,
                                                 "error": "Invalid API key (upstream)",
                                                 "skips": 558, "fails": 3, "auto_off": True})
    monkeypatch.setattr(main, "get_setting", lambda k, d="": d)
    st = main.upstream_key_status()
    assert st["auto_off"] is True and st["consecutive_fails"] == 3
    assert "OFF" in st["note"] or "invalid" in st["note"].lower()


# ------------------------------------------------- providers via dashboard
def test_provider_cfg_falls_back_to_settings(monkeypatch):
    monkeypatch.delenv("NUMINFO_PROVIDER_KEY", raising=False)
    monkeypatch.delenv("NUMINFO_PROVIDER_URL", raising=False)
    monkeypatch.setattr(main, "get_setting",
                        lambda k, d="": {"numinfo_provider_key": "free-key-1234567890",
                                         "numinfo_provider_url": "https://apilayer.net/api/validate"}.get(k, d))
    cfg = main.numinfo_provider()
    assert cfg["key"] == "free-key-1234567890"
    assert cfg["url"] == "https://apilayer.net/api/validate"
    assert cfg["param"] == "number"          # default ab bhi wahi


def test_env_still_wins_over_settings(monkeypatch):
    monkeypatch.setenv("NUMINFO_PROVIDER_KEY", "env-key-1234567890")
    monkeypatch.setattr(main, "get_setting", lambda k, d="": "settings-key-0000000000")
    assert main.numinfo_provider()["key"] == "env-key-1234567890"


def test_provider_keys_are_saveable_from_dashboard():
    """POST /admin/settings in allowlist ke bina key save hi na hoti."""
    src = Path(main.__file__).with_name("main.py").read_text(encoding="utf-8")
    post_blk = src.split('async def admin_save_settings')[1].split('@app.')[0]
    for k in ("numinfo_provider_key", "gst_provider_key", "vehicle_provider_key",
              "numinfo_provider_url", "gst_provider_url"):
        assert f'"{k}"' in post_blk, k
    get_blk = src.split('async def admin_get_settings')[1].split('@app.')[0]
    assert "gst_provider_key" in get_blk and "numinfo_provider_key" in get_blk


def test_status_says_manually_off(monkeypatch):
    monkeypatch.setattr(main, "_UPSTREAM_STATE", {"key_ok": None, "checked_at": 0.0,
                                                  "error": None, "skips": 0,
                                                  "fails": 0, "auto_off": False})
    monkeypatch.setattr(main, "get_setting", lambda k, d="": {"upstream_enabled": "0"}.get(k, d))
    st = main.upstream_key_status()
    assert st["enabled"] is False
    assert "manually OFF" in st["note"]
    assert "native" in st["note"]


def test_settings_save_triggers_backup(monkeypatch):
    """v2.8.7: dashboard settings ko agle restart tak zinda rakhne ke liye
    save karte hi GitHub backup jaana chahiye (boot par DB restore hota hai)."""
    called = []
    monkeypatch.setattr(main, "BACKUP_REPO", "u/r")
    monkeypatch.setattr(main, "BACKUP_TOKEN", "t")
    monkeypatch.setattr(main, "backup_db_to_github",
                        lambda msg="": (called.append(msg), {"success": True})[1])
    assert main.persist_backup_now("test") is True
    import time as _t
    for _ in range(40):
        if called:
            break
        _t.sleep(0.05)
    assert called and "settings" in called[0], called


def test_backup_helper_silent_without_repo(monkeypatch):
    monkeypatch.setattr(main, "BACKUP_REPO", "")
    monkeypatch.setattr(main, "BACKUP_TOKEN", "")
    assert main.persist_backup_now("test") is False


def test_save_endpoint_returns_backup_flag():
    src_txt = Path(main.__file__).with_name("main.py").read_text(encoding="utf-8")
    blk = src_txt.split("async def admin_save_settings")[1].split("@app.")[0]
    assert "persist_backup_now(" in blk and "backup_queued" in blk
