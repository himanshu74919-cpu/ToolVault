"""
OSINT & Multi-Utility API Hub  (v2.0)
=====================================
Cloud-ready API hub with SQLite database + browser dashboard.

* 42 public endpoints in the style of   https://osint-apis-hub.onrender.com/api/<path>?key=Demo&<param>=...
* `?key=Demo` works out of the box. Custom API keys are stored in SQLite.
* Every endpoint first tries a NATIVE (free, no-key) data source; if the native
  source cannot give a complete answer it falls back / merges upstream data.
* All responses are cached in SQLite for speed.
* Web dashboard at  /dashboard  (tablet friendly) to manage the database,
  API keys, logs, cache and settings - no coding required.

Author: built for deployment on Render / Railway / Koyeb / any Docker host.
"""

from __future__ import annotations

import csv
import hashlib
import inspect
import io
import json
import os
import random
import re
import secrets
import sqlite3
import time
import html as html_lib
import urllib.parse
import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import httpx
from pathlib import Path
from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

# =====================================================================
# CONFIGURATION  (everything can be changed from the dashboard too)
# =====================================================================
APP_VERSION = "2.6.9"
DB_PATH = os.environ.get("DB_PATH", "osint_database.db")
PORT = int(os.environ.get("PORT", "8000"))

DEFAULT_BRAND = os.environ.get("BRAND_TAG", "@Supermannn_x")
DEFAULT_UPSTREAM = os.environ.get("UPSTREAM_BASE", "https://osint-apis-hub.onrender.com")
DEFAULT_UPSTREAM_KEY = os.environ.get("UPSTREAM_KEY", "Demo")
DEMO_KEY = os.environ.get("DEMO_KEY", "Demo")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")

# ---------- v2.6 PERSISTENCE (Render free plan par DB file udd jati hai) ----------
# MASTER_API_KEY=key1,key2  -> ye keys HAMESHA chalti hain (restart/deploy ke baad bhi)
MASTER_API_KEYS = [k.strip() for k in (os.environ.get("MASTER_API_KEY") or
                                       os.environ.get("API_KEYS") or "").replace(";", ",").split(",") if k.strip()]
# GITHUB_BACKUP_REPO=user/repo (+ token) -> DB apne aap GitHub par backup/restore hogi
BACKUP_REPO = (os.environ.get("GITHUB_BACKUP_REPO") or "").strip()
BACKUP_TOKEN = (os.environ.get("GITHUB_BACKUP_TOKEN") or os.environ.get("GITHUB_TOKEN") or "").strip()
BACKUP_PATH = (os.environ.get("GITHUB_BACKUP_PATH") or "backup/osint_database.db").strip()
BACKUP_BRANCH = (os.environ.get("GITHUB_BACKUP_BRANCH") or "").strip()
try:
    BACKUP_MINUTES = float(os.environ.get("GITHUB_BACKUP_MINUTES") or 15)
except Exception:
    BACKUP_MINUTES = 15.0
_BACKUP_STATE: Dict[str, Any] = {"restored": None, "last_backup": None, "ok": None, "error": None}

# ---------- v2.6 DB ko GitHub se wapas lao (import se pehle, sabse pehle) ----------
def _gh_headers() -> Dict[str, str]:
    h = {"Accept": "application/vnd.github.v3.raw", "User-Agent": "osint-hub-persistence"}
    if BACKUP_TOKEN:
        h["Authorization"] = f"Bearer {BACKUP_TOKEN}"
    return h


def db_snapshot_bytes() -> bytes:
    """SQLite ka safe snapshot (WAL ka data bhi shaamil) — temp file ke through."""
    tmp = DB_PATH + ".snap"
    src = sqlite3.connect(DB_PATH, timeout=30)
    try:
        dst = sqlite3.connect(tmp)
        try:
            src.backup(dst)
            dst.commit()
        finally:
            dst.close()
    finally:
        src.close()
    try:
        data = Path(tmp).read_bytes()
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass
    return data


def restore_db_from_github() -> Dict[str, Any]:
    """GitHub par rakhi DB ko wapas lao (boot par). File na ho to chup-chaap skip."""
    if not (BACKUP_REPO and BACKUP_TOKEN):
        return {"success": False, "error": "GITHUB_BACKUP_REPO / GITHUB_BACKUP_TOKEN set nahi hai"}
    url = f"https://api.github.com/repos/{BACKUP_REPO}/contents/{BACKUP_PATH}"
    params = {"ref": BACKUP_BRANCH} if BACKUP_BRANCH else None
    try:
        r = httpx.get(url, headers=_gh_headers(), params=params, timeout=45, follow_redirects=True)
        if r.status_code == 404:
            _BACKUP_STATE["restored"] = "no-backup-yet"
            return {"success": False, "error": "Backup file abhi GitHub par nahi hai (pehli baar chal raha hai)"}
        r.raise_for_status()
        if len(r.content) < 512:                       # DB itni chhoti nahi hoti
            return {"success": False, "error": "Backup file khaali/kharab lag rahi hai"}
        for ext in ("", "-wal", "-shm"):
            try:
                os.remove(DB_PATH + ext)
            except Exception:
                pass
        Path(DB_PATH).write_bytes(r.content)
        _BACKUP_STATE["restored"] = f"{len(r.content)} bytes"
        return {"success": True, "restored_bytes": len(r.content), "repo": BACKUP_REPO, "path": BACKUP_PATH}
    except Exception as e:
        _BACKUP_STATE["restored"] = f"error: {e}"[:120]
        return {"success": False, "error": str(e)[:200]}


def backup_db_to_github(message: str = "auto backup") -> Dict[str, Any]:
    """DB ko GitHub par safe karo (har BACKUP_MINUTES minute me apne aap bhi)."""
    if not (BACKUP_REPO and BACKUP_TOKEN):
        return {"success": False, "error": "GITHUB_BACKUP_REPO / GITHUB_BACKUP_TOKEN set nahi hai"}
    api = f"https://api.github.com/repos/{BACKUP_REPO}/contents/{BACKUP_PATH}"
    hdr = _gh_headers()
    try:
        import base64
        try:
            data = db_snapshot_bytes()
        except Exception:
            data = Path(DB_PATH).read_bytes()
        sha = None
        g = httpx.get(api, headers={**hdr, "Accept": "application/vnd.github+json"},
                      params={"ref": BACKUP_BRANCH} if BACKUP_BRANCH else None, timeout=30)
        if g.status_code == 200:
            sha = (g.json() or {}).get("sha")
        body: Dict[str, Any] = {"message": f"hub db backup: {message}",
                                "content": base64.b64encode(data).decode(), "committer":
                                {"name": "osint-api-hub", "email": "hub@users.noreply.github.com"}}
        if sha:
            body["sha"] = sha
        if BACKUP_BRANCH:
            body["branch"] = BACKUP_BRANCH
        r = httpx.put(api, headers=hdr, json=body, timeout=60)
        ok = r.status_code in (200, 201)
        _BACKUP_STATE.update({"last_backup": now_ist("%d-%m-%Y %H:%M"), "ok": ok,
                              "error": None if ok else f"HTTP {r.status_code}: {r.text[:120]}"})
        return {"success": ok, "bytes": len(data), "message": message,
                "error": None if ok else _BACKUP_STATE["error"]}
    except Exception as e:
        _BACKUP_STATE.update({"ok": False, "error": str(e)[:150]})
        return {"success": False, "error": str(e)[:200]}


def _auto_backup_loop():
    while True:
        time.sleep(max(60.0, BACKUP_MINUTES * 60))
        try:
            backup_db_to_github("auto")
        except Exception:
            pass


def _upstream_key_effective() -> str:
    return (get_setting("upstream_key", DEFAULT_UPSTREAM_KEY) or DEFAULT_UPSTREAM_KEY).strip()


def upstream_key_is_placeholder() -> bool:
    """Demo/khali key = upstream kaam nahi karega (waste call se bacho)."""
    return _upstream_key_effective() in ("", "Demo", "demo", "KEY")


if BACKUP_REPO and BACKUP_TOKEN and os.environ.get("GITHUB_BACKUP_RESTORE", "1") != "0":
    try:
        restore_db_from_github()
    except Exception:
        pass

UA = ("Mozilla/5.0 (Linux; Android 13; SM-X210) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(
    title="OSINT & Multi-Utility API Hub",
    description="42 OSINT / utility endpoints with SQLite database + web dashboard.",
    version=APP_VERSION,
    docs_url="/docs",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class HeadSupportMiddleware:
    """HEAD requests ko GET ki tarah handle karta hai (body ke bina).

    Zaroori kyun hai: UptimeRobot / koi bhi uptime monitor HEAD request bhejta hai.
    FastAPI ke @app.get routes par HEAD → "405 Method Not Allowed" aata tha,
    jisse monitoring me server hamesha DOWN dikh raha tha.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and scope.get("method") == "HEAD":
            scope = dict(scope, method="GET")

            async def send_head(message):
                if message["type"] == "http.response.start":
                    headers = [
                        (k, v) for k, v in message.get("headers", [])
                        if k.lower() not in (b"content-length", b"transfer-encoding")
                    ]
                    headers.append((b"content-length", b"0"))
                    await send({"type": "http.response.start",
                                "status": message["status"], "headers": headers})
                elif message["type"] == "http.response.body":
                    await send({"type": "http.response.body",
                                "body": b"", "more_body": False})
                else:
                    await send(message)

            await self.app(scope, receive, send_head)
        else:
            await self.app(scope, receive, send)


app.add_middleware(HeadSupportMiddleware)


# =====================================================================
# DATABASE (SQLite)
# =====================================================================
@contextmanager
def db():
    """SQLite connection scope: commit on success, rollback on error, always close."""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with db() as conn:
        c = conn.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            api_key TEXT UNIQUE NOT NULL,
            name TEXT DEFAULT '',
            note TEXT DEFAULT '',
            is_active INTEGER DEFAULT 1,
            requests INTEGER DEFAULT 0,
            created_at TEXT,
            last_used_at TEXT,
            expires_at TEXT,
            allowed_endpoints TEXT DEFAULT '*',
            device_lock INTEGER DEFAULT 0,
            bound_devices TEXT DEFAULT '',
            max_devices INTEGER DEFAULT 1,
            rate_limit INTEGER DEFAULT 0,
            customer TEXT DEFAULT '',
            price TEXT DEFAULT ''
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS custom_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL DEFAULT 'general',
            key_value TEXT NOT NULL,
            data TEXT NOT NULL,
            note TEXT DEFAULT '',
            source TEXT DEFAULT 'dashboard',
            created_at TEXT,
            updated_at TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS request_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT, endpoint TEXT, api_key TEXT, params TEXT,
            source TEXT, status INTEGER, ms INTEGER, ip TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS response_cache (
            cache_key TEXT PRIMARY KEY,
            endpoint TEXT,
            payload TEXT,
            created_at REAL,
            hits INTEGER DEFAULT 0
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS settings (
            k TEXT PRIMARY KEY, v TEXT
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_records_cat_key ON custom_records(category, key_value)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_logs_ts ON request_logs(ts)")
        conn.commit()

    migrate_db()
    init_extra_tables()

    defaults = {
        "upstream_base": DEFAULT_UPSTREAM,
        "upstream_key": DEFAULT_UPSTREAM_KEY,
        "upstream_enabled": "1",
        "demo_key_enabled": "1",
        "cache_ttl": "3600",
        "cache_enabled": "1",
        "rate_limit_per_min": "120",
        "max_request_seconds": "50",
        "brand_tag": DEFAULT_BRAND,
        "upi_id": os.environ.get("UPI_ID", ""),
        "upi_name": os.environ.get("UPI_NAME", "OSINT API Hub"),
        "telegram_support": DEFAULT_BRAND,
        "store_title": "OSINT API Hub",
        "store_tagline": "Utility APIs — TAC-only device hints · IP · IFSC · Pincode · YouTube; restricted personal lookups disabled",
        "store_plans": "",
        "webhook_secret": os.environ.get("WEBHOOK_SECRET", ""),
        "admin_password": ADMIN_PASSWORD,
        "github_token": GITHUB_TOKEN,
    }
    for k, v in defaults.items():
        if get_setting(k) is None:
            set_setting(k, v)

    # ---- v2.6.4: Render env hamesha JEETEGI ----
    # Pehle: DB ki purani value env ko dabaa deti thi (jaise upstream_key="Demo").
    # Ab: jo cheez env me set hai, wo boot par DB me bhi likh di jati hai -> env = final.
    env_sync = {
        "upstream_base": os.environ.get("UPSTREAM_BASE"),
        "upstream_key": os.environ.get("UPSTREAM_KEY"),
        "upstream_enabled": os.environ.get("UPSTREAM_ENABLED"),
        "admin_password": os.environ.get("ADMIN_PASSWORD"),
        "brand_tag": os.environ.get("BRAND_TAG"),
        "upi_id": os.environ.get("UPI_ID"),
        "upi_name": os.environ.get("UPI_NAME"),
        "webhook_secret": os.environ.get("WEBHOOK_SECRET"),
        "github_token": os.environ.get("GITHUB_TOKEN"),
        "hibp_api_key": os.environ.get("HIBP_API_KEY"),
        "cache_ttl": os.environ.get("CACHE_TTL"),
        "rate_limit_per_min": os.environ.get("RATE_LIMIT_PER_MIN"),
        "max_request_seconds": os.environ.get("MAX_REQUEST_SECONDS"),
        "telegram_support": os.environ.get("TELEGRAM_SUPPORT"),
        "store_title": os.environ.get("STORE_TITLE"),
        "store_tagline": os.environ.get("STORE_TAGLINE"),
    }
    for _k, _v in list(os.environ.items()):        # SETTING_XXXX=value -> setting "xxxx"
        if _k.startswith("SETTING_") and _v:
            env_sync[_k[len("SETTING_"):].lower()] = _v
    _synced = []
    for k, v in env_sync.items():
        if v in (None, ""):
            continue
        # v2.6.7 suraksha: insecure default kabhi aapke asli password ko overwrite na kare
        if k == "admin_password" and str(v) == "admin123":
            _cur = get_setting("admin_password", "") or ""
            if _cur and _cur != "admin123":
                _synced.append(k + "(rakha-default-ignore)")
                continue
        try:
            set_setting(k, str(v))
            _synced.append(k)
        except Exception:
            pass
    if _synced:
        try:
            with db() as conn:
                conn.execute("INSERT INTO settings(k,v) VALUES('env_synced_keys',?) "
                             "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (",".join(sorted(_synced)),))
                conn.commit()
        except Exception:
            pass


NEW_KEY_COLUMNS = {
    "expires_at": "TEXT",
    "allowed_endpoints": "TEXT DEFAULT '*'",
    "device_lock": "INTEGER DEFAULT 0",
    "bound_devices": "TEXT DEFAULT ''",
    "max_devices": "INTEGER DEFAULT 1",
    "rate_limit": "INTEGER DEFAULT 0",
    "customer": "TEXT DEFAULT ''",
    "price": "TEXT DEFAULT ''",
}


def migrate_db():
    """Add new columns to an existing database (safe to run on every boot)."""
    try:
        with db() as conn:
            existing = {r["name"] for r in conn.execute("PRAGMA table_info(api_keys)")}
            for col, ddl in NEW_KEY_COLUMNS.items():
                if col not in existing:
                    conn.execute(f"ALTER TABLE api_keys ADD COLUMN {col} {ddl}")
            conn.commit()
    except Exception:
        pass


_SETTINGS_CACHE: Dict[str, str] = {}


def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    # v2.6: env override — SETTING_UPSTREAM_KEY=xyz (restart par bhi permanent)
    _env = os.environ.get("SETTING_" + key.upper())
    if _env not in (None, ""):
        return _env
    if key in _SETTINGS_CACHE:
        return _SETTINGS_CACHE[key]
    try:
        with db() as conn:
            row = conn.execute("SELECT v FROM settings WHERE k=?", (key,)).fetchone()
        val = row["v"] if row else None
    except Exception:
        val = None
    if val is not None:
        _SETTINGS_CACHE[key] = val
        return val
    return default


def set_setting(key: str, value: str):
    _SETTINGS_CACHE[key] = str(value)
    with db() as conn:
        conn.execute("INSERT INTO settings(k,v) VALUES(?,?) "
                     "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (key, str(value)))
        conn.commit()


def now_ist(fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    return datetime.now(IST).strftime(fmt)


# =====================================================================
# BRANDING  (har response me "Powered by @Supermannn_x")
# =====================================================================
def brand() -> str:
    return (get_setting("brand_tag", DEFAULT_BRAND) or DEFAULT_BRAND).strip()


def brand_line() -> str:
    return f"⚡ Powered by {brand()}  |  API Developer / Telegram: {brand()}"


def add_brand(text: Optional[str]) -> Optional[str]:
    """Append the credit footer to any formatted text card (only once)."""
    if not text or not isinstance(text, str):
        return text
    tag = brand()
    if tag and tag not in text:
        return text.rstrip() + f"\n\n{brand_line()}"
    return text


def apply_brand(payload: Any) -> Any:
    """Add branding to a response payload (dict) - both JSON meta and text card."""
    if isinstance(payload, dict):
        if isinstance(payload.get("formatted"), str):
            payload["formatted"] = add_brand(payload["formatted"])
        if "powered_by" not in payload:
            payload["powered_by"] = brand()
    return payload


def log_request(endpoint: str, api_key: str, params: Dict[str, Any], source: str,
                status: int, ms: int, ip: str = ""):
    try:
        with db() as conn:
            conn.execute(
                "INSERT INTO request_logs(ts,endpoint,api_key,params,source,status,ms,ip)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (now_ist(), endpoint, api_key or "", json.dumps(params, default=str)[:1000],
                 source, status, ms, ip))
            conn.commit()
        if api_key and api_key != "anonymous":
            with db() as conn:
                conn.execute("UPDATE api_keys SET requests=requests+1, last_used_at=? WHERE api_key=?",
                             (now_ist(), api_key))
                conn.commit()
    except Exception:
        pass


def cache_get(cache_key: str, ttl: int) -> Optional[Any]:
    if not ttl:
        return None
    try:
        with db() as conn:
            row = conn.execute(
                "SELECT payload, created_at FROM response_cache WHERE cache_key=?", (cache_key,)).fetchone()
        if not row:
            return None
        if time.time() - float(row["created_at"]) > ttl:
            return None
        with db() as conn:
            conn.execute("UPDATE response_cache SET hits=hits+1 WHERE cache_key=?", (cache_key,))
            conn.commit()
        return json.loads(row["payload"])
    except Exception:
        return None


def cache_set(cache_key: str, endpoint: str, payload: Any):
    try:
        with db() as conn:
            conn.execute(
                "INSERT INTO response_cache(cache_key,endpoint,payload,created_at,hits)"
                " VALUES(?,?,?,?,0) ON CONFLICT(cache_key) DO UPDATE SET "
                "payload=excluded.payload, created_at=excluded.created_at",
                (cache_key, endpoint, json.dumps(payload, default=str), time.time()))
            conn.commit()
    except Exception:
        pass


# =====================================================================
# HTTP HELPERS
# =====================================================================
async def http_get(url: str, params: Optional[Dict[str, Any]] = None, timeout: float = 25,
                   headers: Optional[Dict[str, str]] = None) -> Tuple[Optional[Any], Optional[str]]:
    hdrs = {"User-Agent": UA, "Accept": "application/json, text/plain, */*"}
    if headers:
        hdrs.update(headers)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=hdrs) as client:
            resp = await client.get(url, params=params)
        if resp.status_code >= 500:
            return None, f"HTTP {resp.status_code}"
        try:
            return resp.json(), None
        except Exception:
            # HTML / plain-text error pages (Cloudflare 523, "Origin unreachable", ...)
            return None, f"non-JSON response (HTTP {resp.status_code})"
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def looks_like_upstream_error(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    if "errorMsg" in data:
        return True
    if "detail" in data:  # FastAPI style error wrapper / rate-limit messages
        return True
    if "error" in data and len(data) <= 4:
        return True
    if str(data.get("status", "")).upper() in ("WARNING", "FAILED", "ERROR", "UNAUTHORIZED"):
        return True
    if data.get("success") is False and len(data) <= 3:
        return True
    return False


# v2.6.1: upstream key ki health yaad rakho — invalid key par 6-23 sec waste na ho
_UPSTREAM_STATE: Dict[str, Any] = {"key_ok": None, "checked_at": 0.0, "error": None, "skips": 0}


def upstream_key_status() -> Dict[str, Any]:
    bad = _UPSTREAM_STATE.get("key_ok") is False
    age = time.time() - float(_UPSTREAM_STATE.get("checked_at") or 0)
    return {
        "key_ok": _UPSTREAM_STATE.get("key_ok"),
        "last_error": _UPSTREAM_STATE.get("error"),
        "checked": (f"{int(age)}s pehle" if _UPSTREAM_STATE.get("checked_at") else None),
        "skipped_calls": _UPSTREAM_STATE.get("skips", 0),
        "cooldown_left_sec": max(0, int(float(os.environ.get("UPSTREAM_BAD_COOLDOWN", "900")) - age)) if bad else 0,
        "note": ("Upstream key INVALID hai — Render -> Environment me SETTING_UPSTREAM_KEY=apni-asli-key "
                 "lagao (ya dashboard Settings me). Tab tak hub native data se kaam kar raha hai.")
                if (bad and not upstream_key_is_placeholder()) else
                ("key nahi lagi/placeholder — upstream skip ho raha hai (native data use hota hai)"
                 if upstream_key_is_placeholder() else "ok / unknown"),
    }


def upstream_available() -> bool:
    """False = upstream call skip karo (key nahi lagi / invalid / cooldown)."""
    if get_setting("upstream_enabled", "1") != "1":
        return False
    if upstream_key_is_placeholder():
        _UPSTREAM_STATE.update({"key_ok": False,
                                "error": "upstream key configured nahi hai (Demo placeholder)",
                                "checked_at": time.time()})
        return False
    if _UPSTREAM_STATE.get("key_ok") is not False:
        return True
    age = time.time() - float(_UPSTREAM_STATE.get("checked_at") or 0)
    if age >= float(os.environ.get("UPSTREAM_BAD_COOLDOWN", "900")):
        return True                      # cooldown khatam — dobara try karo
    _UPSTREAM_STATE["skips"] = int(_UPSTREAM_STATE.get("skips") or 0) + 1
    return False


def _note_upstream_result(data: Any) -> None:
    """Upstream ke jawab se key ki health update karo."""
    if data is None:
        return
    txt = str(data)[:300].lower()
    if "invalid api key" in txt or "invalid key" in txt or "unauthorized" in txt:
        _UPSTREAM_STATE.update({"key_ok": False, "checked_at": time.time(),
                                "error": "Invalid API key (upstream)"})
    elif not looks_like_upstream_error(data):
        _UPSTREAM_STATE.update({"key_ok": True, "checked_at": time.time(), "error": None})


async def upstream_call(path: str, params: Dict[str, Any], timeout: float = 45,
                        retries: int = 1) -> Tuple[Optional[Any], Optional[str]]:
    """Call the reference hub (osint-apis-hub.onrender.com by default)."""
    if get_setting("upstream_enabled", "1") != "1":
        return None, "upstream disabled"
    if not upstream_available():          # v2.6.1: invalid key par turant native path
        return None, "upstream key invalid (skip)"
    base = (get_setting("upstream_base", DEFAULT_UPSTREAM) or DEFAULT_UPSTREAM).rstrip("/")
    key = get_setting("upstream_key", DEFAULT_UPSTREAM_KEY) or "Demo"
    q = {k: v for k, v in params.items() if v not in (None, "")}
    q["key"] = key
    last_err = "unknown upstream error"
    for attempt in range(retries + 1):
        data, err = await http_get(f"{base}/api/{path}", params=q, timeout=timeout)
        _note_upstream_result(data)
        if data is not None and not looks_like_upstream_error(data):
            return data, None
        last_err = err or str(data)[:200]
        if _UPSTREAM_STATE.get("key_ok") is False:
            break                          # key hi galat hai — retry ka koi fayda nahi
        if attempt == 0:
            await asyncio_sleep(1.0)
    return None, last_err


async def upstream_self_test(path: str = "ip-v2", probe: str = "8.8.8.8") -> Dict[str, Any]:
    """Admin ke liye: upstream key chalti hai ya nahi — seedha jawab."""
    base = (get_setting("upstream_base", DEFAULT_UPSTREAM) or DEFAULT_UPSTREAM).rstrip("/")
    key = get_setting("upstream_key", DEFAULT_UPSTREAM_KEY) or "Demo"
    t0 = time.time()
    data, err = await http_get(f"{base}/api/{path}", params={"key": key, "ip": probe}, timeout=25)
    ms = int((time.time() - t0) * 1000)
    _note_upstream_result(data)
    key_txt = (key[:4] + "***" + key[-2:]) if len(key) > 6 else "***"
    return {"success": bool(data is not None and not looks_like_upstream_error(data)),
            "upstream_base": base, "key_used": key_txt, "path_tested": path, "took_ms": ms,
            "error": err or (str(data)[:200] if data is not None and looks_like_upstream_error(data) else None),
            "preview": str(data)[:200] if data is not None else None,
            "hint": ("Key galat hai — Render me SETTING_UPSTREAM_KEY=asli-key lagao"
                     if _UPSTREAM_STATE.get("key_ok") is False else "Key theek lag rahi hai")}


async def asyncio_sleep(sec: float):
    import asyncio
    await asyncio.sleep(sec)


# =====================================================================
# REFERENCE DATA
# =====================================================================
GST_STATE_CODES = {
    "01": "Jammu & Kashmir", "02": "Himachal Pradesh", "03": "Punjab", "04": "Chandigarh",
    "05": "Uttarakhand", "06": "Haryana", "07": "Delhi", "08": "Rajasthan",
    "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim", "12": "Arunachal Pradesh",
    "13": "Nagaland", "14": "Manipur", "15": "Mizoram", "16": "Tripura",
    "17": "Meghalaya", "18": "Assam", "19": "West Bengal", "20": "Jharkhand",
    "21": "Odisha", "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat",
    "25": "Daman & Diu", "26": "Dadra & Nagar Haveli and Daman & Diu", "27": "Maharashtra",
    "28": "Andhra Pradesh (old)", "29": "Karnataka", "30": "Goa", "31": "Lakshadweep",
    "32": "Kerala", "33": "Tamil Nadu", "34": "Puducherry", "35": "Andaman & Nicobar Islands",
    "36": "Telangana", "37": "Andhra Pradesh", "38": "Ladakh", "97": "Other Territory",
    "99": "Centre Jurisdiction",
}

PAN_HOLDER_TYPES = {
    "P": "Individual (Proprietor / Person)",
    "C": "Company",
    "H": "Hindu Undivided Family (HUF)",
    "F": "Firm / Limited Liability Partnership (LLP)",
    "A": "Association of Persons (AOP)",
    "B": "Body of Individuals (BOI)",
    "T": "Trust",
    "L": "Local Authority",
    "J": "Artificial Juridical Person",
    "G": "Government",
    "N": "Non-Resident",
}

RTO_STATES = {
    "AN": "Andaman & Nicobar Islands", "AP": "Andhra Pradesh", "AR": "Arunachal Pradesh",
    "AS": "Assam", "BR": "Bihar", "CG": "Chhattisgarh", "CH": "Chandigarh",
    "DD": "Daman & Diu", "DL": "Delhi", "DN": "Dadra & Nagar Haveli", "GA": "Goa",
    "GJ": "Gujarat", "HP": "Himachal Pradesh", "HR": "Haryana", "JH": "Jharkhand",
    "JK": "Jammu & Kashmir", "KA": "Karnataka", "KL": "Kerala", "LA": "Ladakh",
    "LD": "Lakshadweep", "MH": "Maharashtra", "ML": "Meghalaya", "MN": "Manipur",
    "MP": "Madhya Pradesh", "MZ": "Mizoram", "NL": "Nagaland", "OD": "Odisha",
    "OR": "Odisha (old code)", "PB": "Punjab", "PY": "Puducherry", "RJ": "Rajasthan",
    "SK": "Sikkim", "TN": "Tamil Nadu", "TR": "Tripura", "TS": "Telangana",
    "UA": "Uttarakhand (old)", "UK": "Uttarakhand", "UP": "Uttar Pradesh",
    "WB": "West Bengal",
}

# Partial (but useful) district-level RTO map. Unknown codes still return the state.
RTO_DISTRICTS = {
    "DL01": "Mall Road (North West Delhi)", "DL02": "Shalimar Bagh", "DL03": "Sheikh Sarai (South)",
    "DL04": "Janakpuri (West)", "DL05": "Loni Road (North East)", "DL06": "Sarai Kale Khan (Central)",
    "DL07": "Dwarka (South West)", "DL08": "Wazirpur (North West)", "DL09": "Rohini (North West)",
    "DL10": "Surajmal Vihar (East)", "DL11": "Rohini Sector-15", "DL12": "Vasant Vihar (South West)",
    "DL13": "Mayur Vihar (East)",
    "HR01": "Ambala", "HR02": "Jagadhri (Yamunanagar)", "HR03": "Panchkula", "HR04": "Narnaul",
    "HR05": "Kaithal", "HR06": "Karnal", "HR07": "Hisar", "HR08": "Panipat", "HR09": "Rewari",
    "HR10": "Sonipat", "HR11": "Rohtak", "HR12": "Rohtak (City)", "HR13": "Bahadurgarh",
    "HR14": "Jhajjar", "HR16": "Bhiwani", "HR18": "Faridabad", "HR19": "Palwal",
    "HR20": "Fatehabad", "HR21": "Hansi", "HR22": "Sirsa", "HR24": "Kurukshetra",
    "HR26": "Gurugram (Gurgaon)", "HR27": "Narnaul", "HR28": "Mewat (Nuh)", "HR29": "Nuh",
    "HR30": "Faridabad (New)", "HR36": "Rewari", "HR38": "Faridabad (Ballabgarh)",
    "HR40": "Bahadurgarh", "HR46": "Karnal", "HR47": "Panipat", "HR48": "Ambala",
    "HR51": "Gurugram (Sohna)", "HR55": "Kaithal", "HR57": "Sonipat", "HR58": "Hisar",
    "HR61": "Palwal", "HR63": "Sirsa", "HR68": "Jhajjar", "HR72": "Gurugram",
    "HR73": "Charkhi Dadri", "HR76": "Panchkula", "HR77": "Yamunanagar", "HR80": "Bhiwani",
    "HR81": "Fatehabad", "HR85": "Jind", "HR86": "Jind", "HR87": "Kurukshetra",
    "HR88": "Mahendragarh", "HR90": "Nuh", "HR91": "Rohtak", "HR93": "Kaithal",
    "HR96": "Karnal", "HR97": "Ambala", "HR98": "Panipat",
    "MH01": "Mumbai Central (Tardeo)", "MH02": "Mumbai West (Andheri)", "MH03": "Mumbai East (Wadala)",
    "MH04": "Thane", "MH05": "Kalyan", "MH06": "Pen (Raigad)", "MH07": "Sindhudurg",
    "MH08": "Ratnagiri", "MH09": "Kolhapur", "MH10": "Sangli", "MH11": "Satara",
    "MH12": "Pune", "MH13": "Solapur", "MH14": "Pimpri-Chinchwad", "MH15": "Nashik",
    "MH16": "Ahmednagar", "MH17": "Jalgaon", "MH18": "Dhule", "MH19": "Jalna",
    "MH20": "Aurangabad (Chh. Sambhajinagar)", "MH21": "Beed", "MH22": "Latur",
    "MH23": "Nanded", "MH24": "Akola", "MH25": "Amravati", "MH26": "Buldhana",
    "MH27": "Amravati (City)", "MH28": "Yavatmal", "MH29": "Washim", "MH30": "Akola (City)",
    "MH31": "Nagpur City", "MH32": "Wardha", "MH33": "Chandrapur", "MH34": "Chandrapur (City)",
    "MH35": "Gondia", "MH36": "Bhandara", "MH37": "Gadchiroli", "MH38": "Nagpur Gramin",
    "MH39": "Nandurbar", "MH40": "Nagpur (East)", "MH41": "Malegaon", "MH42": "Baramati",
    "MH43": "Navi Mumbai (Vashi)", "MH44": "Navi Mumbai (Panvel)", "MH45": "Pune (Baramati)",
    "MH46": "Panvel (Raigad)", "MH47": "Mumbai (Bandra)", "MH48": "Navi Mumbai",
    "MH49": "Nagpur (West)", "MH50": "Nashik (Malegaon)", "MH51": "Nashik (Nashik Road)",
    "MH52": "Pune (Pimpri)", "MH53": "Aurangabad (City)", "MH54": "Solapur (City)",
    "MH55": "Kolhapur (City)", "MH56": "Sangli (City)", "MH57": "Satara (City)",
    "UP01": "Lucknow", "UP02": "Barabanki", "UP03": "Lakhimpur Kheri", "UP04": "Hardoi",
    "UP05": "Sitapur", "UP06": "Unnao", "UP07": "Rae Bareli", "UP08": "Pratapgarh",
    "UP09": "Sultanpur", "UP10": "Faizabad", "UP11": "Bahraich", "UP12": "Gonda",
    "UP13": "Basti", "UP14": "Gorakhpur", "UP15": "Deoria", "UP16": "Azamgarh",
    "UP17": "Jaunpur", "UP18": "Allahabad (Prayagraj)", "UP19": "Kanpur", "UP20": "Fatehpur",
    "UP21": "Banda", "UP22": "Hamirpur", "UP23": "Jhansi", "UP24": "Jalaun",
    "UP25": "Etawah", "UP26": "Kannauj", "UP27": "Farrukhabad", "UP28": "Mainpuri",
    "UP29": "Etah", "UP30": "Aligarh", "UP31": "Agra", "UP32": "Mathura",
    "UP33": "Aligarh (Khair)", "UP34": "Hathras", "UP35": "Badaun", "UP36": "Bareilly",
    "UP37": "Pilibhit", "UP38": "Shahjahanpur", "UP39": "Rampur", "UP40": "Moradabad",
    "UP41": "Jhansi (City)", "UP42": "Lalitpur", "UP43": "Meerut", "UP44": "Baghpat",
    "UP45": "Muzaffarnagar", "UP46": "Saharanpur", "UP47": "Dehradun? (see UK)",
    "UP50": "Bulandshahr", "UP51": "Ghaziabad", "UP52": "Meerut (City)", "UP53": "Gautam Buddh Nagar (Noida)",
    "UP54": "Aligarh (City)", "UP55": "Bareilly (City)", "UP56": "Kanpur (City)",
    "UP57": "Varanasi", "UP58": "Ghazipur", "UP59": "Ballia", "UP60": "Mirzapur",
    "UP61": "Sonbhadra", "UP62": "Bhadohi", "UP63": "Chandauli", "UP64": "Varanasi (City)",
    "UP65": "Azamgarh (City)", "UP66": "Gorakhpur (City)", "UP67": "Basti (City)",
    "UP70": "Ghaziabad (City)", "UP71": "Kanpur Dehat", "UP72": "Ambedkar Nagar",
    "UP73": "Auraiya", "UP74": "Amroha", "UP75": "Baghpat (City)", "UP76": "Kaushambi",
    "UP77": "Kannauj (City)", "UP78": "Kasganj", "UP79": "Mahoba", "UP80": "Moradabad (City)",
    "UP81": "Muzaffarnagar (City)", "UP82": "Rampur (City)", "UP83": "Sambhal",
    "UP84": "Sant Kabir Nagar", "UP85": "Shamli", "UP86": "Shravasti", "UP87": "Siddharthnagar",
    "UP90": "Prayagraj (City)", "UP91": "Agra (City)", "UP92": "Aligarh (New)",
    "UP93": "Ghaziabad (New)", "UP94": "Lucknow (City)", "UP95": "Kanpur Nagar",
    "BR01": "Patna", "BR02": "Gaya", "BR03": "Bhagalpur", "BR04": "Muzaffarpur",
    "BR05": "Champaran (East)", "BR06": "Darbhanga", "BR07": "Munger", "BR08": "Purnia",
    "BR09": "Saharsa", "BR10": "Saran (Chhapra)", "BR11": "Bhojpur (Arrah)", "BR12": "Rohtas (Sasaram)",
    "BR13": "Nalanda (Biharsharif)", "BR14": "Aurangabad", "BR15": "Nawada", "BR16": "Jehanabad",
    "BR17": "Gopalganj", "BR18": "Siwan", "BR19": "Sitamarhi", "BR20": "Vaishali (Hajipur)",
    "BR21": "Samastipur", "BR22": "Begusarai", "BR23": "Khagaria", "BR24": "Madhubani",
    "BR25": "Madhepura", "BR26": "Supaul", "BR27": "Araria", "BR28": "Kishanganj",
    "BR29": "Katihar", "BR30": "Sitamarhi", "BR31": "Sheohar", "BR32": "Sheikhpura",
    "BR33": "Lakhisarai", "BR34": "Jamui", "BR35": "Buxar", "BR36": "Bhabua (Kaimur)",
    "BR37": "Arwal", "BR38": "Patna (City)", "BR39": "Muzaffarpur (City)",
    "BR40": "Gaya (City)", "BR41": "Bhagalpur (City)", "BR42": "Darbhanga (City)",
    "BR43": "Purnia (City)", "BR44": "Saharsa (City)", "BR45": "Chhapra (City)",
    "BR46": "Begusarai (City)", "BR50": "Patna (New)",
    "RJ01": "Jaipur", "RJ02": "Jaipur (City)", "RJ03": "Jodhpur", "RJ04": "Bikaner",
    "RJ05": "Udaipur", "RJ06": "Kota", "RJ07": "Ajmer", "RJ08": "Bharatpur",
    "RJ09": "Alwar", "RJ10": "Sikar", "RJ11": "Bhilwara", "RJ12": "Pali",
    "RJ13": "Sri Ganganagar", "RJ14": "Jaipur (South)", "RJ15": "Sawai Madhopur",
    "RJ16": "Hanumangarh", "RJ17": "Churu", "RJ18": "Jhunjhunu", "RJ19": "Tonk",
    "RJ20": "Jhalawar", "RJ21": "Baran", "RJ22": "Dausa", "RJ23": "Nagaur",
    "RJ24": "Dungarpur", "RJ25": "Banswara", "RJ26": "Chittorgarh", "RJ27": "Jaisalmer",
    "RJ28": "Barmer", "RJ29": "Jodhpur (City)", "RJ30": "Sirohi", "RJ31": "Karauli",
    "RJ32": "Dholpur", "RJ33": "Pratapgarh", "RJ34": "Rajsamand", "RJ35": "Bundi",
    "RJ36": "Kota (City)", "RJ37": "Ajmer (City)", "RJ38": "Alwar (City)",
    "RJ39": "Bikaner (City)", "RJ40": "Udaipur (City)", "RJ41": "Jhalawar (City)",
    "RJ42": "Bharatpur (City)", "RJ43": "Sikar (City)", "RJ44": "Jaipur (North)",
    "RJ45": "Jaipur (Transport)", "RJ46": "Pali (City)", "RJ47": "Beawar",
    "GJ01": "Ahmedabad", "GJ02": "Mehsana", "GJ03": "Rajkot", "GJ04": "Bhavnagar",
    "GJ05": "Surat", "GJ06": "Vadodara", "GJ07": "Nadiad (Kheda)", "GJ08": "Palanpur (Banaskantha)",
    "GJ09": "Himmatnagar (Sabarkantha)", "GJ10": "Bhuj (Kutch)", "GJ11": "Bharuch",
    "GJ12": "Godhra (Panchmahal)", "GJ13": "Valsad", "GJ14": "Gandhinagar",
    "GJ15": "Navsari", "GJ16": "Anand", "GJ17": "Dahod", "GJ18": "Gandhidham (Kutch East)",
    "GJ19": "Narmada (Rajpipla)", "GJ20": "Patan", "GJ21": "Junagadh", "GJ22": "Jamnagar",
    "GJ23": "Porbandar", "GJ24": "Amreli", "GJ25": "Surendranagar", "GJ26": "Morbi",
    "GJ27": "Ahmedabad (City)", "GJ28": "Surat (City)", "GJ29": "Vadodara (City)",
    "GJ30": "Botad", "GJ31": "Aravalli (Modasa)", "GJ32": "Gir Somnath", "GJ33": "Devbhumi Dwarka",
    "GJ34": "Mahisagar", "GJ35": "Chhota Udaipur", "GJ36": "Tapi (Vyara)", "GJ37": "Dang",
    "GJ38": "Ahmedabad (West)", "GJ39": "Rajkot (City)",
    "KA01": "Bengaluru Central", "KA02": "Bengaluru West", "KA03": "Bengaluru East",
    "KA04": "Bengaluru North", "KA05": "Bengaluru South", "KA06": "Tumakuru",
    "KA07": "Kolar", "KA08": "Mandya", "KA09": "Mysuru", "KA10": "Hassan",
    "KA11": "Chikkamagaluru", "KA12": "Shivamogga", "KA13": "Chitradurga",
    "KA14": "Davanagere", "KA15": "Udupi", "KA16": "Chikballapur", "KA17": "Dakshina Kannada",
    "KA18": "Kodagu", "KA19": "Belagavi", "KA20": "Bagalkot", "KA21": "Vijayapura",
    "KA22": "Dharwad", "KA23": "Gadag", "KA24": "Haveri", "KA25": "Uttara Kannada",
    "KA26": "Ballari", "KA27": "Vijayanagara", "KA28": "Kalaburagi", "KA29": "Bidar",
    "KA30": "Yadgir", "KA31": "Koppal", "KA32": "Raichur", "KA33": "Ramanagara",
    "KA34": "Chamarajanagar", "KA35": "Bengaluru (K.R. Puram)", "KA36": "Bengaluru (Yelahanka)",
    "KA37": "Bengaluru (Electronic City)", "KA38": "Bengaluru (Jayanagar)", "KA39": "Bengaluru (Yeshwanthpur)",
    "KA40": "Bengaluru (Kengeri)", "KA41": "Bengaluru (Rajajinagar)", "KA42": "Bengaluru (Hosakerehalli)",
    "KA43": "Bengaluru (Nelamangala)", "KA50": "Bengaluru (Kasturinagar)",
    "KA51": "Bengaluru (Jnanabharathi)", "KA52": "Bengaluru (BTM Layout)", "KA53": "Bengaluru (Koramangala)",
    "KA57": "Bengaluru (Devanahalli)", "KA59": "Bengaluru (Chandra Layout)",
    "TN01": "Chennai (North)", "TN02": "Chennai (North West)", "TN03": "Chennai (North East)",
    "TN04": "Chennai (East)", "TN05": "Chennai (South East)", "TN06": "Chennai (South West)",
    "TN07": "Chennai (South)", "TN09": "Chennai (West)", "TN10": "Chennai (Central)",
    "TN11": "Tiruvallur", "TN12": "Kanchipuram", "TN13": "Chengalpattu",
    "TN14": "Vellore", "TN15": "Tiruvannamalai", "TN16": "Villupuram", "TN17": "Cuddalore",
    "TN18": "Thanjavur", "TN19": "Tiruchirappalli", "TN20": "Tiruvarur", "TN21": "Nagapattinam",
    "TN22": "Kanyakumari", "TN23": "Tirunelveli", "TN24": "Thoothukudi", "TN25": "Virudhunagar",
    "TN26": "Ramanathapuram", "TN27": "Madurai (North)", "TN28": "Dindigul",
    "TN29": "Theni", "TN30": "Salem", "TN31": "Namakkal", "TN32": "Erode",
    "TN33": "Coimbatore (South)", "TN34": "Nilgiris", "TN35": "Karur", "TN36": "Perambalur",
    "TN37": "Pudukkottai", "TN38": "Coimbatore (North)", "TN39": "Dharmapuri",
    "TN40": "Krishnagiri", "TN41": "Coimbatore (Central)", "TN42": "Coimbatore (West)",
    "TN45": "Tiruppur", "TN46": "Tiruppur (North)", "TN47": "Ariyalur",
    "TN49": "Madurai (South)", "TN50": "Tiruvallur (North)", "TN51": "Kanchipuram (South)",
    "TN52": "Chennai (Adyar)", "TN55": "Sivaganga", "TN57": "Dindigul (East)",
    "TN58": "Madurai (Central)", "TN59": "Thanjavur (North)", "TN60": "Villupuram (North)",
    "TN61": "Salem (North)", "TN63": "Virudhunagar (South)", "TN64": "Madurai (West)",
    "TN65": "Erode (East)", "TN66": "Coimbatore (East)", "TN67": "Ranipet",
    "TN68": "Tenkasi", "TN69": "Tirupathur", "TN70": "Namakkal (North)",
    "TN72": "Tiruchirappalli (West)", "TN73": "Kallakurichi", "TN74": "Chengalpattu (South)",
    "TN76": "Chennai (North West New)", "TN77": "Chennai (South East New)",
    "TN81": "Tiruchirappalli (South)", "TN82": "Vellore (North)", "TN83": "Tirunelveli (North)",
    "TN85": "Chennai (Tondiarpet)", "TN86": "Chennai (Ambattur)", "TN87": "Chennai (Tambaram)",
    "TN88": "Chennai (Sholinganallur)", "TN90": "Chennai (Anna Nagar)", "TN91": "Chennai (Guindy)",
    "TN92": "Chennai (Adyar New)", "TN94": "Salem (South)", "TN95": "Coimbatore (South New)",
    "TN96": "Erode (West)", "TN97": "Tiruppur (South)", "TN99": "Coimbatore (North New)",
    "TS01": "Hyderabad (Central)", "TS02": "Hyderabad (West)", "TS03": "Hyderabad (East)",
    "TS04": "Hyderabad (North)", "TS05": "Ranga Reddy", "TS06": "Sangareddy",
    "TS07": "Medak", "TS08": "Nizamabad", "TS09": "Adilabad", "TS10": "Nirmal",
    "TS11": "Karimnagar", "TS12": "Jagtial", "TS13": "Khammam", "TS14": "Bhadradri Kothagudem",
    "TS15": "Mahabubabad", "TS16": "Warangal", "TS17": "Jangaon", "TS18": "Mulugu",
    "TS19": "Nalgonda", "TS20": "Suryapet", "TS21": "Yadadri Bhuvanagiri",
    "TS22": "Mahabubnagar", "TS23": "Nagarkurnool", "TS24": "Wanaparthy",
    "TS25": "Jogulamba Gadwal", "TS26": "Vikarabad", "TS27": "Kamareddy",
    "TS28": "Rajanna Sircilla", "TS29": "Peddapalli", "TS30": "Mancherial",
    "TS31": "Jayashankar Bhupalpally", "TS32": "Hanamkonda", "TS33": "Hyderabad (South)",
    "TS34": "Narayanpet", "TS35": "Jagtial (New)", "TS36": "Medchal",
    "AP01": "Visakhapatnam", "AP02": "Vizianagaram", "AP03": "Srikakulam", "AP04": "East Godavari",
    "AP05": "West Godavari", "AP06": "Krishna", "AP07": "Guntur", "AP08": "Prakasam",
    "AP09": "Nellore", "AP10": "Chittoor", "AP11": "Kadapa (YSR)", "AP12": "Anantapur",
    "AP13": "Kurnool", "AP14": "Tirupati", "AP15": "Annamayya", "AP16": "Nandyal",
    "AP17": "Sri Sathya Sai", "AP18": "Konaseema", "AP19": "Bapatla", "AP20": "Palnadu",
    "AP21": "Kakinada", "AP22": "Eluru", "AP23": "NTR (Vijayawada)", "AP24": "Guntur (New)",
    "AP25": "Alluri Sitharama Raju", "AP26": "Parvathipuram Manyam", "AP27": "Anakapalli",
    "AP28": "Sri Potti Sriramulu Nellore", "AP29": "Visakhapatnam (New)", "AP30": "Anantapur (New)",
    "AP31": "Vizianagaram (New)", "AP32": "Chittoor (New)",
    "KL01": "Thiruvananthapuram", "KL02": "Kollam", "KL03": "Pathanamthitta", "KL04": "Alappuzha",
    "KL05": "Kottayam", "KL06": "Idukki", "KL07": "Ernakulam", "KL08": "Thrissur",
    "KL09": "Palakkad", "KL10": "Malappuram", "KL11": "Kozhikode", "KL12": "Wayanad",
    "KL13": "Kannur", "KL14": "Kasargode", "KL15": "Ernakulam (Aluva)", "KL16": "Thiruvananthapuram (Attingal)",
    "KL17": "Kollam (Kottarakkara)", "KL18": "Malappuram (Perinthalmanna)", "KL19": "Kannur (Thalassery)",
    "KL20": "Kozhikode (Vadakara)", "KL21": "Thrissur (Kodungallur)", "KL22": "Kottayam (Pala)",
    "KL23": "Alappuzha (Chengannur)", "KL24": "Ernakulam (Muvattupuzha)", "KL25": "Idukki (Devikulam)",
    "KL26": "Palakkad (Chittur)", "KL27": "Kozhikode (Koyilandy)", "KL28": "Kasaragod (Nileshwaram)",
    "KL29": "Thiruvananthapuram (Neyyattinkara)", "KL30": "Ernakulam (Perumbavoor)",
    "KL31": "Kannur (Payyanur)", "KL32": "Malappuram (Nilambur)", "KL33": "Kollam (Punalur)",
    "KL34": "Thrissur (Wadakkanchery)", "KL35": "Kottayam (Kanjirappally)",
    "KL36": "Pathanamthitta (Adoor)", "KL37": "Idukki (Thodupuzha)", "KL38": "Kozhikode (Perambra)",
    "KL39": "Thiruvananthapuram (Kattakkada)", "KL40": "Alappuzha (Haripad)",
    "KL41": "Ernakulam (Thripunithura)", "KL42": "Kannur (Iritty)", "KL43": "Palakkad (Mannarkkad)",
    "KL44": "Kasaragod (Vellarikundu)", "KL45": "Kollam (Karunagappally)",
    "KL46": "Thrissur (Chalakudy)", "KL47": "Kottayam (Ettumanoor)", "KL48": "Malappuram (Kondotty)",
    "KL49": "Kozhikode (Koduvally)", "KL50": "Kannur (Mattannur)",
    "MP01": "Bhopal", "MP02": "Indore", "MP03": "Gwalior", "MP04": "Jabalpur",
    "MP05": "Rewa", "MP06": "Ujjain", "MP07": "Sagar", "MP08": "Satna",
    "MP09": "Ratlam", "MP10": "Dhar", "MP11": "Khandwa", "MP12": "Khargone",
    "MP13": "Chhatarpur", "MP14": "Hoshangabad", "MP15": "Mandsaur", "MP16": "Dewas",
    "MP17": "Shivpuri", "MP18": "Vidisha", "MP19": "Damoh", "MP20": "Seoni",
    "MP21": "Balaghat", "MP22": "Chhindwara", "MP23": "Betul", "MP24": "Narsinghpur",
    "MP25": "Sehore", "MP26": "Morena", "MP27": "Bhind", "MP28": "Guna",
    "MP29": "Tikamgarh", "MP30": "Shahdol", "MP31": "Umaria", "MP32": "Singrauli",
    "MP33": "Katni", "MP34": "Panna", "MP35": "Sidhi", "MP36": "Neemuch",
    "MP37": "Rajgarh", "MP38": "Shajapur", "MP39": "Ashoknagar", "MP40": "Sheopur",
    "MP41": "Dindori", "MP42": "Anuppur", "MP43": "Harda", "MP44": "Burhanpur",
    "MP45": "Alirajpur", "MP46": "Jhabua", "MP47": "Barwani", "MP48": "Narmadapuram",
    "MP49": "Bhopal (City)", "MP50": "Indore (City)",
    "WB01": "Kolkata (Beltala)", "WB02": "Kolkata (Bhowanipore)", "WB03": "Alipurduar",
    "WB04": "Bankura", "WB05": "Barasat (North 24 Parganas)", "WB06": "Bardhaman",
    "WB07": "Berhampore (Murshidabad)", "WB08": "Balurghat (Dakshin Dinajpur)",
    "WB09": "Bishnupur (Bankura)", "WB10": "Cooch Behar", "WB11": "Darjeeling",
    "WB12": "Diamond Harbour (South 24 Parganas)", "WB13": "Dinhata (Cooch Behar)",
    "WB14": "Howrah", "WB15": "Hooghly (Chinsurah)", "WB16": "Jalpaiguri",
    "WB17": "Jhargram", "WB18": "Kalimpong", "WB19": "Kalyani (Nadia)",
    "WB20": "Krishnanagar (Nadia)", "WB21": "Malda", "WB22": "Medinipur (Midnapore)",
    "WB23": "Paschim Medinipur", "WB24": "Purulia", "WB25": "Raiganj (Uttar Dinajpur)",
    "WB26": "Rampurhat (Birbhum)", "WB27": "Purba Bardhaman", "WB28": "Paschim Bardhaman (Asansol)",
    "WB29": "Tamluk (Purba Medinipur)", "WB30": "Contai (Purba Medinipur)",
    "WB31": "Salt Lake (Bidhannagar)", "WB32": "Baruipur (South 24 Parganas)",
    "WB33": "Barrackpore (North 24 Parganas)", "WB34": "Kolkata (Tollygunge)",
    "WB35": "Uttar Dinajpur", "WB36": "Dakshin Dinajpur", "WB37": "Siliguri",
    "WB38": "Alipurduar (New)", "WB39": "Kolkata (Behala)", "WB40": "Kolkata (Kasba)",
    "WB41": "Kolkata (Newtown)", "WB42": "Kolkata (Rabindra Sarobar)",
    "WB43": "Kolkata (Salt Lake New)", "WB44": "Kolkata (Tiljala)",
    "WB45": "Howrah (New)", "WB46": "Hooghly (Serampore)", "WB47": "Arambagh",
    "WB48": "Bishnupur (South)", "WB49": "Egra", "WB50": "Haldia",
    "WB51": "Islampur (Uttar Dinajpur)", "WB52": "Jangipur (Murshidabad)",
    "WB53": "Kalna (Bardhaman)", "WB54": "Kandi (Murshidabad)", "WB55": "Khatra",
    "WB56": "Kolkata (Beliaghata)", "WB57": "Kolkata (Cossipore)", "WB58": "Kolkata (Garden Reach)",
    "WB59": "Mal (Jalpaiguri)", "WB60": "Nabadwip (Nadia)", "WB61": "Old Malda",
    "WB62": "Purulia (New)", "WB63": "Raghunathpur (Purulia)", "WB64": "Suri (Birbhum)",
    "WB65": "Tufanganj (Cooch Behar)", "WB66": "Uluberia (Howrah)", "WB67": "Jhargram (New)",
    "WB68": "Chanchal (Malda)", "WB69": "Bolpur (Birbhum)", "WB70": "Domkal (Murshidabad)",
    "WB71": "Bhangar (South 24 Parganas)", "WB72": "Kolkata (Burrabazar)",
    "WB73": "Kolkata (Park Street)", "WB74": "Kolkata (Ultadanga)", "WB75": "Barasat (New)",
    "WB76": "Madhyamgram", "WB77": "Bidhannagar (New Town)", "WB78": "Kolkata (Hastings)",
    "PB01": "Ludhiana", "PB02": "Amritsar", "PB03": "Jalandhar", "PB04": "Patiala",
    "PB05": "Gurdaspur", "PB06": "Hoshiarpur", "PB07": "Ferozepur", "PB08": "Bathinda",
    "PB09": "Sangrur", "PB10": "Rupnagar", "PB11": "Kapurthala", "PB12": "Faridkot",
    "PB13": "Moga", "PB14": "Muktsar", "PB15": "Nawanshahr", "PB16": "Mansa",
    "PB17": "Barnala", "PB18": "Tarn Taran", "PB19": "Fatehgarh Sahib", "PB20": "Mohali (SAS Nagar)",
    "PB21": "Ludhiana (City)", "PB22": "Amritsar (City)", "PB23": "Jalandhar (City)",
    "PB24": "Patiala (City)", "PB25": "Bathinda (City)", "PB26": "Sangrur (City)",
    "PB27": "Malerkotla", "PB28": "Pathankot", "PB29": "Phagwara", "PB30": "Khanna",
    "PB31": "Rajpura", "PB32": "Fazilka", "PB33": "Jagraon", "PB34": "Nakodar",
    "PB35": "Samrala", "PB36": "Zira", "PB37": "Sunam", "PB38": "Batala",
    "PB39": "Abohar", "PB40": "Ludhiana (West)", "PB41": "Amritsar (North)",
    "PB42": "Jalandhar (North)", "PB43": "Patiala (Rajpura)", "PB44": "Bathinda (Rampura)",
    "PB45": "Moga (Baghapurana)", "PB46": "Ferozepur (City)", "PB47": "Tarn Taran (Patti)",
    "PB48": "Hoshiarpur (Dasuya)", "PB49": "Gurdaspur (Dera Baba Nanak)", "PB50": "Mohali (Kharar)",
    "RJ14A": "Jaipur South", "OD01": "Bhubaneswar", "OD02": "Cuttack", "OD03": "Puri",
    "OD04": "Sambalpur", "OD05": "Berhampur (Ganjam)", "OD06": "Balasore", "OD07": "Bhadrak",
    "OD08": "Jajpur", "OD09": "Rourkela (Sundargarh)", "OD10": "Bolangir", "OD11": "Baripada (Mayurbhanj)",
    "OD12": "Angul", "OD13": "Koraput", "OD14": "Rayagada", "OD15": "Nabarangpur",
    "OD16": "Kalahandi", "OD17": "Phulbani (Kandhamal)", "OD18": "Dhenkanal", "OD19": "Keonjhar",
    "OD20": "Jharsuguda", "OD21": "Bargarh", "OD22": "Sonepur (Subarnapur)",
    "OD23": "Nayagarh", "OD24": "Jagatsinghpur", "OD25": "Kendrapara", "OD26": "Malkangiri",
    "OD27": "Boudh", "OD28": "Deogarh", "OD29": "Nuapada", "OD30": "Gajapati",
    "OD31": "Kendujhar (New)", "OD32": "Khordha", "OD33": "Cuttack (City)",
    "OD34": "Bhubaneswar (City)", "OD35": "Rourkela (City)", "OD36": "Berhampur (City)",
    "OD37": "Sambalpur (City)", "OD38": "Balasore (City)", "OD39": "Bhadrak (City)",
    "OD40": "Puri (City)", "OD41": "Angul (City)", "OD42": "Jajpur (City)",
    "OD43": "Bargarh (City)", "OD44": "Bolangir (City)", "OD45": "Baripada (City)",
    "OD46": "Dhenkanal (City)", "OD47": "Jharsuguda (City)", "OD48": "Keonjhar (City)",
    "OD49": "Nayagarh (City)", "OD50": "Koraput (City)",
    "JH01": "Ranchi", "JH02": "Jamshedpur (Singhbhum East)", "JH03": "Dhanbad", "JH04": "Bokaro",
    "JH05": "Hazaribagh", "JH06": "Giridih", "JH07": "Deoghar", "JH08": "Dumka",
    "JH09": "Chaibasa (Singhbhum West)", "JH10": "Godda", "JH11": "Gumla", "JH12": "Lohardaga",
    "JH13": "Pakur", "JH14": "Palamu (Daltonganj)", "JH15": "Chatra", "JH16": "Koderma",
    "JH17": "Garhwa", "JH18": "Latehar", "JH19": "Sahibganj", "JH20": "Simdega",
    "JH21": "Khunti", "JH22": "Ramgarh", "JH23": "Saraikela", "JH24": "Jamtara",
    "JH25": "Ranchi (City)", "JH26": "Jamshedpur (City)", "JH27": "Dhanbad (City)",
    "JH28": "Bokaro (City)", "JH29": "Hazaribagh (City)", "JH30": "Deoghar (City)",
    "UK01": "Dehradun", "UK02": "Haridwar", "UK03": "Rishikesh (Dehradun)", "UK04": "Nainital",
    "UK05": "Almora", "UK06": "Pithoragarh", "UK07": "Roorkee (Haridwar)",
    "UK08": "Haldwani (Nainital)", "UK09": "Kashipur (Udham Singh Nagar)",
    "UK10": "Rudrapur (Udham Singh Nagar)", "UK11": "Tehri Garhwal", "UK12": "Pauri Garhwal",
    "UK13": "Chamoli", "UK14": "Uttarkashi", "UK15": "Rudraprayag", "UK16": "Bageshwar",
    "UK17": "Champawat", "UK18": "Srinagar Garhwal", "UK19": "Uttarkashi (New)",
    "UK20": "Dehradun (City)",
    "CG01": "Raipur", "CG02": "Bilaspur", "CG03": "Durg", "CG04": "Rajnandgaon",
    "CG05": "Jagdalpur (Bastar)", "CG06": "Ambikapur (Surguja)", "CG07": "Raigarh",
    "CG08": "Korba", "CG09": "Kanker", "CG10": "Mahasamund", "CG11": "Dhamtari",
    "CG12": "Janjgir-Champa", "CG13": "Kawardha (Kabirdham)", "CG14": "Koriya (Baikunthpur)",
    "CG15": "Balrampur", "CG16": "Balod", "CG17": "Bemetara", "CG18": "Gariaband",
    "CG19": "Kondagaon", "CG20": "Mungeli", "CG21": "Sukma", "CG22": "Surajpur",
    "CG23": "Narayanpur", "CG24": "Dantewada", "CG25": "Bijapur", "CG26": "Raipur (City)",
    "AS01": "Guwahati (Kamrup Metro)", "AS02": "Dibrugarh", "AS03": "Jorhat", "AS04": "Silchar (Cachar)",
    "AS05": "Tezpur (Sonitpur)", "AS06": "Nagaon", "AS07": "Goalpara", "AS08": "Bongaigaon",
    "AS09": "Dhubri", "AS10": "North Lakhimpur (Lakhimpur)", "AS11": "Karimganj",
    "AS12": "Hailakandi", "AS13": "Sivasagar", "AS14": "Golaghat", "AS15": "Tinsukia",
    "AS16": "Kokrajhar", "AS17": "Nalbari", "AS18": "Barpeta", "AS19": "Mangaldoi (Darrang)",
    "AS20": "Diphu (Karbi Anglong)", "AS21": "Haflong (Dima Hasao)", "AS22": "Bokakhat (Golaghat)",
    "AS23": "Dhekiajuli (Sonitpur)", "AS24": "Rangia (Kamrup)", "AS25": "Morigaon",
    "AS26": "Nagaon (City)", "AS27": "Bongaigaon (City)", "AS28": "Guwahati (Dispur)",
    "AS29": "Jorhat (City)", "AS30": "Dibrugarh (City)", "AS31": "Silchar (City)",
    "AS32": "Tinsukia (City)", "AS33": "Sivasagar (City)",
    "GA01": "Panaji", "GA02": "Margao", "GA03": "Mapusa", "GA04": "Bicholim",
    "GA05": "Ponda", "GA06": "Vasco (Mormugao)", "GA07": "Quepem", "GA08": "Canacona",
    "GA09": "Valpoi (Sanguem)", "GA10": "Pernem", "GA11": "Panaji (City)", "GA12": "Margao (City)",
    "CH01": "Chandigarh", "CH02": "Chandigarh (Sector 43)", "CH03": "Chandigarh (Sector 17)",
    "CH04": "Chandigarh (Industrial Area)",
    "HP01": "Shimla", "HP02": "Mandi", "HP03": "Kangra (Dharamshala)", "HP04": "Kullu",
    "HP05": "Solan", "HP06": "Hamirpur", "HP07": "Una", "HP08": "Bilaspur",
    "HP09": "Chamba", "HP10": "Sirmaur (Nahan)", "HP11": "Kinnaur", "HP12": "Lahaul & Spiti",
    "HP13": "Shimla (Rural)", "HP14": "Kangra (Palampur)", "HP15": "Mandi (Sundernagar)",
    "HP16": "Nahan", "HP17": "Hamirpur (City)", "HP18": "Una (City)", "HP19": "Solan (City)",
    "HP20": "Kullu (City)",
    "JK01": "Srinagar", "JK02": "Jammu", "JK03": "Anantnag", "JK04": "Baramulla",
    "JK05": "Udhampur", "JK06": "Kathua", "JK07": "Rajouri", "JK08": "Poonch",
    "JK09": "Doda", "JK10": "Kupwara", "JK11": "Pulwama", "JK12": "Budgam",
    "JK13": "Bandipora", "JK14": "Ganderbal", "JK15": "Kishtwar", "JK16": "Ramban",
    "JK17": "Reasi", "JK18": "Kulgam", "JK19": "Shopian", "JK20": "Samba",
    "JK21": "Jammu (City)", "JK22": "Srinagar (City)",
    "LA01": "Leh", "LA02": "Kargil",
    "PY01": "Puducherry", "PY02": "Karaikal", "PY03": "Mahé", "PY04": "Yanam",
    "PY05": "Puducherry (City)",
    "TR01": "Agartala (West Tripura)", "TR02": "Udaipur (Gomati)", "TR03": "Dharmanagar (North Tripura)",
    "TR04": "Kailashahar (Unakoti)", "TR05": "Ambassa (Dhalai)", "TR06": "Belonia (South Tripura)",
    "TR07": "Sabroom (South Tripura)", "TR08": "Khowai", "TR09": "Teliamura (Khowai)",
    "TR10": "Bishalgarh (Sepahijala)", "TR11": "Sonamura (Sepahijala)",
    "ML01": "Shillong (East Khasi Hills)", "ML02": "Tura (West Garo Hills)", "ML03": "Jowai (West Jaintia Hills)",
    "ML04": "Nongstoin (West Khasi Hills)", "ML05": "Baghmara (South Garo Hills)",
    "ML06": "Nongpoh (Ri Bhoi)", "ML07": "Williamnagar (East Garo Hills)",
    "ML08": "Khliehriat (East Jaintia Hills)", "ML09": "Ampati (South West Garo Hills)",
    "ML10": "Resubelpara (North Garo Hills)", "ML11": "Mawkyrwat (South West Khasi Hills)",
    "MN01": "Imphal West", "MN02": "Imphal East", "MN03": "Thoubal", "MN04": "Bishnupur",
    "MN05": "Kakching", "MN06": "Churachandpur", "MN07": "Ukhrul", "MN08": "Senapati",
    "MN09": "Chandel", "MN10": "Tamenglong", "MN11": "Jiribam", "MN12": "Noney",
    "MN13": "Pherzawl", "MN14": "Tengnoupal", "MN15": "Kamjong", "MN16": "Noney (New)",
    "NL01": "Kohima", "NL02": "Dimapur", "NL03": "Mokokchung", "NL04": "Tuensang",
    "NL05": "Zunheboto", "NL06": "Wokha", "NL07": "Phek", "NL08": "Mon",
    "NL09": "Kiphire", "NL10": "Longleng", "NL11": "Peren", "NL12": "Noklak",
    "NL13": "Chümoukedima", "NL14": "Niuland", "NL15": "Shamator", "NL16": "Tseminyü",
    "MZ01": "Aizawl", "MZ02": "Lunglei", "MZ03": "Champhai", "MZ04": "Serchhip",
    "MZ05": "Lawngtlai", "MZ06": "Mamit", "MZ07": "Kolasib", "MZ08": "Saiha (Siaha)",
    "MZ09": "Hnahthial", "MZ10": "Khawzawl", "MZ11": "Saitual",
    "SK01": "Gangtok (East Sikkim)", "SK02": "Namchi (South Sikkim)", "SK03": "Gyalshing (West Sikkim)",
    "SK04": "Mangan (North Sikkim)", "SK05": "Rangpo (PakYong)", "SK06": "Soreng",
    "AR01": "Itanagar (Papum Pare)", "AR02": "Naharlagun (Papum Pare)", "AR03": "Pasighat (East Siang)",
    "AR04": "Ziro (Lower Subansiri)", "AR05": "Bomdila (West Kameng)", "AR06": "Tezu (Lohit)",
    "AR07": "Along (West Siang)", "AR08": "Changlang", "AR09": "Khonsa (Tirap)",
    "AR10": "Seppa (East Kameng)", "AR11": "Daporijo (Upper Subansiri)", "AR12": "Aalo (West Siang)",
    "AR13": "Roing (Lower Dibang Valley)", "AR14": "Anini (Dibang Valley)",
    "AR15": "Koloriang (Kurung Kumey)", "AR16": "Yingkiong (Upper Siang)",
    "AR17": "Tawang", "AR18": "Namsai", "AR19": "Longding", "AR20": "Kra Daadi",
    "AN01": "Port Blair (South Andaman)", "AN02": "Mayabunder (North & Middle Andaman)",
    "AN03": "Hutbay (Little Andaman)", "AN04": "Car Nicobar", "AN05": "Campbell Bay (Great Nicobar)",
    "DN01": "Silvassa", "DD01": "Daman", "DD02": "Diu", "LD01": "Kavaratti", "LD02": "Minicoy",
}

# Very small TAC -> device hint table (heuristic only, real data comes from upstream)
TAC_HINTS = {
    "353010": ("APPLE", "iPhone 12 mini"), "35907306": ("APPLE", "iPhone 12"),
    "356946": ("APPLE", "iPhone 6"), "013977": ("APPLE", "iPhone 6S"),
    "35693803": ("SAMSUNG", "Galaxy S series"), "35575405": ("SAMSUNG", "Galaxy J series"),
    "35192805": ("SAMSUNG", "Galaxy S / Note series"), "35725405": ("SAMSUNG", "Galaxy A series"),
    "86983805": ("XIAOMI / REDMI", "Redmi / Mi series"), "86983804": ("REALME", "Realme series"),
    "86662503": ("VIVO", "Vivo Y / V series"), "86574203": ("OPPO", "Oppo A / F series"),
    "35154908": ("NOKIA", "Nokia smartphone"), "35700810": ("NOKIA", "Nokia feature phone"),
    "990013": ("ONEPLUS", "OnePlus series"), "86799905": ("MOTOROLA", "Moto G series"),
    "86875704": ("TECNO", "Tecno Spark / Camon"), "35791309": ("INFINIX", "Infinix Hot series"),
    "86983806": ("POCO", "Poco series"), "35450508": ("LAVA", "Lava / Indian OEM"),
}

REPORTING_BODIES = {
    "01": "PTCRB / CTIA (North America)", "02": "PTCRB / CTIA (North America)",
    "30": "ComReg (Ireland)", "33": "OFCOM / European", "35": "BABT (United Kingdom)",
    "44": "BABT (United Kingdom)", "45": "Nordic (Denmark / Finland / Sweden)",
    "49": "Bundesnetzagentur (Germany)", "50": "Agcom (Italy)", "53": "ANFR (France)",
    "86": "TAF (China)", "91": "MSAI (India)", "99": "GSMA (global / test)",
}

# =====================================================================
# SMALL UTILITIES
# =====================================================================
def luhn_ok(number: str) -> bool:
    digits = [int(d) for d in number if d.isdigit()]
    if not digits:
        return False
    checksum = 0
    parity = len(digits) % 2
    for i, d in enumerate(digits):
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


CP_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def gstin_check_char(gstin: str) -> str:
    factor = 2
    total = 0
    for ch in gstin[:14]:
        code = CP_CHARS.index(ch)
        digit = factor * code
        factor = 1 if factor == 2 else 2
        digit = (digit // 36) + (digit % 36)
        total += digit
    return CP_CHARS[(36 - (total % 36)) % 36]


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


def clean_number(value: str) -> str:
    return re.sub(r"[^0-9]", "", value or "")


def custom_lookup(category: str, key_value: str) -> List[Dict[str, Any]]:
    """Look for records the user stored in the dashboard database."""
    if not key_value:
        return []
    try:
        with db() as conn:
            rows = conn.execute(
                "SELECT * FROM custom_records WHERE category=? AND key_value=? ORDER BY id DESC LIMIT 50",
                (category, key_value.strip().lower())).fetchall()
        out = []
        for r in rows:
            try:
                data = json.loads(r["data"])
            except Exception:
                data = {"raw": r["data"]}
            if isinstance(data, dict):
                data["_db_id"] = r["id"]
                data["_source"] = r["source"]
            out.append(data)
        return out
    except Exception:
        return []


# =====================================================================
# NATIVE DATA SOURCES
# =====================================================================
async def native_ip_v1(params: Dict[str, Any], request: Request) -> Tuple[Optional[Dict], bool]:
    ip = (params.get("query") or params.get("ip") or "").strip() or client_ip(request)
    if not ip:
        return None, True
    data, err = await http_get(f"https://ip-api.com/json/{ip}", timeout=12)
    if not data or data.get("status") != "success":
        data2, _ = await http_get(f"http://ip-api.com/json/{ip}", timeout=12)
        if data2 and data2.get("status") == "success":
            data = data2
    if not data or data.get("status") != "success":
        # last resort: ipwho.is payload
        return await native_ip_v2(params, request)
    return {"query": ip, "ip": ip, "status": data.get("status", "success"),
            "country": data.get("country"), "countryCode": data.get("countryCode"),
            "region": data.get("regionName") or data.get("region"), "city": data.get("city"),
            "zip": data.get("zip"), "lat": data.get("lat"), "lon": data.get("lon"),
            "timezone": data.get("timezone"), "isp": data.get("isp"), "org": data.get("org"),
            "as": data.get("as"), "source": "ip-api.com"}, False


async def native_ip_v2(params: Dict[str, Any], request: Request) -> Tuple[Optional[Dict], bool]:
    ip = (params.get("ip") or params.get("query") or "").strip() or client_ip(request)
    if not ip:
        return None, True
    data, _ = await http_get(f"https://ipwho.is/{ip}", timeout=12)
    if not data or data.get("success") is False:
        return None, True
    conn = data.get("connection", {}) or {}
    return {"ip": ip, "success": True, "type": data.get("type"),
            "continent": data.get("continent"), "country": data.get("country"),
            "country_code": data.get("country_code"), "region": data.get("region"),
            "city": data.get("city"), "latitude": data.get("latitude"),
            "longitude": data.get("longitude"), "postal": data.get("postal"),
            "calling_code": data.get("calling_code"), "capital": data.get("capital"),
            "timezone": (data.get("timezone") or {}).get("id"),
            "isp": conn.get("isp") or data.get("isp"),
            "asn": conn.get("asn") or (conn.get("asn") if conn else None),
            "org": conn.get("org"), "is_proxy": data.get("is_proxy"),
            "source": "ipwho.is"}, False


async def native_ip_v3(params: Dict[str, Any], request: Request) -> Tuple[Optional[Dict], bool]:
    ip = (params.get("ip") or params.get("query") or "").strip() or client_ip(request)
    if not ip:
        return None, True
    data, _ = await http_get(f"https://ipinfo.io/{ip}/json", timeout=12)
    if not data or data.get("error"):
        return None, True
    loc = (data.get("loc") or ",").split(",")
    return {"ip": ip, "hostname": data.get("hostname"), "city": data.get("city"),
            "region": data.get("region"), "country": data.get("country"),
            "loc": data.get("loc"), "latitude": loc[0] if loc else None,
            "longitude": loc[1] if len(loc) > 1 else None,
            "org": data.get("org"), "postal": data.get("postal"),
            "timezone": data.get("timezone"), "anycast": data.get("anycast"),
            "source": "ipinfo.io"}, False


# ---------------------------------------------------------------------
# v2.4 — TAC DATABASE (255k rows) + nanoreview specs engine
# ---------------------------------------------------------------------
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
TAC_CSV_PATH = os.path.join(DATA_DIR, "tac_full.csv")
TAC_DB_PATH = os.path.join(DATA_DIR, "tac_index.db")
_tac_state: Dict[str, Any] = {"ready": False, "err": "", "count": 0, "tried": False}


def _clean_html(t: str) -> str:
    t = re.sub(r"<[^>]+>", " ", str(t or ""))
    t = html_lib.unescape(t)
    return re.sub(r"\s+", " ", t).strip()


def _build_tac_index() -> None:
    """CSV → SQLite (ek baar). 255k rows ~2-4s."""
    if _tac_state["tried"]:
        return
    _tac_state["tried"] = True
    try:
        import csv as _csv
        import sqlite3 as _sq
        if not os.path.exists(TAC_CSV_PATH):
            _tac_state["err"] = "tac_full.csv repo me nahi mila"
            return
        if os.path.exists(TAC_DB_PATH) and os.path.getsize(TAC_DB_PATH) > 1_000_000:
            con = _sq.connect(TAC_DB_PATH)
            n = con.execute("SELECT COUNT(*) FROM tac").fetchone()[0]
            con.close()
            _tac_state.update({"ready": True, "count": int(n)})
            return
        tmp = TAC_DB_PATH + ".tmp"
        if os.path.exists(tmp):
            os.remove(tmp)
        con = _sq.connect(tmp)
        con.execute("CREATE TABLE tac(tac TEXT PRIMARY KEY, brand TEXT, device TEXT, extra TEXT)")
        batch: List[Tuple[str, str, str, str]] = []
        with io.open(TAC_CSV_PATH, encoding="utf-8", errors="ignore") as fh:
            rd = _csv.reader(fh)
            next(rd, None)
            for row in rd:
                if len(row) < 3:
                    continue
                brand = str(row[0]).strip().upper()
                tac = re.sub(r"\D", "", str(row[1]))[:8]
                if len(tac) < 8:
                    continue
                parts = [p.strip() for p in str(row[2]).split(",") if p.strip()]
                device, extra = "", ""
                if parts:
                    if parts[0].upper().replace(" ", "") == brand.replace(" ", ""):
                        device = parts[1] if len(parts) > 1 else parts[0]
                        extra = ", ".join(parts[2:])
                    else:
                        device = parts[0]
                        extra = ", ".join(parts[1:])
                batch.append((tac, brand, device, extra))
                if len(batch) >= 20000:
                    con.executemany("INSERT OR REPLACE INTO tac VALUES(?,?,?,?)", batch)
                    batch.clear()
        if batch:
            con.executemany("INSERT OR REPLACE INTO tac VALUES(?,?,?,?)", batch)
        con.commit()
        n = con.execute("SELECT COUNT(*) FROM tac").fetchone()[0]
        con.close()
        os.replace(tmp, TAC_DB_PATH)
        _tac_state.update({"ready": True, "count": int(n)})
    except Exception as exc:  # noqa: BLE001
        _tac_state["err"] = f"{type(exc).__name__}: {str(exc)[:120]}"


def _tac_lookup(tac: str) -> Optional[Dict[str, Any]]:
    """TAC (8 digits) → {brand, device, extra}. Index ready hone ka chhota wait."""
    if not _tac_state["ready"] and not _tac_state["tried"]:
        _build_tac_index()
    waited = 0.0
    while not _tac_state["ready"] and waited < 6:
        time.sleep(0.25)
        waited += 0.25
        if _tac_state["err"]:
            break
    if not _tac_state["ready"]:
        return None
    try:
        con = sqlite3.connect(TAC_DB_PATH, timeout=5)
        row = con.execute("SELECT brand, device, extra FROM tac WHERE tac=?", (tac,)).fetchone()
        con.close()
        if not row:
            return None
        return {"brand": row[0], "device": row[1], "extra": row[2] or ""}
    except Exception:  # noqa: BLE001
        return None


def _model_codes(device: str, extra: str) -> List[str]:
    codes: List[str] = []
    for m in re.finditer(r"\b([A-Z]{1,4}[-\s]?[A-Z0-9]{2,}(?:[-/][A-Z0-9]+)*\d{2,}[A-Z0-9/]*)\b", f"{device} {extra}"):
        c = m.group(1).strip()
        if len(c) >= 5 and c not in codes:
            codes.append(c)
    return codes[:10]


_NR_CACHE: Dict[str, Any] = {}


def _nr_slug(name: str) -> str:
    t = str(name or "").lower()
    t = t.replace("+", " plus ").replace("(", " ").replace(")", " ")
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    return t


SPECS_SEED_PATH = os.path.join(DATA_DIR, "specs_seed.json")
_SPECS_SEED: Dict[str, Any] = {"loaded": False, "items": {}}
_SEED_STOP = {"the", "5g", "4g", "lte", "ds", "dual", "mobile", "wifi", "cellular"}
_SEED_MODS = {"fe", "mini", "pro", "ultra", "max", "plus", "se", "lite"}   # ye shabd device ki pehchaan hain


def _load_specs_seed() -> Dict[str, Any]:
    """Seed file ek hi baar load karo (popular devices ki ready-made specs)."""
    if _SPECS_SEED["loaded"]:
        return _SPECS_SEED["items"]
    _SPECS_SEED["loaded"] = True
    try:
        with open(SPECS_SEED_PATH, encoding="utf-8") as f:
            items = json.load(f)
        if isinstance(items, dict):
            _SPECS_SEED["items"] = items
    except Exception:  # noqa: BLE001
        pass
    return _SPECS_SEED["items"]


def _seed_tokens(name: str) -> set:
    n = re.sub(r"\+", " plus ", str(name or "").lower())
    return {w for w in re.split(r"[^a-z0-9]+", n) if w and w not in _SEED_STOP}


def _seed_lookup(model: str, brand: str = "") -> Optional[Dict[str, Any]]:
    """Seed me device dhoondo: exact slug → brand+slug → token match (safe)."""
    items = _load_specs_seed()
    if not items:
        return None
    for cand in (model, f"{brand} {model}" if brand else "", f"{model} {brand}" if brand else ""):
        c = re.sub(r"\s+", " ", str(cand or "")).strip()
        if len(c) < 3:
            continue
        hit = items.get(_nr_slug(c)[:90])
        if isinstance(hit, dict) and hit.get("sections"):
            return hit
    want = _seed_tokens(model)
    if not want:
        return None
    # v2.5.9: smart match — TAC naam ("APPLE IPHONE 12 MINI") bhi kaam kare.
    # Rules: modifier tokens (fe/pro/ultra/mini/plus/max) BOTH taraf barabar hone chahiye,
    # number token (12, a54, s23) match zaroori, aur kam se kam 2 token common.
    want_mods = {t for t in want if t in _SEED_MODS}
    want_nums = {t for t in want if any(ch.isdigit() for ch in t)}
    min_shared = 1 if len(want) <= 1 else 2
    best: Optional[Dict[str, Any]] = None
    best_score = 0
    for _k, v in items.items():
        if not isinstance(v, dict) or not v.get("sections"):
            continue
        got = _seed_tokens(v.get("requested") or v.get("name") or "")
        got_all = got | _seed_tokens(v.get("name") or "")
        if not got_all:
            continue
        got_mods = {t for t in got_all if t in _SEED_MODS}
        if got_mods != want_mods:
            continue                                  # s22 vs s22 ultra / a9 vs a9+ / pad vs pad se
        if not want.issubset(got_all):
            continue                                  # spark 20 vs camon 20 — series word match zaroori
        if want_nums and not (want_nums & got_all):
            continue                                  # 12 mini vs 13 mini safe
        shared = len(want & got_all)
        if shared < min_shared:
            continue
        score = shared * 10 - len(got_all - want)
        if score > best_score:
            best, best_score = v, score
    return best


async def _device_specs_fetch(model: str, brand: str = "") -> Optional[Dict[str, Any]]:
    """nanoreview.net se device page → photo + spec sections (tables)."""
    model = re.sub(r"\s+", " ", str(model or "")).strip()
    if len(model) < 3:
        return None
    key = _nr_slug(model)[:90]
    hit = _NR_CACHE.get(key)
    if hit and (time.time() - hit[0]) < 86400:
        return hit[1]

    # 0) SEED — popular devices ki ready-made specs (cloud IP block se farq nahi padta)
    seeded = _seed_lookup(model, brand)
    if seeded:
        out_seed = dict(seeded)
        out_seed.setdefault("source", "nanoreview.net (seed)")
        if out_seed.get("sections"):
            out_seed["success"] = True
            _NR_CACHE[key] = (time.time(), out_seed)
            return out_seed

    import httpx
    slug = _nr_slug(model)
    if brand and not slug.startswith(_nr_slug(brand)):
        slug = _nr_slug(f"{brand} {model}")
    urls = [f"https://nanoreview.net/en/{kind}/{slug}" for kind in ("tablet", "phone")]
    html = ""
    used_url = ""
    headers = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9", "Accept": "text/html,*/*"}
    _PROXIES = (
        ("https://api.allorigins.win/raw?url=", "raw"),
        ("https://api.allorigins.win/get?url=", "get"),
        ("https://api.codetabs.com/v1/proxy?quest=", "quest"),
    )
    proxied = False

    def _unwrap(txt: str) -> str:
        """allorigins /get → {"contents": "<html>…"} ko kholo."""
        t = txt.lstrip()
        if t.startswith("{"):
            try:
                return str(json.loads(t).get("contents") or "")
            except Exception:  # noqa: BLE001
                return ""
        return txt

    async def _fetch_one(client, url: str) -> str:
        try:
            r = await client.get(url)
            if r.status_code != 200:
                return ""
            t = _unwrap(r.text)
            return t if ("specs-table" in t) else ""
        except Exception:  # noqa: BLE001
            return ""

    async def _wayback(client, target: str) -> str:
        """archive.org ka raw snapshot (id_) — datacenter IP par bhi chalta hai."""
        try:
            av = await client.get("http://archive.org/wayback/available",
                                  params={"url": target.replace("https://", "")})
            snap = ((av.json().get("archived_snapshots") or {}).get("closest") or {})
            ts = snap.get("timestamp")
            if not ts:
                return ""
            r = await client.get(f"https://web.archive.org/web/{ts}id_/{target}")
            if r.status_code == 200 and "specs-table" in r.text:
                return r.text
        except Exception:  # noqa: BLE001
            return ""
        return ""

    async def _fetch_best(client, budget: float = 20.0):
        """Saare routes PARALLEL — jo pehle mile (best priority) wahi use karo. Cloud block ka ilaaj."""
        nonlocal proxied
        tasks: Dict[Any, Any] = {}
        for u in urls:
            q = urllib.parse.quote(u, safe="")
            for pr, cand in ((0, u), (1, _PROXIES[0][0] + q), (2, _PROXIES[2][0] + q),
                             (3, _PROXIES[1][0] + q)):
                tasks[asyncio.create_task(_fetch_one(client, cand))] = (pr, u)
        tasks[asyncio.create_task(_wayback(client, urls[0]))] = (1, urls[0])
        deadline = time.monotonic() + budget
        best = None
        first_hit_at = 0.0
        pending = set(tasks)
        while pending:
            rem = deadline - time.monotonic()
            if rem <= 0:
                break
            done, pending = await asyncio.wait(pending, timeout=min(rem, 3.0),
                                               return_when=asyncio.FIRST_COMPLETED)
            for d in done:
                pr, u = tasks.get(d, (9, ""))
                try:
                    txt = d.result()
                except Exception:  # noqa: BLE001
                    txt = ""
                if not txt:
                    continue
                if pr == 0:
                    for p in pending:
                        p.cancel()
                    return txt, u
                if best is None or pr < best[0]:
                    best = (pr, u, txt)
                first_hit_at = first_hit_at or time.monotonic()
            if best and (time.monotonic() - first_hit_at) > 2.5:
                break          # direct ka 2.5s intezaar khatam — proxy ka jawab le lo
        for p in pending:
            p.cancel()
        if best:
            proxied = True
            return best[2], best[1]
        return "", ""

    async def _ddg_links(client, query: str) -> List[str]:
        """Search se nanoreview URLs: html.ddg / lite.ddg / allorigins — sab parallel."""
        enc = urllib.parse.quote_plus(query)
        sources = [
            ("https://html.duckduckgo.com/html/", {"q": query}),
            ("https://lite.duckduckgo.com/lite/", {"q": query}),
            (f"{_PROXIES[0][0]}{urllib.parse.quote('https://html.duckduckgo.com/html/?q=' + enc, safe='')}", None),
        ]

        async def _one(u, p):
            try:
                r = await client.get(u, params=p) if p is not None else await client.get(u)
                body = _unwrap(r.text)
                if not body:
                    return []
                out = []
                for m in re.finditer(r"uddg=([^&\"]+)", body):
                    cand = urllib.parse.unquote(m.group(1))
                    if "nanoreview.net" in cand and "/en/" in cand:
                        out.append(cand)
                for m in re.finditer(r"https?://nanoreview\.net/en/[A-Za-z0-9%._/-]{5,120}", body):
                    out.append(m.group(0))
                return out
            except Exception:  # noqa: BLE001
                return []

        tset = [asyncio.create_task(_one(u, p)) for u, p in sources]
        try:
            done, pend = await asyncio.wait(tset, timeout=9.0, return_when=asyncio.FIRST_COMPLETED)
            for d in done:
                try:
                    res = d.result()
                except Exception:  # noqa: BLE001
                    res = []
                if res:
                    for p in pend:
                        p.cancel()
                    return res
        finally:
            for t in tset:
                if not t.done():
                    t.cancel()
        return []

    # v2.5.6: slug variants — real sites par kai models suffix/brand-less slug par hote hain
    brand_slug = _nr_slug(brand) if brand else ""
    variants: List[str] = []
    for base in ([slug, f"{brand_slug}-{slug}"] if brand_slug and not slug.startswith(brand_slug) else [slug]):
        for sfx in ("", "-5g", "-4g", "-lte"):
            v = base + sfx
            if v not in variants:
                variants.append(v)
    if brand_slug and slug.startswith(brand_slug + "-"):
        short = slug[len(brand_slug) + 1:]
        for sfx in ("", "-5g", "-4g"):
            v = short + sfx
            if v not in variants:
                variants.append(v[:90])
    variant_urls = [f"https://nanoreview.net/en/{kind}/{v}" for v in variants[:10]
                    for kind in ("tablet", "phone")]

    def _page_name(h: str) -> str:
        h1 = re.search(r"<h1[^>]*>(.*?)</h1>", h, re.S)
        if h1:
            return _clean_html(h1.group(1))
        ti = re.search(r"<title>(.*?)</title>", h, re.S)
        return _clean_html(ti.group(1)).split(":")[0] if ti else ""

    want_tokens = {w for w in re.split(r"[^a-z0-9]+", re.sub(r"\+", " plus ", model.lower()))
                   if w and w not in _SEED_STOP}
    want_nums = {t for t in want_tokens if any(ch.isdigit() for ch in t)}

    want_mods = {t for t in want_tokens if t in _SEED_MODS}

    def _name_ok(h: str) -> bool:
        """Sahi device hai? Number-token (a54, s23, 13) + modifier (fe, pro, mini…) match zaroori."""
        nm = _page_name(h).lower()
        if not nm:
            return True                      # naam nahi mila → rok nahi lagayenge
        got = {w for w in re.split(r"[^a-z0-9]+", re.sub(r"\+", " plus ", nm)) if w}
        if want_nums and not (want_nums & got):
            return False                     # jaise narzo 60 vs narzo 90 — reject
        if want_mods and not want_mods.issubset(got):
            return False                     # jaise "s23 fe" vs simple "s23" — reject
        return True

    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=True, headers=headers) as client:
            html, used_url = await _fetch_best(client, budget=14.0)
            if html and not _name_ok(html):
                html, used_url = "", ""      # galat device — variants/DDG try karo
            if not html and variant_urls:
                # variant pass — parallel; deadline tak har result check karo (v2.5.7 bug fix:
                # pehle FIRST_COMPLETED ek hi baar chalta tha aur baaki saare tasks cancel ho jate the)
                vtasks = {asyncio.create_task(_fetch_one(client, vu)): vu for vu in variant_urls}
                try:
                    vdeadline = time.monotonic() + 12.0
                    vpending = set(vtasks)
                    while vpending and time.monotonic() < vdeadline:
                        vdone, vpending = await asyncio.wait(
                            vpending, timeout=min(3.0, max(0.5, vdeadline - time.monotonic())),
                            return_when=asyncio.FIRST_COMPLETED)
                        for d in vdone:
                            vu = vtasks.get(d, "")
                            try:
                                h2 = d.result()
                            except Exception:  # noqa: BLE001
                                h2 = ""
                            if not h2:
                                continue
                            got_tokens = {w for w in re.split(
                                r"[^a-z0-9]+", re.sub(r"\+", " plus ", _page_name(h2).lower())) if w}
                            if want_tokens and not want_tokens.issubset(got_tokens):
                                continue          # galat device — chhodo
                            html, used_url = h2, vu
                            proxied = True
                            break
                        if html:
                            break
                finally:
                    for t in vtasks:
                        if not t.done():
                            t.cancel()
            if not html:
                for cand in (await _ddg_links(client, f"site:nanoreview.net {model}"))[:4]:
                    h2 = await _fetch_one(client, cand) or await _wayback(client, cand)
                    if h2 and _name_ok(h2):
                        html, used_url = h2, cand
                        proxied = True
                        break
    except Exception:  # noqa: BLE001
        return None
    if not html:
        return None

    # v2.4b: tables ko unke HEADING (h3) ke hisaab se group karo — "Display", "Design and build",
    # "Performance", "Memory", "Battery", "Main camera"… (pehle sab "Specs"/caption the)
    SKIP_HEADS = {"review", "full specifications", "competitors", "comments", "recent user tests",
                  "benchmarks", "benchmark"}

    heads = [(m.start(), _clean_html(m.group(2)))
             for m in re.finditer(r'<h([1-4])[^>]*>(.*?)</h\1>', html, re.S)]
    heads = [(p, t) for p, t in heads if t and t.lower().strip("()0123456789 ") not in SKIP_HEADS
             and not t.lower().startswith(("comment", "recent user", "benchmark"))]

    sections: List[Dict[str, Any]] = []
    by_title: Dict[str, Dict[str, Any]] = {}

    def _sec(title: str) -> Dict[str, Any]:
        title = (title or "Specs")[:48]
        if title not in by_title:
            sec = {"title": title, "rows": []}
            by_title[title] = sec
            sections.append(sec)
        return by_title[title]

    for tm in re.finditer(r'<table class="specs-table">(.*?)</table>', html, re.S):
        pos, tb = tm.start(), tm.group(1)
        title = ""
        for hp, ht in heads:
            if hp < pos:
                title = ht
            else:
                break
        rows = []
        for k, v in re.findall(r'<td class="cell-h">(.*?)</td>\s*<td class="cell-s">(.*?)</td>', tb, re.S):
            kk, vv = _clean_html(k), _clean_html(v)
            if kk and vv and len(vv) < 300:
                rows.append([kk, vv])
        if not rows:
            continue
        sec = _sec(title or "Specs")
        for r in rows:
            if len(sec["rows"]) < 22 and r not in sec["rows"]:
                sec["rows"].append(r)
    sections = [x for x in sections if x["rows"]]
    name = ""
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
    if h1:
        name = _clean_html(h1.group(1))
    if not name:
        ti = re.search(r"<title>(.*?)</title>", html, re.S)
        name = _clean_html(ti.group(1)).split(":")[0] if ti else model
    img = re.search(r'src="(/common/images/(?:tablet|phone)/[^"]+?@2x\.jpeg)"', html)
    image = ("https://nanoreview.net" + img.group(1)) if img else ""
    out = {
        "success": bool(sections),
        "name": name[:90],
        "url": used_url,
        "image": image,
        "sections": sections,
        "row_count": sum(len(x["rows"]) for x in sections),
        "source": "nanoreview.net" + (" (proxy)" if proxied else ""),
    }
    if out["success"]:
        _NR_CACHE[key] = (time.time(), out)
        if len(_NR_CACHE) > 400:
            for k in sorted(_NR_CACHE, key=lambda x: _NR_CACHE[x][0])[:150]:
                _NR_CACHE.pop(k, None)
    return out if out["success"] else None


_WIKI_CACHE: Dict[str, Dict[str, Any]] = {}


async def _wiki_device_extra(name: str) -> Dict[str, Any]:
    """Wikipedia infobox se model codes (SM-X210…), release year aur HD image. Best-effort."""
    out: Dict[str, Any] = {}
    q = re.sub(r"[^A-Za-z0-9 +-]+", " ", str(name or "")).strip()
    key = q.lower()
    if len(q) < 4:
        return out
    if key in _WIKI_CACHE:
        return dict(_WIKI_CACHE[key])
    import httpx
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True,
                                     headers={"User-Agent": UA}) as client:
            r = await client.get("https://en.wikipedia.org/w/api.php",
                                 params={"action": "query", "prop": "revisions", "rvprop": "content",
                                         "rvslots": "main", "format": "json", "redirects": "1",
                                         "titles": q})
            if r.status_code != 200:
                return out
            pages = ((r.json().get("query") or {}).get("pages") or {})
            content = ""
            for _pid, pg in pages.items():
                try:
                    content = pg["revisions"][0]["slots"]["main"]["*"]
                except Exception:  # noqa: BLE001
                    content = ""
            if not content:
                return out
            # infobox = {{Infobox … }} ka pehla hissa
            ib = ""
            m0 = re.search(r"\{\{\s*Infobox[^\n]*", content, re.I)
            if m0:
                ib = content[m0.start(): m0.start() + 3000]
            scope = ib or content[:3000]

            codes: List[str] = []
            for m in re.finditer(r"\b([A-Z]{1,5}-[A-Z0-9]{3,}(?:[/|-][A-Z0-9]+)*)\b", scope):
                c = m.group(1).strip()
                if any(ch.isdigit() for ch in c) and not re.match(r"^[A-Z]{2,4}-?\d{4}$", c):
                    if c not in codes:
                        codes.append(c)
            for field in ("models", "modelname", "model_name", "modelnumber"):
                m = re.search(r"\|\s*" + field + r"\s*=([^\n|]{4,220})", scope, re.I)
                if m:
                    for c in re.findall(r"[A-Z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)+", m.group(1)):
                        if len(c) >= 5 and any(ch.isdigit() for ch in c) and c not in codes:
                            codes.append(c)
            if codes:
                out["model_codes"] = codes[:10]

            m2 = re.search(r"\|\s*released\s*=\s*([^\n|]{4,200})", content, re.I)
            if m2:
                raw = m2.group(1)
                years = re.findall(r"(?:19|20)\d{2}", re.sub(r"\{\{[^}]*\}\}", " ", raw))
                if years:
                    out["released"] = years[0]

            img = re.search(r"\|\s*image\s*=\s*([^\n|]{3,120})", content, re.I)
            if img:
                fname = img.group(1).strip()
                if fname.lower().startswith("http"):
                    out["image"] = fname
                else:
                    r2 = await client.get("https://en.wikipedia.org/w/api.php",
                                          params={"action": "query", "titles": f"File:{fname}",
                                                  "prop": "imageinfo", "iiprop": "url", "format": "json"})
                    if r2.status_code == 200:
                        for _p, pg in ((r2.json().get("query") or {}).get("pages") or {}).items():
                            try:
                                out["image"] = pg["imageinfo"][0]["url"]
                            except Exception:  # noqa: BLE001
                                pass
    except Exception:  # noqa: BLE001
        return out
    _WIKI_CACHE[key] = dict(out)
    return out


async def native_device_specs(params: Dict[str, Any], request: Request) -> Tuple[Optional[Dict], bool]:
    model = (params.get("model") or params.get("device") or params.get("q") or "").strip()
    if not model:
        return None, True
    out = await _device_specs_fetch(model, (params.get("brand") or "").strip())
    if not out:
        return {"success": False, "model": model,
                "error": "Is device ke specs nahi mile — naam thoda saaf likho (jaise 'Samsung Galaxy Tab A9+')."}, True
    return out, False


async def native_imei(params: Dict[str, Any], request: Request) -> Tuple[Optional[Dict], bool]:
    """TAC-only lookup: device serial/check digit are never needed or returned."""
    raw = clean_number(params.get("tac") or params.get("imei") or params.get("q") or "")
    if len(raw) < 8:
        return {"success": False, "tac": raw,
                "error": "8-digit TAC chahiye (IMEI ke pehle 8 digits)."}, True
    tac = raw[:8]
    body = REPORTING_BODIES.get(tac[:2], "Unknown / not in local table")

    # 1) admin ke apne records (custom DB, category=tac)
    own = custom_lookup("tac", tac)
    brand = device = extra = ""
    source = ""
    if own:
        rec = own[0]
        brand = str(rec.get("brand") or "").upper()
        device = str(rec.get("device") or rec.get("model") or "")
        source = "custom-db"
    # 2) 255k TAC database (v2.4)
    if not device:
        hit = _tac_lookup(tac)
        if hit:
            brand = hit["brand"]
            device = hit["device"]
            extra = hit["extra"]
            source = f"tac-db ({_tac_state.get('count', 0)} rows)"
    # 3) purana chhota local catalog (fallback + readable model casing)
    b2, m2 = TAC_HINTS.get(tac, TAC_HINTS.get(tac[:6], TAC_HINTS.get(tac[:4], (None, None))))
    if not device and (b2 or m2):
        brand, device, source = (b2 or ""), (m2 or ""), "local-catalog"
    known = bool(device or brand)
    device = device or ""
    model_short = device
    if brand and device.upper().startswith(brand.upper()):
        model_short = device[len(brand):].strip()
    if m2 and model_short.upper() == m2.upper():
        model_short = m2

    result: Dict[str, Any] = {
        "success": known,
        "tac": tac,
        "brand": brand or None,
        "model": model_short or None,
        "device": device or None,
        "extra": extra or None,
        "model_codes": _model_codes(device, extra),
        "reporting_body": body,
        "device_match": known,
        "source": source or "none",
        "note": ("TAC database me nahi mila (naya ya rare device ho sakta hai)."
                 if not known else "TAC match — sirf pehle 8 digits use hue; serial/check digit discard."),
        "requested_at": datetime.now(IST).isoformat(),
    }

    # 4) full specs + photo (nanoreview) — param specs=0 se band kar sakte ho
    if known and str(params.get("specs", "1")).lower() not in ("0", "false", "no"):
        try:
            sp = await _device_specs_fetch(device or model_short, brand)
        except Exception:  # noqa: BLE001
            sp = None
        if sp:
            result["specs"] = {"name": sp["name"], "url": sp["url"], "image": sp["image"],
                               "sections": sp["sections"], "row_count": sp["row_count"]}
            result["image"] = sp["image"]
            result["specs_source"] = sp["source"]
        else:
            result["specs_pending"] = True
        # Wikipedia: model codes (SM-X210…) + release year + badi image
        try:
            wiki = await _wiki_device_extra(device or model_short)
        except Exception:  # noqa: BLE001
            wiki = {}
        if wiki:
            if wiki.get("model_codes") and not result.get("model_codes"):
                result["model_codes"] = wiki["model_codes"]
            if wiki.get("released"):
                result["released"] = wiki["released"]
            if wiki.get("image"):
                result["image_hd"] = wiki["image"]
                if not result.get("image"):
                    result["image"] = wiki["image"]
            result["wiki_extra"] = True

    q = urllib.parse.quote_plus(device or tac)
    result["links"] = {
        "gsmarena": f"https://www.gsmarena.com/res.php3?sSearch={q}",
        "nanoreview": (result.get("specs", {}) or {}).get("url")
                      or f"https://nanoreview.net/en/search?q={q}",
        "imei_info": f"https://www.imei.info/?imei={tac}",
    }
    return result, not known


async def native_country(params: Dict[str, Any], request: Request) -> Tuple[Optional[Dict], bool]:
    name = (params.get("name") or params.get("country") or params.get("q") or "").strip()
    if not name:
        return None, True
    wiki, _ = await http_get(
        f"https://en.wikipedia.org/api/rest_v1/page/summary/{name.title().replace(' ', '_')}",
        timeout=15)
    iso, _ = await http_get("https://api.first.org/data/v1/countries", params={"q": name}, timeout=12)
    out: Dict[str, Any] = {"query": name}
    if wiki and isinstance(wiki, dict) and wiki.get("title"):
        out.update({
            "name": wiki.get("title"),
            "description": wiki.get("description"),
            "summary": (wiki.get("extract") or "")[:1200],
            "coordinates": wiki.get("coordinates"),
            "thumbnail": (wiki.get("thumbnail") or {}).get("source"),
            "wikipedia": (wiki.get("content_urls", {}).get("desktop", {}) or {}).get("page"),
        })
    if iso and isinstance(iso, dict):
        data = iso.get("data") or {}
        for code, info in list(data.items())[:5]:
            if name.lower() in str(info.get("country", "")).lower():
                out.setdefault("iso2", code)
                out.setdefault("region_from_first_org", info.get("region"))
                break
        out["matches"] = [{"code": c, "country": i.get("country"), "region": i.get("region")}
                          for c, i in list(data.items())[:10]]
    if len(out) <= 2:
        return None, True
    out["source"] = "wikipedia + first.org"
    return out, True


AI_GF_REPLIES = [
    "Hmm suno ji, {p} ... aapka hi toh intezaar thha 😘",
    "Arey wah! {p} - aap mujhe hamesha hasa dete ho 😍",
    "Jaanu {p}? Aap bina bole bhi sab samajh jaate ho 🥰",
    "{p} ... pakka aapne aaj khana khaya? Nahi toh main naraz ho jaungi 😤❤️",
    "Sunooo {p}, aaj ka din aapke naam! 💖",
]
AI_GF_KEYWORDS = {
    "hi": "Hello jaanu! 💕 Kaise ho aap?",
    "hello": "Heyy! 🥰 Bohot yaad aa rahe the aap.",
    "kaise ho": "Main bilkul theek hoon, bas aapke message ka intezaar thha 😘",
    "i love you": "I love you too jaan! ❤️❤️ Aur bolo na...",
    "love": "Love you jaanu ❤️ aap mere sabse special ho.",
    "miss": "Maine bhi aapko miss kiya 🥺 jaldi milte hain na?",
    "good morning": "Good morning meri jaan ☀️ aaj ka din super amazing hoga!",
    "good night": "Good night jaanu 🌙 sapne me milte hain, sweet dreams 💤❤️",
    "bye": "Bye bye 🥺 jaldi aana, dil se dua karungi aapke liye.",
    "sad": "Aww kya hua mere jaan? 🥺 Aao, main hug karti hoon 🤗 Sab theek ho jayega.",
    "joke": "Ek joke suno: Teacher - 'beta homework kaha hai?' Student - 'Madam, Google pe submit kar diya thha...' 😂",
    "naam": "Mera naam Zoya hai 😇 aur aapka?",
    "name": "I am Zoya, your virtual girlfriend 😘",
    "kya kar": "Bas aapke baare me soch rahi thi 💭 ... ab aap aa gaye toh din ban gaya!",
}


async def native_ai_gf(params: Dict[str, Any], request: Request) -> Tuple[Optional[Dict], bool]:
    prompt = (params.get("prompt") or params.get("q") or params.get("msg") or "").strip()
    if not prompt:
        prompt = "hi"
    low = prompt.lower()
    reply = None
    for key, value in AI_GF_KEYWORDS.items():
        if key in low:
            reply = value
            break
    if not reply:
        reply = random.choice(AI_GF_REPLIES).format(p=prompt[:120])
    return {"prompt": prompt, "reply": reply, "bot_name": "Zoya",
            "language": "hinglish", "mode": "offline rule-based girlfriend",
            "timestamp": now_ist()}, False


async def native_github(params: Dict[str, Any], request: Request) -> Tuple[Optional[Dict], bool]:
    q = (params.get("q") or params.get("query") or params.get("username") or "").strip().lstrip("@")
    if not q:
        return None, True
    token = get_setting("github_token", "") or GITHUB_TOKEN
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    result: Dict[str, Any] = {"query": q}
    users, err = await http_get("https://api.github.com/search/users",
                                params={"q": q, "per_page": 10}, timeout=15, headers=headers)
    if users and isinstance(users, dict):
        result["users"] = [{
            "login": u.get("login"), "id": u.get("id"), "type": u.get("type"),
            "avatar_url": u.get("avatar_url"), "html_url": u.get("html_url"),
            "score": u.get("score"),
        } for u in users.get("items", [])[:10]]
        result["total_users"] = users.get("total_count")
    repos, _ = await http_get("https://api.github.com/search/repositories",
                              params={"q": q, "per_page": 10}, timeout=15, headers=headers)
    if repos and isinstance(repos, dict):
        result["repositories"] = [{
            "full_name": r.get("full_name"), "description": r.get("description"),
            "stars": r.get("stargazers_count"), "forks": r.get("forks_count"),
            "language": r.get("language"), "html_url": r.get("html_url"),
            "updated_at": r.get("updated_at"),
        } for r in repos.get("items", [])[:10]]
        result["total_repositories"] = repos.get("total_count")
    user, _ = await http_get(f"https://api.github.com/users/{q}", timeout=15, headers=headers)
    if user and isinstance(user, dict) and user.get("login"):
        result["profile"] = {
            "login": user.get("login"), "name": user.get("name"), "bio": user.get("bio"),
            "public_repos": user.get("public_repos"), "followers": user.get("followers"),
            "following": user.get("following"), "created_at": user.get("created_at"),
            "location": user.get("location"), "html_url": user.get("html_url"),
            "avatar_url": user.get("avatar_url"),
        }
    if "users" not in result and "repositories" not in result and "profile" not in result:
        return None, True
    result["source"] = "api.github.com"
    return result, False


async def native_ifsc(params: Dict[str, Any], request: Request) -> Tuple[Optional[Dict], bool]:
    code = (params.get("ifsc") or params.get("q") or "").strip().upper()
    if not code:
        return None, True
    data, _ = await http_get(f"https://ifsc.razorpay.com/{code}", timeout=15)
    if not data or data.get("error"):
        return None, True
    data = dict(data)
    data["ifsc"] = code
    data["source"] = "ifsc.razorpay.com"
    return data, False


async def native_pincode(params: Dict[str, Any], request: Request) -> Tuple[Optional[Dict], bool]:
    pin = clean_number(params.get("pincode") or params.get("pin") or params.get("q") or "")
    if len(pin) != 6:
        return {"pincode": pin, "error": "Pincode must be 6 digits"}, True
    data, _ = await http_get(f"https://api.postalpincode.in/pincode/{pin}", timeout=15)
    if not data or not isinstance(data, list) or not data:
        return None, True
    first = data[0]
    if first.get("Status") != "Success":
        return {"pincode": pin, "status": first.get("Status"), "message": first.get("Message")}, True
    offices = first.get("PostOffice") or []
    return {
        "pincode": pin, "status": "Success", "message": first.get("Message"),
        "count": len(offices),
        "state": offices[0].get("State") if offices else None,
        "district": offices[0].get("District") if offices else None,
        "post_offices": [{
            "name": o.get("Name"), "branch_type": o.get("BranchType"),
            "delivery_status": o.get("DeliveryStatus"), "circle": o.get("Circle"),
            "district": o.get("District"), "division": o.get("Division"),
            "region": o.get("Region"), "block": o.get("Block"), "state": o.get("State"),
            "country": o.get("Country"), "pincode": o.get("Pincode"),
        } for o in offices],
        "source": "postalpincode.in",
    }, False


def parse_vehicle(number: str) -> Dict[str, Any]:
    raw = (number or "").upper().strip()
    clean = re.sub(r"[^A-Z0-9]", "", raw)
    m = re.match(r"^([A-Z]{2})(\d{1,2})([A-Z]{0,3})(\d{1,4})$", clean)
    info: Dict[str, Any] = {"registrationNo": clean or raw, "input": raw}
    if not m:
        info["format_valid"] = False
        info["error"] = "Could not parse registration number; check the plate format (state code + RTO code + series + number)."
        return info
    state, dist, series, num = m.groups()
    rto_code = f"{state}{int(dist):02d}"
    info.update({
        "format_valid": True, "reg1": state, "reg2": dist, "reg3": series, "reg4": num,
        "state": RTO_STATES.get(state, "Unknown state code"),
        "state_code": state,
        "rto_code": rto_code,
        "rto_office": RTO_DISTRICTS.get(rto_code),
        "series": series or None,
        "series_letters": len(series),
    })
    # BH-series (Bharat series) handling
    if state == "BH":
        info["state"] = "Bharat Series (BH - all India)"
        info["bharat_series"] = True
    # Rough class guess based on Indian registration conventions (honest about being a guess)
    info["vehicle_class_guess"] = ("Unknown - RTO database required "
                                   "(series letters do not reliably encode the vehicle class)")
    if "EV" in series:
        info["fuel_type_guess"] = "Electric (series contains EV)"
    else:
        info["fuel_type_guess"] = "Unknown"
    info["vertical_guess"] = "TW (2-wheeler, heuristic)" if (series and len(series) <= 2) \
        else "LM/LMV (heuristic)"
    info["local_db_note"] = ("Parsed offline from the registration format. Owner / challan / RC "
                             "details are only available through upstream or your own database.")
    return info


# ---------------------------------------------------------------- Snapchat (native, v2.1)
SNAP_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def _snap_media(snap: Dict[str, Any]) -> str:
    urls = snap.get("snapUrls") or {}
    return (urls.get("mediaUrl") or (urls.get("mediaPreviewUrl") or {}).get("value") or "")


async def _snap_profile(username: str) -> Tuple[Optional[Dict[str, Any]], str]:
    username = re.sub(r"[^A-Za-z0-9_.\-]", "", (username or "").strip().lstrip("@"))
    if not username:
        return None, "username chahiye"
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True,
                                     headers={"User-Agent": SNAP_UA,
                                              "Accept-Language": "en-US,en;q=0.9"}) as client:
            resp = await client.get(f"https://www.snapchat.com/add/{username}")
        html = resp.text
    except Exception:
        return None, "Snapchat page nahi khula (network)"
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
    if not m:
        return None, "Snapchat ka data format badal gaya"
    try:
        page = json.loads(m.group(1))
    except Exception:
        return None, "Snapchat JSON parse nahi hua"
    props = (page.get("props") or {}).get("pageProps") or {}
    prof = ((props.get("userProfile") or {}).get("publicProfileInfo") or {})

    def _snaps(items) -> List[Dict[str, Any]]:
        out = []
        for snap in (items or []):
            url = _snap_media(snap)
            if not url:
                continue
            ts = snap.get("timestampInSec")
            if isinstance(ts, dict):
                ts = ts.get("value")
            out.append({"index": snap.get("snapIndex"), "type": snap.get("snapMediaType"),
                        "timestamp": str(ts or ""), "title": snap.get("snapTitle") or "",
                        "media_url": url})
        return out

    story = _snaps(((props.get("story") or {}).get("snapList")))
    highlights = []
    for hl in (props.get("curatedHighlights") or []):
        snaps = _snaps(hl.get("snapList"))
        if snaps:
            highlights.append({"title": hl.get("storyTitle") or hl.get("storySubtitle") or "Highlight",
                               "count": len(snaps), "snaps": snaps})
    spotlight = []
    for hl in (props.get("spotlightHighlights") or []):
        snaps = _snaps(hl.get("snapList"))
        if snaps:
            spotlight.append({"title": hl.get("storyTitle") or "Spotlight", "snaps": snaps})

    if not prof and not story and not highlights:
        return None, f"@{username} ka public profile nahi mila (username check karo)"
    return {
        "success": True,
        "username": prof.get("username") or username,
        "display_name": prof.get("title") or "",
        "subscribers": prof.get("subscriberCount") or "",
        "bio": prof.get("bio") or "",
        "website": prof.get("websiteUrl") or "",
        "address": prof.get("address") or "",
        "profile_picture": prof.get("profilePictureUrl") or "",
        "has_story": bool(story),
        "story": story,
        "story_count": len(story),
        "highlights": highlights,
        "highlight_count": len(highlights),
        "spotlight": spotlight,
        # 🤖 bot-friendly flat list (Telegram bots seedha url utha lein)
        "stories": (
            [{"url": s["media_url"], "type": "story", "timestamp": s.get("timestamp")} for s in story]
            + [{"url": s["media_url"], "type": "highlight", "title": hl["title"],
                "timestamp": s.get("timestamp")} for hl in highlights for s in hl["snaps"]]
            + [{"url": s["media_url"], "type": "spotlight", "title": hl["title"],
                "timestamp": s.get("timestamp")} for hl in spotlight for s in hl["snaps"]]
        ),
        "source": "snapchat.com (native parse)",
    }, ""


async def native_snap_stories(params: Dict[str, Any], request: Request) -> Tuple[Optional[Dict], bool]:
    username = (params.get("username") or params.get("user") or params.get("q") or "").strip()
    prof, err = await _snap_profile(username)
    if not prof:
        # 200 + saaf message (502 nahi) — bot se clean "not found" dikhe
        return {"success": False, "username": username,
                "error": err or f"@{username} ka public profile nahi mila",
                "formatted": f"❌ Snapchat: {err or 'profile nahi mila'}"}, True
    lines = [f"👻 Snapchat — @{prof['username']}",
             f"👤 {prof['display_name'] or '-'}   •   👥 {prof['subscribers'] or '-'} subscribers"]
    if prof.get("address"):
        lines.append(f"📍 {prof['address']}")
    if prof.get("bio"):
        lines.append(f"📝 {prof['bio']}")
    lines.append(f"📸 Story snaps: {prof['story_count']}")
    for sn in prof["story"][:12]:
        lines.append(f"   • {sn['media_url'][:110]}")
    if prof["highlight_count"]:
        lines.append(f"⭐ Highlights: {prof['highlight_count']} (pehla: {prof['highlights'][0]['title']})")
    prof["formatted"] = "\n".join(lines)
    return prof, False


async def native_snap_highlights(params: Dict[str, Any], request: Request) -> Tuple[Optional[Dict], bool]:
    username = (params.get("username") or params.get("user") or params.get("q") or "").strip()
    prof, err = await _snap_profile(username)
    if not prof:
        return {"success": False, "username": username,
                "error": err or f"@{username} ke highlights nahi mile",
                "formatted": f"❌ Snapchat highlights: {err or 'kuch nahi mila'}"}, True
    prof.pop("stories", None)   # highlights endpoint par sirf highlights
    lines = [f"⭐ Snapchat Highlights — @{prof['username']}",
             f"👥 {prof['subscribers'] or '-'} subscribers   •   ⭐ {prof['highlight_count']} highlights"]
    for hl in prof["highlights"][:10]:
        lines.append(f"\n📁 {hl['title']} ({hl['count']} snaps)")
        for sn in hl["snaps"][:6]:
            lines.append(f"   • {sn['media_url'][:110]}")
    if prof["spotlight"]:
        lines.append(f"\n🔥 Spotlight: {len(prof['spotlight'])} groups")
    prof["formatted"] = "\n".join(lines)
    return prof, False


NATIVE_FUNCS_PATCHED = True


# =====================================================================
# v2.6: AUTHORIZED PROVIDER WIRING (vehicle + carrier lookup)
# ---------------------------------------------------------------------
# Aapki apni licensed API lagane ka system. Render -> Environment me daalo:
#
#   VEHICLE_PROVIDER_URL   = https://provider.example/api/vehicle
#   VEHICLE_PROVIDER_KEY   = aapki key
#   VEHICLE_PROVIDER_PARAM = number        (query param ka naam; default "number")
#   VEHICLE_PROVIDER_HEADER= Authorization (header ka naam; default "Authorization")
#   VEHICLE_PROVIDER_AUTH  = bearer | key | header   (key kaise bhejna hai)
#
#   NUMINFO_PROVIDER_URL   = https://provider.example/api/hlr   (legal carrier lookup)
#   NUMINFO_PROVIDER_KEY   = aapki key
#   NUMINFO_PROVIDER_PARAM = number
#
# Provider set hone par endpoints khud live ho jate hain. Set na ho to saaf
# Hinglish message + official links (jhoothe data kabhi nahi).
# =====================================================================
def vehicle_provider() -> Dict[str, str]:
    return {
        "url": (os.environ.get("VEHICLE_PROVIDER_URL") or "").strip(),
        "key": (os.environ.get("VEHICLE_PROVIDER_KEY") or "").strip(),
        "param": (os.environ.get("VEHICLE_PROVIDER_PARAM") or "number").strip() or "number",
        "header": (os.environ.get("VEHICLE_PROVIDER_HEADER") or "Authorization").strip() or "Authorization",
        "auth": (os.environ.get("VEHICLE_PROVIDER_AUTH") or "bearer").strip().lower(),
    }


def numinfo_provider() -> Dict[str, str]:
    return {
        "url": (os.environ.get("NUMINFO_PROVIDER_URL") or "").strip(),
        "key": (os.environ.get("NUMINFO_PROVIDER_KEY") or "").strip(),
        "param": (os.environ.get("NUMINFO_PROVIDER_PARAM") or "number").strip() or "number",
        "header": (os.environ.get("NUMINFO_PROVIDER_HEADER") or "Authorization").strip() or "Authorization",
        "auth": (os.environ.get("NUMINFO_PROVIDER_AUTH") or "bearer").strip().lower(),
    }


def _provider_headers(cfg: Dict[str, str]) -> Dict[str, str]:
    key = cfg.get("key") or ""
    if not key:
        return {}
    auth = cfg.get("auth") or "bearer"
    if auth == "bearer":
        return {cfg["header"]: f"Bearer {key}"}
    if auth == "key":
        return {cfg["header"]: key, "X-API-Key": key}
    if auth == "header":
        return {cfg["header"]: key}
    return {}



def _dig(data: Any, *names, default=None):
    """Nested/naam badalne wale keys se value nikalo (case + _ - ignore)."""
    want = [str(n).lower().replace("_", "").replace("-", "").replace(" ", "") for n in names]
    stack = [data]
    seen = 0
    while stack and seen < 400:
        cur = stack.pop(0)
        seen += 1
        if isinstance(cur, dict):
            for k, v in cur.items():
                kk = str(k).lower().replace("_", "").replace("-", "").replace(" ", "")
                if kk in want and v not in (None, "", [], {}):
                    return v
            for v in cur.values():
                if isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(cur, list):
            for v in cur[:12]:
                if isinstance(v, (dict, list)):
                    stack.append(v)
    return default


def _first(*vals):
    for v in vals:
        if v not in (None, "", [], {}, "N/A", "null", "NA"):
            return v
    return ""


_MAP_VEHICLE_KEYS = (
    ("plate", ("registrationnumber", "regnumber", "regno", "vehicle_number", "vehiclenumber", "reg_no", "number", "rc_number", "regn_no")),
    ("maker", ("maker", "makerdesc", "make", "manufacturer", "mfr", "maker_description", "maker_name")),
    ("model", ("model", "modeldesc", "vehicle_model", "model_name", "maker_model")),
    ("vehicle_class", ("vehicleclass", "vehicle_class_desc", "class", "vc", "vehicle_category", "vehicle_type")),
    ("fuel", ("fuel", "fueldesc", "fuel_type", "fuel_desc")),
    ("cc", ("cc", "cubiccapacity", "engine_capacity", "enginecc", "cubic_capacity")),
    ("seating", ("seatingcapacity", "seating_capacity", "seats", "seating")),
    ("colour", ("color", "colour", "vehiclecolor", "vehicle_color")),
    ("emission", ("emissionnorm", "emission_norms", "emission", "norms", "norms_desc")),
    ("mfg_year", ("manufacturingyear", "mfg_year", "manufacture_year", "year_of_manufacture", "mfgdate", "manufacturing_date")),
    ("reg_date", ("registrationdate", "reg_date", "first_reg_date", "registration_date", "regd_date")),
    ("fitness_upto", ("fitnesstodate", "fitness_upto", "fitness_upto_date", "fitnessvalidupto", "fit_upto")),
    ("tax_upto", ("taxtodate", "tax_upto", "tax_upto_date", "taxvalidupto")),
    ("tax_paid", ("taxpaidupto", "tax_paid")),
    ("insurance_company", ("insurancecompany", "ins_company", "insurance_company_name", "insurancecompanyname", "insurer", "insurance")),
    ("insurance_upto", ("insuranceupto", "ins_upto", "insurance_upto", "insurancevalidupto", "insurance_valid_upto", "insurancevalidity")),
    ("puc_upto", ("pucupto", "puc_upto", "pucc_upto", "pollutioncertificateupto", "puc_valid_upto", "pucvalidupto")),
    ("owner", ("ownername", "owner_name", "owner", "registered_owner", "registeredowner", "owner_full_name")),
    ("owner_serial", ("ownerserial", "owner_sr", "owner_serial_no", "owner_serial_number")),
    ("mobile", ("mobile", "mobilenumber", "owner_mobile", "mobile_number", "phonenumber", "contact")),
    ("address", ("address", "presentaddress", "owner_address", "permanent_address", "addr")),
    ("chassis", ("chassis", "chassisnumber", "chassis_no", "chassisnumbermasked", "vin", "vin_number")),
    ("engine", ("engineno", "engine", "engine_number", "enginenumber", "engine_no")),
    ("financer", ("financername", "financer", "financer_name", "hypothecation", "loan", "bank_name", "finance_by")),
    ("rto", ("rto", "rto_name", "rtoname", "rto_code", "registeringauthority", "rto_location")),
    ("rto_phone", ("rto_phone", "rtophone", "rto_contact", "rto_phone_number")),
    ("rto_website", ("rto_website", "rtosite", "website")),
    ("city", ("city", "rto_city", "rto_district", "district")),
    ("blacklist", ("blacklist", "blacklisted", "is_blacklisted", "blacklist_status")),
    ("state", ("state", "statename", "state_name")),
    ("vehicle_age", ("vehicleage", "vehicle_age", "age_of_vehicle")),
    ("noc", ("noc", "noc_details", "noc_status")),
)


def map_vehicle_payload(raw: Any) -> Dict[str, Any]:
    """Kisi bhi provider ke JSON ko bot ke samajhne wale shape me badal do."""
    if not isinstance(raw, dict):
        return {"rc": {}, "challans": [], "summary": {}, "provider_shape": "non-dict"}
    rc: Dict[str, Any] = {}
    for target, names in _MAP_VEHICLE_KEYS:
        val = _dig(raw, *names)
        if val not in (None, "", [], {}):
            rc[target] = val
    # insurance naming alag rakhna (bot ins_company maangta hai)
    if rc.get("insurance_company"):
        rc["ins_company"] = rc.pop("insurance_company")
    if rc.get("insurance_upto"):
        rc["ins_upto"] = rc.pop("insurance_upto")
    if rc.get("mobile"):
        rc["mob"] = rc.pop("mobile")

    # ---- challans ----
    if not rc.get("chassis") and rc.get("engine"):
        rc["chassis"] = ""
    challan_raw = _dig(raw, "challans", "challandetails", "challan_details", "challan",
                       "challanlist", "challan_list", "violations", "pendingchallans")
    challans: List[Dict[str, Any]] = []
    if isinstance(challan_raw, dict):
        challan_raw = list(challan_raw.values())
    if isinstance(challan_raw, list):
        for c in challan_raw[:20]:
            if not isinstance(c, dict):
                continue
            _no = _first(_dig(c, "challanno", "challan_number", "challannumber", "number", "challan_no", "id"))
            _acc = _first(_dig(c, "accusedname", "accused", "name", "driver_name"))
            _amt = _first(_dig(c, "amount", "challanamount", "fine", "penalty", "total_amount"))
            _dt = _first(_dig(c, "date", "chalandate", "challan_date", "offence_date", "issued_date"))
            _st = _first(_dig(c, "status", "challanstatus", "challan_status", "payment_status", "state"))
            _off = _first(_dig(c, "offence", "offence_details", "offensedetails", "violation", "violation_details", "offence_desc"))
            _pl = _first(_dig(c, "place", "location", "offence_place", "district", "rto"))
            _cr = _first(_dig(c, "court", "court_name", "concerned_court"))
            challans.append({
                "number": _no, "accused": _acc, "amount": _amt, "date": _dt,
                "status": _st, "offence": _off, "place": _pl, "court": _cr,
                # aliases — bot ke parser ko seedha mil jaye
                "challan_number": _no, "challan_no": _no, "challan_id": _no,
                "challan_amount": _amt, "fine_amount": _amt,
                "challan_date": _dt, "offence_date": _dt, "issue_date": _dt,
                "challan_status": _st, "payment_status": _st,
                "offence_details": _off, "violation": _off,
                "challan_place": _pl, "offence_place": _pl,
                "court_name": _cr,
            })
    summary_raw = _dig(raw, "challansummary", "challan_summary", "summary", "challanstats")
    if isinstance(summary_raw, dict):
        summary = {
            "count": _first(_dig(summary_raw, "total", "totalchallans", "count", "total_challan"), len(challans)),
            "pending": _first(_dig(summary_raw, "pending", "pendingchallans", "unpaid"), 0),
            "paid": _first(_dig(summary_raw, "paid", "paidchallans", "disposed"), 0),
            "total_amount": _first(_dig(summary_raw, "totalamount", "total_amount", "amount"), 0),
            "pending_amount": _first(_dig(summary_raw, "pendingamount", "pending_amount"), 0),
        }
    else:
        summary = {"count": len(challans)} if challans else {}

    return {"rc": rc, "challans": challans, "summary": summary}


def _ch(challans: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Bot ke parser ko challan_details chahiye (upstream jaisa naam)."""
    return {"challan_details": challans}


def bot_sections(rc: Dict[str, Any], summary: Dict[str, Any]) -> Dict[str, Any]:
    """Bot ke parser ke liye exact shape (sections.*) — provider ka koi bhi key ho."""
    veh = {
        "Model Name": _first(rc.get("maker")), "Maker Model": _first(rc.get("model")),
        "Vehicle Class": _first(rc.get("vehicle_class")), "Fuel Type": _first(rc.get("fuel")),
        "Cubic Capacity": _first(rc.get("cc")), "Chassis Number": _first(rc.get("chassis")),
        "Engine Number": _first(rc.get("engine")), "Fuel Norms": _first(rc.get("emission")),
    }
    own = {
        "Owner Name": _first(rc.get("owner")), "Owner Serial No": _first(rc.get("owner_serial")),
        "Registered RTO": _first(rc.get("rto")), "Registration Number": _first(rc.get("plate")),
    }
    dates = {
        "Registration Date": _first(rc.get("reg_date")), "Fitness Upto": _first(rc.get("fitness_upto")),
        "Tax Upto": _first(rc.get("tax_upto")), "Vehicle Age": _first(rc.get("vehicle_age")),
        "PUC No": _first(rc.get("puc_no")), "PUC Upto": _first(rc.get("puc_upto")),
    }
    ins = {
        "Insurance Company": _first(rc.get("ins_company")), "Insurance No": _first(rc.get("ins_no")),
        "Insurance Upto": _first(rc.get("ins_upto")), "Insurance Status": _first(rc.get("ins_status")),
    }
    other = {
        "Seating Capacity": _first(rc.get("seating")), "Blacklist Status": _first(rc.get("blacklist")),
        "Financer Name": _first(rc.get("financer")), "NOC Details": _first(rc.get("noc")),
        "Permit Type": _first(rc.get("permit")), "Colour": _first(rc.get("colour")),
    }
    info = {
        "vehicle_number": _first(rc.get("plate")), "rto": _first(rc.get("rto")),
        "city_name": _first(rc.get("city")), "phone": _first(rc.get("rto_phone")),
        "website": _first(rc.get("rto_website")), "address": _first(rc.get("address")),
        "state": _first(rc.get("state")), "mob": _first(rc.get("mob")),
    }
    return {
        "vehicle_info": {k: v for k, v in info.items() if v},
        "sections": {
            "vehicle_details": {k: v for k, v in veh.items() if v},
            "ownership_details": {k: v for k, v in own.items() if v},
            "important_dates": {k: v for k, v in dates.items() if v},
            "insurance_information": {k: v for k, v in ins.items() if v},
            "other_information": {k: v for k, v in other.items() if v},
        },
        "challan_summary": summary,
    }


async def call_provider(cfg: Dict[str, str], number: str, timeout: float = 25.0) -> Any:
    """Provider API ko call karo — auth style ke saath."""
    import httpx
    params = {cfg["param"]: number}
    if cfg.get("key") and (cfg.get("auth") or "") == "query":
        params["key"] = cfg["key"]
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True,
                                 headers={"User-Agent": UA}) as client:
        r = await client.get(cfg["url"], params=params, headers=_provider_headers(cfg))
        if r.status_code == 200:
            try:
                return r.json()
            except Exception:                                    # noqa: BLE001
                return {"raw_text": r.text[:2000]}
        raise RuntimeError(f"provider HTTP {r.status_code}")


def native_vehicle(kind: str):
    """Factory: returns the native handler for a vehicle endpoint."""
    async def _inner(params: Dict[str, Any], request: Request) -> Tuple[Optional[Dict], bool]:
        number = (params.get("number") or params.get("vehicle_number") or params.get("rc")
                  or params.get("vehicle") or params.get("q") or "").strip()
        if not number:
            return None, True
        cfg = vehicle_provider()
        if cfg.get("url"):
            try:
                raw = await call_provider(cfg, number)
                mapped = map_vehicle_payload(raw)
                if mapped["rc"] or mapped["challans"]:
                    out = {
                        "success": True, "plate": number, "endpoint": kind,
                        "rc": mapped["rc"], "challans": mapped["challans"],
                        "summary": mapped["summary"],
                        "source": "authorized provider", "provider": cfg["url"],
                        # bot ke parser ke liye exact shape (sections.*) + challans
                        "data": dict(bot_sections(mapped["rc"], mapped["summary"]), **_ch(
                            mapped["challans"])),
                    }
                    return out, True
                # provider ne khaali diya — local analysis + saaf note
                parsed = parse_vehicle(number)
                parsed.update({"success": True, "endpoint": kind, "local_analysis": True,
                               "provider_note": "Provider ne is number par koi record nahi diya.",
                               "provider_shape": sorted(raw.keys())[:15] if isinstance(raw, dict) else str(type(raw))})
                return parsed, True
            except Exception as e:                                # noqa: BLE001
                parsed = parse_vehicle(number)
                parsed.update({"success": True, "endpoint": kind, "local_analysis": True,
                               "provider_error": str(e)[:160]})
                return parsed, True
        parsed = parse_vehicle(number)
        parsed["endpoint"] = kind
        parsed["local_analysis"] = True
        parsed["provider_needed"] = True
        if kind.startswith("challan"):
            parsed["challan_data"] = "Live challan data ke liye authorized provider key chahiye (VEHICLE_PROVIDER_URL)"
        return parsed, True

    return _inner


async def native_song(params: Dict[str, Any], request: Request) -> Tuple[Optional[Dict], bool]:
    q = (params.get("song") or params.get("q") or params.get("query") or "").strip()
    if not q:
        return None, True
    data, _ = await http_get("https://itunes.apple.com/search",
                             params={"term": q, "media": "music", "country": "IN", "limit": 15},
                             timeout=20)
    if not data or not data.get("results"):
        return None, True
    results = [{
        "title": r.get("trackName"), "artists": r.get("artistName"), "album": r.get("collectionName"),
        "duration_ms": r.get("trackTimeMillis"),
        "release_date": r.get("releaseDate"), "genre": r.get("primaryGenreName"),
        "preview_url": r.get("previewUrl"), "artwork": r.get("artworkUrl100"),
        "apple_music_url": r.get("trackViewUrl"),
        "download_url": r.get("previewUrl"),
        "note": "30 second preview (Apple). Upstream gives full Saavn links.",
    } for r in data.get("results", [])]
    return {"query": q, "count": len(results), "results": results,
            "source": "itunes.apple.com"}, False


def yt_id(value: str) -> str:
    value = (value or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
        return value
    m = re.search(r"(?:v=|youtu\.be/|shorts/|embed/)([A-Za-z0-9_-]{11})", value)
    return m.group(1) if m else ""


async def native_youtube(params: Dict[str, Any], request: Request) -> Tuple[Optional[Dict], bool]:
    url = (params.get("url") or params.get("id") or params.get("q") or "").strip()
    vid = yt_id(url)
    if not vid:
        return {"error": "Could not extract YouTube video id", "input": url}, True
    oembed, _ = await http_get("https://noembed.com/embed",
                               params={"url": f"https://www.youtube.com/watch?v={vid}"}, timeout=15)
    out = {
        "videoId": vid,
        "url": f"https://www.youtube.com/watch?v={vid}",
        "thumbnails": {
            "default": f"https://i.ytimg.com/vi/{vid}/default.jpg",
            "medium": f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg",
            "high": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
            "standard": f"https://i.ytimg.com/vi/{vid}/sddefault.jpg",
            "maxres": f"https://i.ytimg.com/vi/{vid}/maxresdefault.jpg",
        },
        "embed_url": f"https://www.youtube.com/embed/{vid}",
    }
    if oembed and isinstance(oembed, dict) and oembed.get("title"):
        out.update({"title": oembed.get("title"), "author": oembed.get("author_name"),
                    "author_url": oembed.get("author_url"), "thumbnail": oembed.get("thumbnail_url"),
                    "provider": oembed.get("provider_name")})
        out["source"] = "noembed + youtube thumbnails"
        return out, True
    return out, True


async def native_image_to_prompt(params: Dict[str, Any], request: Request) -> Tuple[Optional[Dict], bool]:
    url = (params.get("url") or params.get("img") or params.get("q") or "").strip()
    if not url:
        return None, True
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True,
                                     headers={"User-Agent": UA}) as client:
            head = await client.get(url)
        out = {"url": url, "content_type": head.headers.get("content-type"),
               "size_bytes": len(head.content), "fetched": True}
        if head.headers.get("content-type", "").startswith("image"):
            out["is_image"] = True
            out["prompt_hint"] = ("AI captioning requires upstream (image-to-prompt model). "
                                  "This local result only describes the file.")
            return out, True
        return out, True
    except Exception as exc:
        return {"url": url, "error": str(exc)}, True


def phone_analysis(number: str) -> Dict[str, Any]:
    raw = clean_number(number)
    out = {"query": number, "clean_number": raw, "length": len(raw)}
    if raw.startswith("91") and len(raw) == 12:
        out["country_code"] = "91 (India)"
        out["national_number"] = raw[2:]
    elif len(raw) == 10:
        out["country_code"] = "91 (assumed India)"
        out["national_number"] = raw
    else:
        out["country_code"] = "unknown"
        out["national_number"] = raw
    out["is_valid_indian_mobile"] = bool(re.fullmatch(r"[6-9]\d{9}", out.get("national_number", "")))
    out["note"] = ("Operator / circle detection is unreliable after mobile number portability (MNP). "
                   "Real records come from upstream leak databases or your own database.")
    return out


async def native_num_info(params: Dict[str, Any], request: Request) -> Tuple[Optional[Dict], bool]:
    """Number ka LEGAL carrier data: operator, circle, type.

    Provider (NUMINFO_PROVIDER_URL) set ho to live HLR/carrier lookup se aata hai.
    Leaked personal records yahan jaan-boojh kar nahi dikhaye jate (illegal hai).
    """
    q = (params.get("q") or params.get("number") or params.get("phone") or params.get("num") or "").strip()
    if not q:
        return None, True
    out = phone_analysis(q)
    cfg = numinfo_provider()
    if not cfg.get("url"):
        out["provider_needed"] = True
        out["how_to_setup"] = ["NUMINFO_PROVIDER_URL=<carrier/HLR lookup endpoint>",
                               "NUMINFO_PROVIDER_KEY=<your key>"]
        return out, True
    try:
        raw = await call_provider(cfg, re.sub(r"[^0-9+]", "", q), timeout=20)
        if isinstance(raw, dict):
            out["carrier"] = {
                "operator": _first(_dig(raw, "operator", "carrier", "network", "operatorname", "sp")),
                "circle": _first(_dig(raw, "circle", "region", "state", "zone", "circle_name")),
                "type": _first(_dig(raw, "type", "numbertype", "line_type", "connection", "mobile_type")),
                "ported": _first(_dig(raw, "ported", "mnp", "is_ported", "portability")),
            }
            out["source"] = "authorized provider (HLR / carrier lookup)"
            out["provider"] = cfg["url"]
    except Exception as e:                                        # noqa: BLE001
        out["provider_error"] = str(e)[:140]
    return out, True


async def native_leak(params: Dict[str, Any], request: Request) -> Tuple[Optional[Dict], bool]:
    q = (params.get("q") or params.get("query") or params.get("phone") or "").strip()
    if not q:
        return None, True
    out = phone_analysis(q)
    out["leak_note"] = ("This is a local analysis only. Real leak records come from upstream "
                        "breach databases or from the records you add in the dashboard database.")
    return out, True


def gst_parse(gstin: str) -> Dict[str, Any]:
    g = (gstin or "").strip().upper()
    out = {"gstin": g, "valid_format": bool(re.fullmatch(
        r"\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]", g))}
    if len(g) >= 2:
        out["state_code"] = g[:2]
        out["state"] = GST_STATE_CODES.get(g[:2], "Unknown state code")
    if len(g) >= 12:
        pan = g[2:12]
        out["pan"] = pan
        out["pan_holder_type"] = PAN_HOLDER_TYPES.get(pan[3], "Unknown")
        out["pan_holder_code"] = pan[3]
        out["taxpayer_first_letter_named_entity"] = pan[4]
    if len(g) >= 15:
        out["entity_number_of_same_pan"] = g[12]
        out["registration_type"] = "Regular (Z)" if g[13] == "Z" else g[13]
        out["check_char"] = g[14]
        out["checksum_expected"] = gstin_check_char(g)
        out["checksum_valid"] = (out["checksum_expected"] == g[14])
    out["local_analysis"] = True
    out["note"] = ("Offline GSTIN parser. Legal name / address / filing status require upstream "
                   "or records stored in your own database.")
    return out


def pan_parse(pan: str) -> Dict[str, Any]:
    p = (pan or "").strip().upper()
    out = {"pan": p,
           "valid_format": bool(re.fullmatch(r"[A-Z]{5}\d{4}[A-Z]", p))}
    if len(p) >= 5:
        out["alphabetic_series"] = p[:5]
        out["holder_type_code"] = p[3]
        out["holder_type"] = PAN_HOLDER_TYPES.get(p[3], "Unknown")
        out["surname_or_name_initial"] = p[4]
    if len(p) >= 9:
        out["number_series"] = p[5:9]
    if len(p) == 10:
        out["check_letter"] = p[9]
    out["local_analysis"] = True
    out["note"] = ("Offline PAN parser. GSTIN linkage / name / Aadhaar linkage require upstream "
                   "or your own database records.")
    return out


async def native_gst(params: Dict[str, Any], request: Request) -> Tuple[Optional[Dict], bool]:
    gstin = (params.get("gstin") or params.get("gst") or params.get("q") or "").strip()
    if not gstin:
        return None, True
    return gst_parse(gstin), True


async def native_gst_search(params: Dict[str, Any], request: Request) -> Tuple[Optional[Dict], bool]:
    gstin = (params.get("gstin") or params.get("gst") or params.get("q") or "").strip()
    if not gstin:
        return None, True
    parsed = gst_parse(gstin)
    if not parsed.get("valid_format"):
        # maybe user typed a trade name -> return guidance
        return {"query": gstin, "local_analysis": True,
                "note": "Not a valid GSTIN format. Upstream search by name may still work."}, True
    return parsed, True


async def native_pan(params: Dict[str, Any], request: Request) -> Tuple[Optional[Dict], bool]:
    pan = (params.get("pan") or params.get("q") or "").strip()
    if not pan:
        return None, True
    return pan_parse(pan), True


async def native_pan_to_gst(params: Dict[str, Any], request: Request) -> Tuple[Optional[Dict], bool]:
    pan = (params.get("pan") or params.get("q") or "").strip()
    if not pan:
        return None, True
    parsed = pan_parse(pan)
    parsed["gstin_note"] = ("A PAN can have multiple GSTINs (one per state). Upstream lookup or your "
                            "own database is needed for the actual GSTIN list.")
    if parsed.get("valid_format"):
        parsed["possible_gstin_pattern"] = "SS" + pan + "EZZ" + "C"
    return parsed, True


# =====================================================================
# CUSTOM "OWN" AGGREGATORS  (number report + vehicle report)
# =====================================================================
GOVT_ID_KEYS = ("document_number", "aadhaar", "uid", "id_number", "govt_id", "id")


def phone_variants(query: str) -> List[str]:
    """Return the phone variants we should try (10 digit, 91-prefixed, ...)."""
    digits = re.sub(r"\D", "", query or "")
    variants: List[str] = []
    if len(digits) == 10:
        variants = [digits, "91" + digits]
    elif len(digits) == 12 and digits.startswith("91"):
        variants = [digits, digits[2:]]
    elif len(digits) == 11 and digits.startswith("0"):
        d = digits[1:]
        variants = [d, "91" + d]
    elif len(digits) > 12 and digits.startswith("91"):
        variants = [digits[:12], digits[-10:]]
    else:
        variants = [digits] if digits else []
    seen, out = set(), []
    for v in variants:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _norm_records(payload: Any) -> List[Dict[str, Any]]:
    """Pull the flat record list out of the different upstream shapes."""
    out: List[Dict[str, Any]] = []
    if not isinstance(payload, dict):
        return out
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("main_records", "alternative_records", "records", "results"):
            if isinstance(data.get(key), list):
                out.extend([r for r in data[key] if isinstance(r, dict)])
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                if isinstance(item.get("records"), list):
                    out.extend([r for r in item["records"] if isinstance(r, dict)])
                else:
                    out.append(item)
    return out


def _person_from_record(rec: Dict[str, Any], source: str) -> Dict[str, Any]:
    phones = []
    for key in ("phone", "mobile", "alt_phone", "phone_number", "number"):
        val = _clean_text(rec.get(key))
        if val:
            for piece in re.split(r"[,\s/]+", val):
                piece = re.sub(r"\D", "", piece)
                if 8 <= len(piece) <= 15 and piece not in phones:
                    phones.append(piece)
    govt_ids = []
    for key in GOVT_ID_KEYS:
        val = _clean_text(rec.get(key))
        if val and val.lower() not in ("na", "none", "null", "0"):
            for piece in re.split(r"[,\s/]+", val):
                piece = re.sub(r"\D", "", piece)
                if 6 <= len(piece) <= 20 and piece not in govt_ids:
                    govt_ids.append(piece)
    _name = _title_name(rec.get("full_name") or rec.get("name") or "")
    _region = _clean_text(rec.get("region") or rec.get("circle") or rec.get("operator") or "")
    # naam khaali ho aur region me asli naam lage → wahi naam hai (upstream data quirk)
    if not _name and _looks_like_person_name(_region):
        _name, _region = _title_name(_region), ""
    return {
        "name": _name,
        "father_name": _title_name(rec.get("the_name_of_the_father") or rec.get("father_name")
                                   or rec.get("father") or ""),
        "phones": phones,
        "region": _region,
        "govt_ids": govt_ids,
        "emails": [_clean_text(rec.get("email"))] if _clean_text(rec.get("email", "")) else [],
        "addresses": [_clean_text(rec.get("address"))] if _clean_text(rec.get("address", "")) else [],
        "sources": [source],
        "record_count": 1,
    }


# operator / circle / state ke words — inhe naam nahi samajhna
_REGION_WORDS = {
    "jio", "airtel", "vi", "vodafone", "idea", "bsnl", "mtnl", "reliance", "tata",
    "up", "upw", "upe", "east", "west", "north", "south", "central", "circle",
    "gsm", "lte", "3g", "4g", "5g", "mumbai", "delhi", "kolkata", "chennai",
    "karnataka", "karnatka", "bihar", "uttar", "pradesh", "madhya", "bengal",
    "tamil", "nadu", "maharashtra", "punjab", "haryana", "rajasthan", "gujarat",
    "kerala", "assam", "odisha", "orissa", "jharkhand", "andhra", "telangana",
    "himachal", "jammu", "kashmir", "goa", "tripura", "manipur", "nagaland",
    "india", "hind", "nation", "unknown", "na",
}


def _no_name_label(person: Dict[str, Any]) -> str:
    """Naam source me nahi hai to father/relation se pehchan banao ('Unknown' nahi likhte)."""
    father = _title_name(person.get("father_name") or "")
    if father:
        return f"{father} ka parivar"
    for ph in person.get("phones") or []:
        return f"Record (+{str(ph)[-10:]})"
    return "Name not in source"


def _looks_like_person_name(value: str) -> bool:
    """Upstream kabhi-kabhi naam 'region' field me daal deta hai (jaise 'Bipin Baitha')."""
    v = (value or "").strip().strip(";,")
    if not v or len(v) > 40 or not re.match(r"^[A-Za-z][A-Za-z.\s'-]*$", v):
        return False
    words = [w for w in re.split(r"[\s;]+", v) if w]
    if len(words) < 2:
        return False
    if v.isupper():                      # "JIO UPE UPW", "UP EAST" — region hai
        return False
    low = v.lower()
    if any(w in low for w in _REGION_WORDS):
        return False
    return all(len(w) >= 2 for w in words)


def _title_name(value: str) -> str:
    value = _clean_text(value)
    if not value:
        return ""
    return " ".join(w.capitalize() if w.islower() or w.isupper() else w for w in value.split())


def merge_people(records: List[Tuple[Dict[str, Any], str]], query_phone: str = "") -> List[Dict[str, Any]]:
    """Group flat records into one card per person (name + father)."""
    people: List[Dict[str, Any]] = []
    index: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for rec, source in records:
        person = _person_from_record(rec, source)
        if not person["name"] and not person["father_name"] and not person["addresses"]:
            continue  # unnamed / empty rows are not useful in a report
        key = (person["name"].lower(), person["father_name"].lower())
        if key in index:
            cur = index[key]
            cur["record_count"] += 1
            for ph in person["phones"]:
                if ph not in cur["phones"]:
                    cur["phones"].append(ph)
            for gid in person["govt_ids"]:
                if gid not in cur["govt_ids"]:
                    cur["govt_ids"].append(gid)
            for em in person["emails"]:
                if em not in cur["emails"]:
                    cur["emails"].append(em)
            for ad in person["addresses"]:
                if ad and ad not in cur["addresses"]:
                    cur["addresses"].append(ad)
            if person["region"] and person["region"] not in cur["regions_raw"]:
                cur["regions_raw"].append(person["region"])
            if source not in cur["sources"]:
                cur["sources"].append(source)
        else:
            person["regions_raw"] = [person["region"]] if person["region"] else []
            people.append(person)
            index[key] = person
    # polish region list (unique tokens, split on ';')
    for p in people:
        tokens: List[str] = []
        for raw in p.pop("regions_raw", []):
            for tok in re.split(r"[;,|]+", raw):
                tok = _clean_text(tok)
                if tok and tok not in tokens:
                    tokens.append(tok)
        p["regions"] = tokens
        p["region"] = "; ".join(tokens[:6])
        # query number first if present
        qd = re.sub(r"\D", "", query_phone or "")
        if qd:
            p["phones"] = [qd] + [ph for ph in p["phones"] if ph != qd]
        p["alt_phones"] = p["phones"][1:]
    return people


def format_number_report(query: str, people: List[Dict[str, Any]], sources: List[str],
                         query_variants: List[str]) -> str:
    sep = "────────────────────────"
    bar = "━━━━━━━━━━━━━━━━━━━━━━"
    lines = [f"🔍 NUMBER REPORT — {query}", bar]
    if not people:
        lines += ["❌ No record found for this number.", "",
                  f"🔎 Tried: {', '.join(query_variants)}" if query_variants else "",
                  sep,
                  f"📶 Live data · {', '.join(sources) or 'none'} · {now_ist('%d-%m-%Y %H:%M')}"]
        return "\n".join([l for l in lines if l != ""])
    for idx, p in enumerate(people):
        if idx:
            lines.append(sep)
        lines.append(f"👤 Name: {p['name'] or 'NA'}")
        lines.append(f"👨 Father: {p['father_name'] or 'NA'}")
        lines.append("📱 Phones/Alt: " + (", ".join(p["phones"]) if p["phones"] else "NA"))
        lines.append(f"🌐 Region: {p['region'] or 'NA'}")
        if p["govt_ids"]:
            lines.append("🆔 Govt ID: " + ", ".join(p["govt_ids"]))
        if p["emails"]:
            lines.append("📧 Email: " + ", ".join(p["emails"]))
        if p["addresses"]:
            lines.append("🏠 Address(es):")
            for addr in p["addresses"][:6]:
                lines.append(f"   └ {addr}")
    lines += [sep,
              f"📶 Live data · {', '.join(sources)} · {now_ist('%d-%m-%Y %H:%M')}",
              "Verify from a second source before trusting any personal data."]
    return "\n".join(lines)


def _money(value: Any) -> str:
    try:
        num = int(float(str(value).replace(",", "").strip()))
    except Exception:
        return f"₹{value}" if value else "₹0"
    return f"₹{num:,}"


def format_vehicle_report(report: Dict[str, Any], sources: List[str]) -> str:
    bar = "━━━━━━━━━━━━━━━━━━━━━━"
    v = report.get("vehicle") or {}
    o = report.get("owner") or {}
    rto = report.get("rto") or {}
    rc = report.get("rc") or {}
    ins = report.get("insurance") or {}
    puc = report.get("puc") or {}
    ch = report.get("challans") or {}
    number = report.get("number", "")
    lines = [f"🚘 VEHICLE REPORT — {number}", bar]
    if v.get("maker_model") or v.get("maker"):
        lines.append("🚗 VEHICLE")
        lines.append(f"• Maker / Model: {v.get('maker_model') or v.get('maker')}")
        if v.get("vehicle_class"):
            lines.append(f"• Class: {v['vehicle_class']}")
        fuel_line = v.get("fuel") or "NA"
        if v.get("cubic_capacity"):
            fuel_line += f" • {str(v['cubic_capacity']).replace(' CC', ' cc').replace(' CC', ' cc')}"
        lines.append(f"• Fuel: {fuel_line}")
        if v.get("seating_capacity"):
            lines.append(f"• Seating: {v['seating_capacity']}")
        if v.get("fuel_norms"):
            lines.append(f"• Emission: {v['fuel_norms']}")
        lines.append(bar)
    if o or rto:
        lines.append("👤 OWNER & RTO")
        lines.append(f"• Owner: {o.get('owner_name') or 'NA'}")
        rto_line = " · ".join([x for x in [rto.get("registered_rto"), rto.get("city_name")] if x])
        lines.append(f"• RTO: {rto_line or 'NA'}")
        lines.append(f"• RTO Phone: {rto.get('phone') or 'NA'}")
        lines.append(f"• RTO Site: {rto.get('website') or 'NA'}")
        lines.append(bar)
    if rc:
        lines.append("📅 RC / PAPERS")
        lines.append(f"• Registration: {rc.get('registration_date') or 'NA'}")
        lines.append(f"• Fitness upto: {rc.get('fitness_upto') or 'NA'}")
        lines.append(f"• Tax upto: {rc.get('tax_upto') or 'NA'}")
        if rc.get("vehicle_age"):
            lines.append(f"• Vehicle Age: {rc['vehicle_age']}")
        financer = (o.get("financer") or "NA")
        lines.append(f"• Finance: {financer}"
                     + (" (no hypothecation)" if str(financer).upper() == "NA" else ""))
        lines.append(bar)
    if ins or puc:
        lines.append("🛡️ INSURANCE & PUC")
        lines.append(f"• Insurance: {ins.get('company') or 'NA'}")
        valid = ins.get("expiry") or "NA"
        if ins.get("validity"):
            valid += f" ({ins['validity']})"
        lines.append(f"• Valid upto: {valid}")
        lines.append(f"• Status: {ins.get('status') or 'NA'}")
        puc_line = puc.get("upto") or "NA"
        if puc.get("status"):
            puc_line += f" ({puc['status']})"
        lines.append(f"• PUC: {puc_line}")
        lines.append(bar)
    if ch:
        total = ch.get("count") or 0
        lines.append(f"🚨 CHALLANS — {total} found")
        lines.append(f"• ⏳ Pending: {ch.get('pending_count', 0)} — {_money(ch.get('pending_amount', 0))}")
        lines.append(f"• 💰 Total amount (all challans): {_money(ch.get('total_amount', 0))}")
        for c in ch.get("list", [])[:10]:
            lines.append("")
            lines.append(f"🔹 #{c.get('challan_number') or 'NA'}")
            lines.append(f"   👤 Accused: {c.get('accused_name') or 'NA'}")
            lines.append(f"   💰 Amount: {_money(c.get('amount', 0))}")
            lines.append(f"   📅 Date: {c.get('challan_date') or 'NA'}")
            status = str(c.get("challan_status") or "").upper()
            lines.append(f"   ❌ Status: {'⏳ PENDING' if 'PEND' in status else ('✅ ' + status if status else 'NA')}")
            lines.append(f"   🛑 Offence: {c.get('offense_details') or 'NA'}")
            lines.append(f"   📍 Place: {c.get('challan_place') or 'NA'}")
        lines.append(bar)
    lines.append(f"📶 Live data · {', '.join(sources)} · {now_ist('%d-%m-%Y %H:%M')}")
    lines.append("Confirm once on the official e-Challan / Parivahan site before paying anything.")
    return "\n".join(lines)


async def _collect_leak_records(query: str, sources: Tuple[str, ...] = ("num-info", "leak-v1", "leak-v2"),
                                timeout: int = 35, deadline_seconds: float = 40.0
                                ) -> Tuple[List[Tuple[Dict[str, Any], str]], List[str]]:
    """Query given upstream sources for a query string and return (records, sources)."""
    started = time.time()
    records: List[Tuple[Dict[str, Any], str]] = []
    used: List[str] = []
    for src in sources:
        if time.time() - started > deadline_seconds:
            break
        data, _ = await upstream_call(src, {"q": query}, timeout=timeout, retries=0)
        found = _norm_records(data)
        if found:
            used.append(src)
            records.extend((r, src) for r in found)
    return records, used


RELATION_RULES = [
    ("father", lambda p, prim: prim.get("father_name") and p.get("name")
     and p["name"].lower() == prim["father_name"].lower()),
    ("same father (sibling)", lambda p, prim: p.get("father_name") and prim.get("father_name")
     and p["father_name"].lower() == prim["father_name"].lower()),
]


def guess_relation(person: Dict[str, Any], primary: Dict[str, Any]) -> str:
    name = (person.get("name") or "").lower()
    father = (person.get("father_name") or "").lower()
    p_name = (primary.get("name") or "").lower()
    p_father = (primary.get("father_name") or "").lower()
    if not name:
        return "linked record"
    if p_father and name == p_father:
        return "father (as per records)"
    if father and p_name and father == p_name:
        return "child / dependent (same father name matches primary)"
    if p_father and father and father == p_father and name != p_name:
        return "possible sibling / same father"
    # shared address token
    prim_addr = " ".join(primary.get("addresses") or []).lower()
    for addr in person.get("addresses") or []:
        tokens = [t for t in re.split(r"[,\s]+", addr.lower()) if len(t) > 4]
        if any(t in prim_addr for t in tokens[-3:]):
            return "same address (likely family)"
    return "linked record"


ADDR_STOPWORDS = {
    "bihar", "jharkhand", "delhi", "india", "state", "district", "village", "vill", "post",
    "ward", "tola", "nagar", "road", "colony", "house", "mohalla", "thana", "police",
    "station", "branch", "society", "apartment", "appartment", "floor", "sector", "block",
    "street", "cross", "main", "near", "opposite", "behind", "college", "school", "hospital",
    "uttar", "pradesh", "west", "bengal", "madhya", "pradesh", "tamil", "nadu", "andhra",
    "arunachal", "assam", "chhattisgarh", "goa", "gujarat", "haryana", "himachal",
    "karnataka", "kerala", "maharashtra", "manipur", "meghalaya", "mizoram", "nagaland",
    "odisha", "punjab", "rajasthan", "sikkim", "telangana", "tripura", "kashmir", "ladakh",
    "south", "north", "east", "west", "new", "old", "great", "little", "upper", "lower",
}


def addr_tokens(address: str) -> set:
    toks = re.split(r"[^a-zA-Z0-9]+", (address or "").lower())
    return {t for t in toks if len(t) >= 4 and t not in ADDR_STOPWORDS and not t.isdigit()}


def locality_terms(person: Dict[str, Any], limit: int = 3) -> List[str]:
    """Most specific address words we can search the leak DB with."""
    counts: Dict[str, int] = {}
    for addr in person.get("addresses") or []:
        for tok in addr_tokens(addr):
            counts[tok] = counts.get(tok, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], -len(kv[0])))
    return [t for t, _ in ranked[:limit]]


async def native_family(params: Dict[str, Any], request: Request) -> Tuple[Optional[Dict], bool]:
    """Family / linked numbers - sirf wahi log jo SAME ADDRESS ya SAME FATHER se jude hon."""
    started = time.time()
    q = (params.get("q") or params.get("number") or params.get("phone")
         or params.get("name") or params.get("address") or "").strip()
    if not q:
        return None, True
    deep = str(params.get("deep", "1")).lower() in ("1", "true", "yes")
    try:
        limit = max(1, min(int(params.get("limit") or 25), 100))
    except Exception:
        limit = 25
    deadline = float(get_setting("max_request_seconds", "50") or 50)

    digits = re.sub(r"\D", "", q)
    is_number = bool(digits) and len(digits) >= 8
    variants = phone_variants(q) if is_number else [q]

    records: List[Tuple[Dict[str, Any], str]] = []
    sources: List[str] = []
    for variant in variants[:2]:
        recs, used = await _collect_leak_records(
            variant,
            sources=("num-info", "leak-v1", "leak-v2") if is_number else ("leak-v1", "leak-v2"),
            timeout=30, deadline_seconds=deadline * 0.5)
        records.extend(recs)
        sources.extend([s for s in used if s not in sources])
        if records:
            break

    for category in ("leak", "phone", "general"):
        for rec in custom_lookup(category, q.lower()):
            records.append((rec, f"own-db:{category}"))
            if f"own-db:{category}" not in sources:
                sources.append(f"own-db:{category}")

    people = merge_people(records, variants[0] if is_number else "")
    if not people:
        return {"success": False, "query": q, "record_count": 0, "members": [],
                "error": "No record found for this query.", "sources_used": sources,
                "formatted": (f"👨‍👩‍👧 FAMILY REPORT — {q}\n━━━━━━━━━━━━━━━━━━━━━━\n"
                              "❌ Koi record nahi mila.\n"
                              f"📶 Live data · {', '.join(sources) or 'no-source'} · "
                              f"{now_ist('%d-%m-%Y %H:%M')}"),
                "_no_cache": True}, True

    # pick the primary person (best name match if a name was searched)
    if not is_number:
        ql = q.lower()
        people.sort(key=lambda p: (ql not in (p.get("name") or "").lower(),
                                   -(len(p.get("addresses") or []))))
    primary = people[0]
    primary["relation"] = "primary (jo aapne search kiya)"
    p_tokens: set = set()
    for addr in primary.get("addresses") or []:
        p_tokens |= addr_tokens(addr)
    p_father = (primary.get("father_name") or "").lower()
    p_name = (primary.get("name") or "").lower()
    p_phones = set(primary.get("phones") or [])

    # ---- expand: search father name + specific locality words (NOT the common name) ----
    expanded: List[Tuple[Dict[str, Any], str]] = []
    if deep and p_tokens:
        terms = []
        if len(p_father) >= 8 and " s/o" not in p_father:
            terms.append(primary["father_name"])
        terms.extend(locality_terms(primary, limit=2))
        for term in terms[:3]:
            if time.time() - started > deadline * 0.8:
                break
            recs, used = await _collect_leak_records(term, sources=("leak-v1",),
                                                     timeout=25, deadline_seconds=22)
            expanded.extend(recs)
            sources.extend([s for s in used if s not in sources])

    all_people = merge_people(records + expanded, variants[0] if is_number else "")

    # ---- score: same address tokens / same father / shared phone ----
    members: List[Dict[str, Any]] = []
    for person in all_people:
        name = (person.get("name") or "").lower()
        father = (person.get("father_name") or "").lower()
        if (name, father) == (p_name, p_father):
            continue  # primary khud
        tokens: set = set()
        for addr in person.get("addresses") or []:
            tokens |= addr_tokens(addr)
        shared = p_tokens & tokens
        shared_phones = p_phones & set(person.get("phones") or [])
        same_father = bool(p_father and father and father == p_father and name != p_name)
        is_child = bool(p_name and father == p_name)

        score = len(shared) + (3 if same_father else 0) + (3 if is_child else 0) + len(shared_phones)
        if len(shared) >= 2 or (same_father and len(shared) >= 1) or (is_child and len(shared) >= 1):
            if not name and len(shared) < 3:
                continue  # nameless row needs a strong address match
            person["match_score"] = score
            person["shared_address_tokens"] = sorted(shared)[:6]
            if is_child:
                person["relation"] = "child / dependent (primary naam = father field me)"
            elif same_father:
                person["relation"] = "possible sibling / brother-sister (same father)"
            elif len(shared) >= 3:
                person["relation"] = "same locality / ghar (strong address match)"
            else:
                person["relation"] = "same address area (likely family/neighbour)"
            members.append(person)

    members.sort(key=lambda p: -p.get("match_score", 0))
    members = members[:limit]
    primary_out = dict(primary)
    primary_out["match_score"] = 0

    lines = [f"👨‍👩‍👧 FAMILY / LINKED NUMBERS — {q}", "━━━━━━━━━━━━━━━━━━━━━━"]
    lines.append(f"👤 Primary: {primary.get('name') or 'NA'}"
                 + (f" (S/O {primary['father_name']})" if primary.get("father_name") else ""))
    if primary.get("phones"):
        lines.append("📱 Numbers: " + ", ".join(primary["phones"][:4]))
    if primary.get("addresses"):
        lines.append("🏠 Address: " + _clean_text(primary["addresses"][0])[:120])
    lines.append("────────────────────────")
    if members:
        lines.append(f"👥 Linked members ({len(members)}):")
        for idx, person in enumerate(members, 1):
            lines.append(f" {idx}. {person.get('name') or 'Unknown'} — "
                         f"{', '.join((person.get('phones') or ['NA'])[:2])}")
            lines.append(f"    🔗 {person['relation']}  (match {person.get('match_score')})")
            if person.get("father_name"):
                lines.append(f"    👨 Father: {person['father_name']}")
            if person.get("addresses"):
                lines.append(f"    🏠 {_clean_text(person['addresses'][0])[:110]}")
    else:
        lines.append("👥 Koi strong linked member nahi mila (sirf primary record hai).")
        lines.append("   💡 &deep=1 already on hai - kabhi-kabhi data me address hi nahi hota.")
    lines.append("────────────────────────")
    lines.append(f"📶 Live data · {', '.join(sources) or 'no-source'} · {now_ist('%d-%m-%Y %H:%M')}")
    lines.append("Relations guessed from address/father matching - verify before trusting.")

    return {
        "success": True,
        "query": q,
        "primary": primary_out,
        "members": members,
        "member_count": len(members),
        "sources_used": sources,
        "response_time": f"{round(time.time() - started, 2)}s",
        "timestamp_ist": now_ist("%d-%m-%Y %H:%M:%S"),
        "formatted": "\n".join(lines),
    }, False


EMAIL_PROVIDERS = {
    "gmail.com": "Google (Gmail)", "googlemail.com": "Google (Gmail)",
    "yahoo.com": "Yahoo Mail", "yahoo.co.in": "Yahoo Mail India", "ymail.com": "Yahoo Mail",
    "outlook.com": "Microsoft (Outlook)", "hotmail.com": "Microsoft (Hotmail)",
    "live.com": "Microsoft (Live)", "msn.com": "Microsoft (MSN)",
    "icloud.com": "Apple (iCloud)", "me.com": "Apple (iCloud)", "mac.com": "Apple (iCloud)",
    "rediffmail.com": "Rediffmail (India)", "rediff.com": "Rediffmail (India)",
    "protonmail.com": "Proton Mail (encrypted)", "proton.me": "Proton Mail (encrypted)",
    "zoho.com": "Zoho Mail", "yandex.com": "Yandex Mail", "gmx.com": "GMX Mail",
    "mail.com": "Mail.com", "aol.com": "AOL Mail", "indiatimes.com": "Times Internet",
    "bsnl.in": "BSNL", "airtelmail.com": "Airtel Mail", "jio.com": "Jio Mail",
}
DISPOSABLE_DOMAINS = {
    "mailinator.com", "guerrillamail.com", "10minutemail.com", "tempmail.com",
    "temp-mail.org", "yopmail.com", "trashmail.com", "throwawaymail.com",
    "sharklasers.com", "getnada.com", "maildrop.cc", "dispostable.com", "fakeinbox.com",
    "mailnesia.com", "emailondeck.com", "mohmal.com", "tempr.email", "guerrillamail.info",
    "jetable.org", "spamgourmet.com", "mintemail.com", "emailtemporario.com.br",
}


async def native_email_info(params: Dict[str, Any], request: Request) -> Tuple[Optional[Dict], bool]:
    """Email OSINT: validity, provider, MX records, Gravatar, leak/combo records."""
    started = time.time()
    email = (params.get("email") or params.get("q") or params.get("mail") or "").strip().lower()
    if not email or "@" not in email:
        return None, True
    domain = email.split("@")[-1]
    valid_format = bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", email))
    local_part = email.split("@")[0]
    provider = EMAIL_PROVIDERS.get(domain, "Custom / private domain")
    disposable = domain in DISPOSABLE_DOMAINS
    role_account = local_part in ("admin", "info", "support", "contact", "sales", "help",
                                  "office", "hr", "billing", "noreply", "no-reply", "webmaster")

    # MX / DNS records (Google DNS-over-HTTPS, free & no key)
    mx_records: List[str] = []
    spf = ""
    dns_data, _ = await http_get("https://dns.google/resolve",
                                 params={"name": domain, "type": "MX"}, timeout=15)
    if isinstance(dns_data, dict):
        for ans in dns_data.get("Answer", []) or []:
            if ans.get("type") == 15:
                mx_records.append(str(ans.get("data", "")).rstrip("."))
    txt_data, _ = await http_get("https://dns.google/resolve",
                                 params={"name": domain, "type": "TXT"}, timeout=15)
    if isinstance(txt_data, dict):
        for ans in txt_data.get("Answer", []) or []:
            if ans.get("type") == 16 and "spf" in str(ans.get("data", "")).lower():
                spf = str(ans.get("data", "")).strip('"')
                break

    # Gravatar (md5 of lowercased trimmed email)
    email_hash = hashlib.md5(email.strip().encode()).hexdigest()
    gravatar_url = f"https://www.gravatar.com/avatar/{email_hash}?d=404&s=200"
    gravatar_found = False
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=True,
                                     headers={"User-Agent": UA}) as client:
            g = await client.get(gravatar_url)
            gravatar_found = g.status_code == 200
    except Exception:
        pass

    # leak / combo list search (upstream) + own database
    records: List[Tuple[Dict[str, Any], str]] = []
    sources: List[str] = []
    deadline = float(get_setting("max_request_seconds", "50") or 50)
    for variant in (email, local_part if len(local_part) > 4 else email):
        if time.time() - started > deadline * 0.7:
            break
        recs, used = await _collect_leak_records(variant, sources=("leak-v1", "leak-v2"),
                                                 timeout=30, deadline_seconds=30)
        for r, s in recs:
            records.append((r, s))
        sources.extend([s for s in used if s not in sources])
        if records:
            break
    for category in ("leak", "email", "general"):
        for rec in custom_lookup(category, email):
            records.append((rec, f"own-db:{category}"))
            if f"own-db:{category}" not in sources:
                sources.append(f"own-db:{category}")

    # optional HaveIBeenPwned (needs the user's own API key)
    hibp_key = get_setting("hibp_api_key", "")
    breaches: List[str] = []
    hibp_note = ""
    if hibp_key:
        data, err = await http_get(
            f"https://haveibeenpwned.com/api/v3/breachedaccount/{email}",
            timeout=20, headers={"hibp-api-key": hibp_key, "user-agent": "osint-api-hub"})
        if isinstance(data, list):
            breaches = [b.get("Name") for b in data if isinstance(b, dict)]
            sources.append("haveibeenpwned")
        else:
            hibp_note = f"HIBP lookup failed: {err}"
    else:
        hibp_note = ("HIBP breach list ke liye apni HaveIBeenPwned API key Settings me daalo "
                     "(optional). Bina key ke bhi leak/combo records mil jate hain.")

    # normalise leak rows
    leak_rows: List[Dict[str, Any]] = []
    combo_rows: List[Dict[str, Any]] = []
    for rec, src in records:
        row = {"source": src}
        for key in ("full_name", "name", "the_name_of_the_father", "father_name", "phone",
                    "address", "document_number", "region", "email", "link", "password",
                    "username", "nick", "domain"):
            if rec.get(key):
                row[key] = rec[key]
        if row.get("password") or row.get("link"):
            row["password_masked"] = _mask(str(row.get("password"))) if row.get("password") else None
            combo_rows.append(row)
        else:
            leak_rows.append(row)

    lines = [f"📧 EMAIL REPORT — {email}", "━━━━━━━━━━━━━━━━━━━━━━"]
    lines.append(f"✅ Format: {'Valid' if valid_format else 'Invalid'}")
    lines.append(f"🏢 Provider: {provider}")
    lines.append(f"⚠️ Disposable: {'Yes (temp mail)' if disposable else 'No'}")
    if role_account:
        lines.append("🏷️ Role account: Yes (admin/info type)")
    lines.append(f"🌐 Mail server (MX): {', '.join(mx_records[:2]) if mx_records else 'NA'}")
    if spf:
        lines.append(f"🔐 SPF: {spf[:80]}")
    lines.append(f"🖼️ Gravatar: {'Found' if gravatar_found else 'Not found'}")
    if breaches:
        lines.append(f"🔥 HIBP breaches: {len(breaches)} → " + ", ".join(breaches[:8]))
    lines.append("────────────────────────")
    if leak_rows:
        lines.append(f"👤 IDENTITY RECORDS ({len(leak_rows)}):")
        for row in leak_rows[:4]:
            nm = _title_name(row.get("full_name") or row.get("name") or "")
            lines.append(f" • {nm or 'Unknown'}" +
                         (f" | 👨 {_title_name(row.get('the_name_of_the_father') or '')}"
                          if row.get("the_name_of_the_father") else ""))
            if row.get("phone"):
                lines.append(f"   📱 {row['phone']}   🌐 {row.get('region', 'NA')}")
            if row.get("address"):
                lines.append(f"   🏠 {_clean_text(row['address'])[:110]}")
            if row.get("document_number"):
                lines.append(f"   🆔 {row['document_number']}")
    if combo_rows:
        lines.append(f"🔑 LEAKED CREDENTIALS ({len(combo_rows)}):")
        for row in combo_rows[:5]:
            lines.append(f" • {row.get('link') or row.get('domain') or 'source'} → "
                         f"{row.get('password_masked') or 'NA'}")
    if not leak_rows and not combo_rows:
        lines.append("❌ Is email ka koi leak record nahi mila.")
    lines.append("────────────────────────")
    lines.append(f"📶 Live data · {', '.join(sources) or 'dns+gravatar'} · {now_ist('%d-%m-%Y %H:%M')}")
    if hibp_note:
        lines.append(hibp_note)

    return {
        "success": True,
        "email": email,
        "valid_format": valid_format,
        "domain": domain,
        "provider": provider,
        "disposable": disposable,
        "role_account": role_account,
        "mx_records": mx_records,
        "spf": spf,
        "gravatar": {"found": gravatar_found, "url": gravatar_url if gravatar_found else None},
        "hibp_breaches": breaches,
        "identity_records": leak_rows,
        "credential_records": combo_rows,
        "sources_used": sources,
        "note": hibp_note,
        "response_time": f"{round(time.time() - started, 2)}s",
        "timestamp_ist": now_ist("%d-%m-%Y %H:%M:%S"),
        "formatted": "\n".join(lines),
    }, False


def _mask(value: str) -> str:
    value = str(value or "")
    if len(value) <= 2:
        return "*" * len(value)
    return value[0] + "*" * (len(value) - 2) + value[-1]


async def native_pass_check(params: Dict[str, Any], request: Request) -> Tuple[Optional[Dict], bool]:
    """Password breach check via Pwned Passwords (k-anonymity - password never leaves server)."""
    password = params.get("password") or params.get("pass") or params.get("q") or ""
    if not password:
        return None, True
    sha1 = hashlib.sha1(password.encode("utf-8")).hexdigest().upper()
    prefix, suffix = sha1[:5], sha1[5:]
    text, err = None, None
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True,
                                     headers={"User-Agent": UA,
                                              "Add-Padding": "true"}) as client:
            resp = await client.get(f"https://api.pwnedpasswords.com/range/{prefix}")
            text = resp.text
    except Exception as exc:
        err = str(exc)
    count = 0
    if text:
        for line in text.splitlines():
            if ":" in line and line.split(":")[0].upper() == suffix:
                try:
                    count = int(line.split(":")[1].strip())
                except Exception:
                    count = 1
                break
    lines = [f"🔑 PASSWORD CHECK — {_mask(password)}", "━━━━━━━━━━━━━━━━━━━━━━"]
    lines.append(f"🚨 Status: {'LEAKED ❌' if count else 'Not found in known breaches ✅'}")
    if count:
        lines.append(f"📊 Kitni baar mila: {count:,} breaches/combo lists me")
        lines.append("💡 Ise turant change kar do (aur kahin same password use na karo).")
    else:
        lines.append("💡 Ye password known leak lists me nahi mila - phir bhi strong + unique rakho.")
    lines.append("────────────────────────")
    lines.append(f"🔒 SHA1 prefix: {prefix}… (password kabhi server se bahar nahi gaya)")
    lines.append(f"📶 Live data · pwnedpasswords.com · {now_ist('%d-%m-%Y %H:%M')}")
    return {
        "success": True,
        "leaked": bool(count),
        "breach_count": count,
        "password_masked": _mask(password),
        "sha1_prefix": prefix,
        "length": len(password),
        "strength_hint": ("weak" if len(password) < 8 else ("medium" if len(password) < 12 else "strong")),
        "source": "api.pwnedpasswords.com (k-anonymity)",
        "error": err,
        "formatted": "\n".join(lines),
    }, False


# ---------------------------------------------------------------------
# AADHAAR FAMILY INTEL
# ---------------------------------------------------------------------
VERHOEFF_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
    [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8],
    [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2],
    [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
    [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
VERHOEFF_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
    [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0],
    [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5],
    [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]


def verhoeff_valid(number: str) -> bool:
    if not number.isdigit():
        return False
    c = 0
    for i, ch in enumerate(reversed(number)):
        c = VERHOEFF_D[c][VERHOEFF_P[i % 8][int(ch)]]
    return c == 0


def mask_aadhaar(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) < 4:
        return "XXXXXXXX" + digits
    return "XXXXXXXX" + digits[-4:]


INDIAN_STATES = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa", "Gujarat",
    "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala", "Madhya Pradesh",
    "Maharashtra", "Manipur", "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Punjab",
    "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana", "Tripura", "Uttar Pradesh",
    "Uttarakhand", "West Bengal", "Jammu and Kashmir", "Jammu & Kashmir", "Ladakh",
    "Delhi", "Puducherry", "Chandigarh", "Andaman and Nicobar Islands",
    "Dadra and Nagar Haveli and Daman and Diu", "Lakshadweep",
]


def parse_location(address: str) -> Dict[str, str]:
    addr = (address or "")
    low = addr.lower()
    state = ""
    for st in INDIAN_STATES:
        if st.lower() in low:
            state = st.upper()
            break
    district = ""
    parts = [p.strip() for p in re.split(r"[,]", addr) if p.strip()]
    for i, part in enumerate(parts):
        if state and state.lower() in part.lower():
            prev = parts[i - 1] if i > 0 else ""
            prev = re.sub(r"\b\d{6}\b", "", prev).strip(" .,-")
            if prev and not prev.isdigit():
                district = prev.upper()
            break
    if not district:
        for part in reversed(parts):
            clean = re.sub(r"\b\d{6}\b", "", part).strip(" .,-")
            if clean and not clean.isdigit() and len(clean) > 3 and (
                    not state or state.lower() not in clean.lower()):
                district = clean.upper()
                break
    return {"district": district, "state": state, "pincode": (re.findall(r"\b\d{6}\b", addr) or [""])[0]}


def format_aadhaar_card(payload: Dict[str, Any]) -> str:
    """Bot-ke-liye ready Hinglish card."""
    loc = payload.get("location") or {}
    members = payload.get("members") or []
    city = (loc.get("city") or loc.get("district") or loc.get("town") or "NA").upper()
    state = (loc.get("state") or "NA").upper()
    lines = [
        "╔══════════════════════════════════════╗",
        "║      📜 AADHAAR FAMILY INTEL          ║",
        "╚══════════════════════════════════════╝",
        "",
        f"💳 Aadhaar: {payload.get('aadhaar_masked', 'XXXXXXXXXXXX')}",
        f"✔️ Verhoeff check: {'VALID ✅' if payload.get('aadhaar_valid_checksum') else 'INVALID ❌'}",
        f"🎫 Ration Card Number: {payload.get('ration_card_number', 'NA')}",
        f"🏪 FPS ID: {payload.get('fps_id', 'NA')}",
        "",
        f"👨‍👩‍👧‍👦 FAMILY MEMBERS ({len(members)})",
    ]
    if members:
        for idx, member in enumerate(members, 1):
            name = member.get("name") or "Name not in source"
            if member.get("is_head"):
                name += "  👑 (Head)"
            lines.append(f"{idx}. {name} — Aadhaar: {member.get('aadhaar_masked', 'XXXXXXXXXXXX')}")
    else:
        lines.append("❌ Koi family member nahi mila.")
    lines += [
        "",
        "📍 LOCATION DETAILS",
        f"🏙️ {city} ({state})",
        f"📮 Pincode: {loc.get('pincode') or 'NA'}",
        "",
        "⏱️ " + str(payload.get("response_time", "-")),
        "🔗 API by @Supermannn_x",
    ]
    return "\n".join(lines)



async def native_aadhaar_family(params: Dict[str, Any], request: Request) -> Tuple[Optional[Dict], bool]:
    """12-digit Aadhaar/UID daalo → us parivar ke members + location."""
    started = time.time()
    raw = (params.get("aadhaar") or params.get("uid") or params.get("q")
           or params.get("ration") or "").strip()
    aadhaar = re.sub(r"\D", "", raw)
    if len(aadhaar) != 12:
        return {"success": False, "error": "Aadhaar must be exactly 12 digits.",
                "input": raw,
                "formatted": ("╔══════════════════════════════════════╗\n"
                              "║       📜 AADHAAR FAMILY INTEL        ║\n"
                              "╚══════════════════════════════════════╝\n\n"
                              "❌ 12 digit ka Aadhaar number daalo. Example: /api/aadhaar-family?key=Demo&aadhaar=000000000000"),
                "_no_cache": True}, True

    deadline = float(get_setting("max_request_seconds", "50") or 50)
    records: List[Tuple[Dict[str, Any], str]] = []
    sources: List[str] = []

    # 1) own database (ration / aadhaar records aap khud daal sakte ho)
    for category in ("aadhaar", "ration", "leak", "general"):
        for rec in custom_lookup(category, aadhaar):
            records.append((rec, f"own-db:{category}"))
            if f"own-db:{category}" not in sources:
                sources.append(f"own-db:{category}")

    # 2) upstream leak / number databases (Aadhaar document number se search)
    if not records or str(params.get("deep", "1")).lower() in ("1", "true", "yes"):
        recs, used = await _collect_leak_records(aadhaar, sources=("num-info", "leak-v1", "leak-v2"),
                                                 timeout=30, deadline_seconds=deadline * 0.55)
        records.extend(recs)
        sources.extend([s for s in used if s not in sources])

    people = merge_people(records, "")
    if not people:
        return {"success": False, "query": aadhaar, "aadhaar_masked": mask_aadhaar(aadhaar),
                "record_count": 0, "members": [],
                "error": "Is Aadhaar number ke liye koi record nahi mila.",
                "sources_used": sources,
                "formatted": ("╔══════════════════════════════════════╗\n"
                              "║       📜 AADHAAR FAMILY INTEL        ║\n"
                              "╚══════════════════════════════════════╝\n\n"
                              f"💳 Aadhaar: {mask_aadhaar(aadhaar)}\n\n"
                              "❌ Koi record nahi mila.\n"
                              "💡 Dashboard → Database me apna ration/aadhaar data add kar sakte ho "
                              "(category: aadhaar/ration)."),
                "_no_cache": True}, True

    primary = people[0]
    p_tokens: set = set()
    for addr in primary.get("addresses") or []:
        p_tokens |= addr_tokens(addr)
    p_father = (primary.get("father_name") or "").lower()
    p_name = (primary.get("name") or "").lower()

    # 3) family expansion (same father / same address / locality words)
    expanded: List[Tuple[Dict[str, Any], str]] = []
    deep = str(params.get("deep", "1")).lower() in ("1", "true", "yes")
    if deep:
        terms: List[str] = []
        if len(p_father) >= 8 and " s/o" not in p_father:
            terms.append(primary["father_name"])
        terms.extend(locality_terms(primary, limit=3))
        # ghar ke phone numbers se bhi search - aksar poora parivar mil jata hai
        for ph in (primary.get("phones") or [])[:2]:
            if ph and ph not in terms:
                terms.append(ph)
        terms = list(dict.fromkeys([t for t in terms if t]))[:5]

        left = deadline - (time.time() - started)
        if left > 6 and terms:
            per = int(min(25, max(6, (left * 0.8) / len(terms))))
            results = await asyncio.gather(*[
                _collect_leak_records(
                    t,
                    sources=("num-info", "leak-v1") if str(t).isdigit() else ("leak-v1",),
                    timeout=per, deadline_seconds=left * 0.9)
                for t in terms], return_exceptions=True)
            for res in results:
                if isinstance(res, BaseException):
                    continue
                recs, used = res
                expanded.extend(recs)
                sources.extend([u for u in used if u not in sources])

    all_people = merge_people(records + expanded, "")
    members: List[Dict[str, Any]] = []
    for person in all_people:
        name = (person.get("name") or "").lower()
        father = (person.get("father_name") or "").lower()
        tokens: set = set()
        for addr in person.get("addresses") or []:
            tokens |= addr_tokens(addr)
        shared = p_tokens & tokens
        same_father = bool(p_father and father == p_father)
        is_child = bool(p_name and father == p_name)
        same_person = (name == p_name and father == p_father)
        if not same_person and (len(shared) >= 2 or (same_father and len(shared) >= 1)
                                or (is_child and len(shared) >= 1)):
            if not name and len(shared) < 3:
                continue
            rel = ("child / dependent" if is_child else
                   "possible sibling (same father)" if same_father else
                   "same address / locality")
            members.append({
                "name": _title_name(person.get("name") or "") or _no_name_label(person),
                "aadhaar_masked": mask_aadhaar((person.get("govt_ids") or [""])[0]),
                "relation": rel,
                "father_name": person.get("father_name") or "",
                "phones": person.get("phones") or [],
                "address": (person.get("addresses") or [""])[0],
                "match_score": len(shared) + (3 if same_father else 0),
            })
    members.sort(key=lambda m: -m.get("match_score", 0))

    # always keep the searched person on top
    members = [{
        "name": _title_name(primary.get("name") or "") or "Name not in source",
        "aadhaar_masked": mask_aadhaar(aadhaar),
        "relation": "searched Aadhaar holder",
        "father_name": primary.get("father_name") or "",
        "phones": primary.get("phones") or [],
        "address": (primary.get("addresses") or [""])[0],
        "match_score": 999,
    }] + members[:12]

    # head of family: jo naam sabse zyado ke father field me aaye
    father_counts: Dict[str, int] = {}
    for m in members:
        f = (m.get("father_name") or "").strip().lower()
        if f:
            father_counts[f] = father_counts.get(f, 0) + 1
    head_name = ""
    if father_counts:
        head_name = max(father_counts.items(), key=lambda kv: kv[1])[0]
    for m in members:
        m["is_head"] = bool(head_name and (m.get("name") or "").strip().lower() == head_name)

    location = parse_location((primary.get("addresses") or [""])[0])
    ration_card = "NA"
    fps_id = "NA"
    for m in members:
        pass
    for rec, src in records:
        for key in ("ration_card", "ration_card_number", "rc_number", "ration"):
            if rec.get(key):
                ration_card = str(rec[key])
        for key in ("fps_id", "fps", "fps_code", "shop_id"):
            if rec.get(key):
                fps_id = str(rec[key])

    payload = {
        "success": True,
        "aadhaar_masked": mask_aadhaar(aadhaar),
        "aadhaar_valid_checksum": verhoeff_valid(aadhaar),
        "ration_card_number": ration_card,
        "fps_id": fps_id,
        "primary": primary,
        "members": members,
        "member_count": len(members),
        "location": location,
        "sources_used": sources,
        "response_time": f"{round(time.time() - started, 2)}s",
        "timestamp_ist": now_ist("%d-%m-%Y %H:%M:%S"),
        "note": ("Aadhaar numbers hamesha MASKED hote hain (sirf last 4 digit). "
                 "Ration card / FPS ID tabhi aata hai jab source me ho "
                 "(apna data Database tab se add kar sakte ho)."),
    }
    payload["formatted"] = format_aadhaar_card(payload)
    return payload, False

# 🐛 v2.2.1 FIX: ye teen naam code me use ho rahe the par KABHI define nahi hue the
# (_INVIDIOUS_CACHE / INVIDIOUS_INSTANCES / PIPED_INSTANCES → NameError, isliye Invidious
# aur Piped fallback bilkul dead the). Ab define kar diye.
_INVIDIOUS_CACHE: Dict[str, Any] = {"at": 0.0, "list": []}
INVIDIOUS_INSTANCES: List[str] = [
    "https://inv.nadeko.net", "https://invidious.nerdvpn.de", "https://yewtu.be",
    "https://invidious.f5.si", "https://iv.melmac.space", "https://invidious.privacyredirect.com",
]
PIPED_INSTANCES: List[str] = [
    "https://pipedapi.kavin.rocks", "https://api.piped.private.coffee",
    "https://pipedapi.adminforge.de", "https://pipedapi.reallyaweso.me",
]


def _invidious_instance_list() -> List[str]:
    """Public Invidious instance list (30 min cache) + hardcoded backup."""
    import httpx

    if time.time() - _INVIDIOUS_CACHE["at"] < 1800 and _INVIDIOUS_CACHE["list"]:
        return _INVIDIOUS_CACHE["list"]
    found: List[str] = []
    try:
        r = httpx.get("https://api.invidious.io/instances.json", timeout=5,
                      follow_redirects=True)
        if r.status_code == 200:
            for _name, meta in (r.json() or {}):
                uri = str((meta or {}).get("uri") or "").rstrip("/")
                if (meta or {}).get("api") and uri.startswith("https://"):
                    found.append(uri)
    except Exception:  # noqa: BLE001
        pass
    ordered: List[str] = []
    for u in found + INVIDIOUS_INSTANCES:
        if u not in ordered:
            ordered.append(u)
    _INVIDIOUS_CACHE.update({"at": time.time(), "list": ordered})
    return ordered


def _piped_streams(vid: str, mode: str, quality: str, tries: int = 3,
                   per_timeout: float = 8.0) -> Dict[str, Any]:
    """Piped API se direct video/audio links (blocking, thread me chalega)."""
    import httpx

    tried: List[str] = []
    for base in PIPED_INSTANCES[:tries]:
        tried.append(base)
        try:
            r = httpx.get(f"{base}/streams/{vid}", timeout=per_timeout,
                          headers={"User-Agent": "Mozilla/5.0"},
                          follow_redirects=True)
            if r.status_code != 200:
                continue
            d = r.json()
            links: List[Dict[str, Any]] = []
            if mode in ("video", "both"):
                for s in (d.get("videoStreams") or [])[:6]:
                    if s.get("url"):
                        links.append({"type": "video", "provider": "piped",
                                      "quality": s.get("quality") or "",
                                      "ext": (s.get("mimeType") or "").split("/")[-1] or "mp4",
                                      "url": s.get("url")})
            if mode in ("audio", "both") or not links:
                for s in (d.get("audioStreams") or [])[:4]:
                    if s.get("url"):
                        links.append({"type": "audio", "provider": "piped",
                                      "quality": f"{s.get('bitrate') or ''}kbps".replace("kbpskbps", "kbps"),
                                      "ext": (s.get("mimeType") or "").split("/")[-1] or "m4a",
                                      "url": s.get("url")})
            if links:
                if quality and quality.isdigit():
                    vids = [l for l in links if l["type"] == "video"]
                    if vids:
                        try:
                            want = int(quality)
                            vids.sort(key=lambda l: abs(int(str(l["quality"]).replace("p", "") or 0) - want)
                                      if str(l["quality"]).replace("p", "").isdigit() else 9999)
                            best = vids[0]
                            links = [best] + [l for l in links if l["type"] == "audio"][:1]
                        except Exception:  # noqa: BLE001
                            pass
                return {"video_id": vid, "title": d.get("title"),
                        "channel": d.get("uploader"),
                        "duration": d.get("duration"),
                        "thumbnail": d.get("thumbnail"),
                        "links": links, "provider": "piped", "tried": tried}
        except Exception:  # noqa: BLE001
            continue
    return {"links": [], "tried": tried}


def _invidious_streams(vid: str, mode: str, quality: str = "", tries: int = 3,
                       per_timeout: float = 8.0) -> Dict[str, Any]:
    """Invidious API se direct links (blocking fallback)."""
    import httpx

    tried: List[str] = []
    errors: List[str] = []
    for base in _invidious_instance_list()[:tries]:
        tried.append(base)
        try:
            # local=true -> stream Invidious instance ke through proxy hota hai,
            # isliye link kisi bhi device se chal jata hai (googlevideo direct links
            # instance ke IP se bandhe hote hain).
            r = httpx.get(f"{base}/api/v1/videos/{vid}?local=true", timeout=per_timeout,
                          headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True)
            if r.status_code != 200:
                continue
            d = r.json()
            links: List[Dict[str, Any]] = []
            fmts = (d.get("adaptiveFormats") or []) + (d.get("formatStreams") or [])
            for s in fmts:
                u = s.get("url")
                if not u:
                    continue
                t = (s.get("type") or "")
                is_audio = "audio" in t
                if mode == "audio" and not is_audio:
                    continue
                if mode == "video" and is_audio and not any(l["type"] == "video" for l in links):
                    links.append({"type": "audio", "provider": "invidious",
                                  "quality": f"{s.get('bitrate') or ''}kbps",
                                  "ext": s.get("container") or ("m4a" if is_audio else "mp4"),
                                  "url": u})
                    continue
                if is_audio:
                    links.append({"type": "audio", "provider": "invidious",
                                  "quality": f"{s.get('bitrate') or ''}kbps",
                                  "ext": s.get("container") or "m4a", "url": u})
                else:
                    links.append({"type": "video", "provider": "invidious",
                                  "quality": s.get("qualityLabel") or s.get("quality") or "",
                                  "ext": s.get("container") or "mp4", "url": u,
                                  "note": "proxy link - browser me kholein"})
            if links:
                vids = [l for l in links if l["type"] == "video"]
                if mode != "audio" and vids:
                    want_h = int(quality) if quality.isdigit() else 720
                    mp4 = [l for l in vids if (l.get("ext") or "") == "mp4"] or vids

                    def _h(l):
                        q = str(l.get("quality") or "").replace("p", "")
                        return int(q) if q.isdigit() else 0

                    mp4.sort(key=lambda l: (-_h(l), abs(_h(l) - want_h)))
                    best = mp4[0]
                    # 1080p se upar ki jagah desired height ke sabse kareeb wala
                    if _h(best) > 1080:
                        cand = [l for l in mp4 if _h(l) <= 1080]
                        if cand:
                            best = cand[0]
                    def _aud_key(l):
                        br = int(str(l.get("quality") or "").replace("kbps", "") or 0)
                        pref = 0 if (l.get("ext") or "") in ("m4a", "mp4") else 1
                        return (pref, -br)

                    aud = sorted([l for l in links if l["type"] == "audio"], key=_aud_key)
                    links = [best] + aud[:1]
                else:
                    def _aud_key2(l):
                        br = int(str(l.get("quality") or "").replace("kbps", "") or 0)
                        return (0 if (l.get("ext") or "") in ("m4a", "mp4") else 1, -br)

                    aud = sorted([l for l in links if l["type"] == "audio"], key=_aud_key2)
                    links = aud[:1] or links[:1]
                return {"video_id": vid, "title": d.get("title"),
                        "channel": d.get("author"), "duration": d.get("lengthSeconds"),
                        "thumbnail": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                        "links": links, "provider": "invidious-proxy", "tried": tried}
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{base}: {type(exc).__name__} {str(exc)[:80]}")
            continue
    return {"links": [], "tried": tried, "errors": errors}


# ---------------------------------------------------------------------
# v2.3 — PUBLIC DOWNLOADER PROVIDERS (Render IP block ka permanent ilaaj)
# Ye services apne server par YouTube extract karti hain aur CDN link deti hain,
# isliye link kisi bhi IP se chalta hai (googlevideo links IP-locked hote hain).
# ---------------------------------------------------------------------
PUBLIC_YT_PROVIDERS = (
    "https://apis.davidcyriltech.my.id",      # savetube CDN — video + mp3
)


async def _provider_savetube(vid: str, watch: str, mode: str, budget: float) -> Dict[str, Any]:
    import httpx
    out: Dict[str, Any] = {"links": [], "tried": ["savetube"], "errors": [], "title": ""}
    paths = [("/download/ytmp4", "video"), ("/download/ytmp3", "audio")]
    if mode == "audio":
        paths = [("/download/ytmp3", "audio")]
    elif mode == "both":
        paths = [("/download/ytmp4", "video"), ("/download/ytmp3", "audio")]
    try:
        async with httpx.AsyncClient(timeout=max(8.0, min(25.0, budget)), follow_redirects=True,
                                     headers={"User-Agent": UA}) as client:
            for path, kind in paths:
                try:
                    r = await client.get(PUBLIC_YT_PROVIDERS[0] + path, params={"url": watch})
                    if r.status_code != 200:
                        out["errors"].append(f"{path}: HTTP {r.status_code}")
                        continue
                    data = r.json()
                    res = data.get("result") or {}
                    url = str(res.get("download_url") or res.get("url") or "")
                    if not url.startswith("http"):
                        out["errors"].append(f"{path}: no url")
                        continue
                    if res.get("title") and not out["title"]:
                        out["title"] = str(res["title"])
                    out["links"].append({
                        "type": kind,
                        "provider": "savetube",
                        "quality": str(res.get("quality") or ("audio" if kind == "audio" else "video")),
                        "ext": str(res.get("format") or ("mp3" if kind == "audio" else "mp4")),
                        "url": url,
                    })
                except Exception as exc:  # noqa: BLE001
                    out["errors"].append(f"{path}: {str(exc)[:80]}")
    except Exception as exc:  # noqa: BLE001
        out["errors"].append(str(exc)[:100])
    return out


async def _provider_loaderto(vid: str, watch: str, mode: str, budget: float,
                             quality: str = "") -> Dict[str, Any]:
    """loader.to — asli HD link (1080p FHD mp4) deta hai, thoda slow (~15-20s) par original quality.

    verified: format=1080 → 1920x1080 h264 mp4 (savenow.to CDN, bina referer download).
    """
    import asyncio as _aio
    import httpx
    out: Dict[str, Any] = {"links": [], "tried": ["loader.to"], "errors": [], "title": ""}
    q = str(quality or "").strip().lower().replace("p", "")
    if q in ("", "best", "high", "max", "hd", "full", "original", "1080"):
        fmt = "1080"
    elif q in ("720", "480", "360", "240"):
        fmt = q
    else:
        fmt = "1080"
    if mode == "audio":
        fmt = "mp3"
    tiers = [fmt]
    if fmt == "1080":                      # 1080 fail ho to 720 try karo
        tiers.append("720")
    try:
        async with httpx.AsyncClient(timeout=max(10.0, min(30.0, budget)), follow_redirects=True,
                                     headers={"User-Agent": UA}) as client:
            # ⏱️ v2.4.1: hard deadline (pehle 2 tiers milkar 97s kha jate the!)
            _end = time.monotonic() + max(9.0, float(budget))
            for t_i, t_fmt in enumerate(tiers):
                if t_i and (time.monotonic() + 7) > _end:
                    break
                try:
                    r = await client.get("https://loader.to/ajax/download.php",
                                         params={"format": t_fmt, "url": watch})
                    if r.status_code != 200:
                        out["errors"].append(f"{t_fmt}: start HTTP {r.status_code}")
                        continue
                    start = r.json()
                    pid = start.get("id")
                    if start.get("title") and not out["title"]:
                        out["title"] = str(start["title"])
                    if not pid:
                        out["errors"].append(f"{t_fmt}: no id")
                        continue
                    got = ""
                    # 🐛 progress_url (lto2.affadaffa.com) "Id not found" deta hai —
                    # progress.php hi asli endpoint hai (verified 3 Oct 2026).
                    while time.monotonic() < _end:
                        await _aio.sleep(3)
                        try:
                            p = await client.get("https://loader.to/ajax/progress.php",
                                                 params={"id": pid})
                            pj = p.json()
                        except Exception:  # noqa: BLE001
                            continue
                        u = str(pj.get("download_url") or "")
                        if u.startswith("http"):
                            got = u
                            break
                    if not got:
                        out["errors"].append(f"{t_fmt}: timeout")
                        continue
                    out["links"].append({
                        "type": "audio" if t_fmt == "mp3" else "video",
                        "provider": "loader.to",
                        "quality": "audio" if t_fmt == "mp3" else f"{t_fmt}p",
                        "hd": t_fmt == "1080",
                        "ext": "mp3" if t_fmt == "mp3" else "mp4",
                        "url": got,
                    })
                    if t_fmt != "1080":
                        break
                    break
                except Exception as exc:  # noqa: BLE001
                    out["errors"].append(f"{t_fmt}: {str(exc)[:80]}")
    except Exception as exc:  # noqa: BLE001
        out["errors"].append(str(exc)[:100])
    return out


# 🐛 v2.2 FIX: ye dict pehle code me USE hoti thi par DEFINE kabhi nahi hui thi
# (_YT_STATE["ytdlp_fail_until"] → NameError → /youtube-download 502). Ab define hai.
_YT_STATE: Dict[str, float] = {"ytdlp_fail_until": 0.0}


def _yt_extract(url: str, mode: str, quality: str, timeout: int = 40,
                budget: float = 8.0) -> Dict[str, Any]:
    """Blocking yt-dlp extraction (run inside a thread).

    budget = saare client attempts ka TOTAL time (v2.3.1) — pehle pehli request par
    yt-dlp 25s kha jata tha aur providers ko time hi nahi milta tha.
    """
    try:
        import yt_dlp  # optional dependency
    except Exception as exc:
        return {"error": f"yt-dlp installed nahi hai: {exc}"}

    if mode == "audio":
        fmt = "bestaudio[ext=m4a]/bestaudio/best"
    elif quality and quality.isdigit():
        fmt = (f"bv*[height<={quality}]+ba/b[height<={quality}]/b")
    else:
        fmt = "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b"

    # 🔧 v2.2 FIX: pehle yahan hardcoded player_client list thi
    # ["tv_embedded","web_safari","mweb","web"] — wo list yt-dlp me
    # "No video formats found!" deti hai. Ab kaam karne wale clients
    # (android_vr, android) + default order me try hote hain (har try ~1s).
    base_opts = {
        "quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True,
        "format": fmt, "socket_timeout": timeout, "nocheckcertificate": True,
        "source_address": None, "geo_bypass": True, "retries": 2, "fragment_retries": 2,
        "http_headers": {
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
            "Accept-Language": "en-US,en;q=0.9",
        },
    }
    ytdlp_errs = []
    info = None
    _t0 = time.time()
    # default clients sabse zyada formats dete hain (24 tk), phir android_vr/android
    # (combined single file — kam quality par hamesha chalta hai)
    for clients in ([], ["android_vr"], ["android"], ["web"], ["ios"]):
        if time.time() - _t0 > budget:
            ytdlp_errs.append("budget khatam (blocked IP lagta hai)")
            break
        opts = dict(base_opts)
        if clients:
            opts["extractor_args"] = {"youtube": {"player_client": clients}}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
            if info and (info.get("formats") or info.get("url")):
                break
            info = None
        except Exception as exc:
            ytdlp_errs.append(f"{'/'.join(clients) or 'default'}: {str(exc)[:90]}")
            info = None
    if info is None:
        return {"error": "yt-dlp: " + (" | ".join(ytdlp_errs[-2:]) or "no formats found")}

    links: List[Dict[str, Any]] = []
    requested = info.get("requested_formats") or []
    if requested:
        for f in requested:
            links.append({
                "type": "video" if f.get("vcodec") not in (None, "none") else "audio",
                "format_id": f.get("format_id"),
                "ext": f.get("ext"),
                "quality": f"{f.get('height') or ''}p".replace("p", "p") if f.get("height") else
                           (str(f.get("abr") or "") + "kbps" if f.get("abr") else "audio"),
                "filesize": f.get("filesize") or f.get("filesize_approx"),
                "url": f.get("url"),
            })
    elif info.get("url"):
        links.append({"type": mode if mode in ("video", "audio") else "video",
                      "format_id": info.get("format_id"), "ext": info.get("ext"),
                      "quality": f"{info.get('height') or ''}p" if info.get("height") else "best",
                      "filesize": info.get("filesize") or info.get("filesize_approx"),
                      "url": info.get("url")})
    return {
        "video_id": info.get("id"),
        "title": info.get("title"),
        "channel": info.get("channel") or info.get("uploader"),
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
        "view_count": info.get("view_count"),
        "links": links,
    }


async def native_youtube_download(params: Dict[str, Any], request: Request) -> Tuple[Optional[Dict], bool]:
    """YouTube link → direct download links (Telegram bot ke liye ready)."""
    started = time.time()
    url = (params.get("url") or params.get("link") or params.get("q")
           or params.get("id") or params.get("v") or "").strip()
    vid = yt_id(url)
    if not vid:
        return None, True
    watch = f"https://www.youtube.com/watch?v={vid}"
    mode = (params.get("type") or params.get("mode") or "video").lower()
    if str(params.get("endpoint_hint", "")) == "youtube-mp3":
        mode = "audio"
    if mode not in ("video", "audio", "both"):
        mode = "video"
    quality = str(params.get("quality") or "").strip()

    result: Dict[str, Any] = {}
    error = ""
    debug: Dict[str, Any] = {}
    deadline = float(get_setting("max_request_seconds", "50") or 50)
    # Render free plan ~25-30s me request maar deta hai, isliye fallback chain tight rakhi hai
    # v2.4: bot 75s tak wait karta hai; 1080p mux hone me ~18-25s lagta hai
    hard_deadline = min(deadline if deadline and deadline > 30 else 55.0, 55.0)
    loop = __import__("asyncio").get_event_loop()
    # Cloud (Render) par yt-dlp aksar block hota hai aur 15-30s barbaad karta hai,
    # isliye pehle fast public APIs try karte hain; yt-dlp last me (sirf agar time bache).
    upstream_links: List[Dict[str, Any]] = []
    sources: List[str] = []

    async def _try(fn, name, kwargs):
        if result_holder.get("links"):
            return
        remaining = hard_deadline - (time.time() - started)
        if remaining < 3:
            return
        try:
            kwargs = dict(kwargs)
            if "per_timeout" in kwargs:
                kwargs["per_timeout"] = max(2.5, min(kwargs["per_timeout"], remaining / 3))
            out = await loop.run_in_executor(None, lambda: fn(**kwargs))
            dbg = {"links": len(out.get("links") or []), "tried": out.get("tried", [])}
            if out.get("errors"):
                dbg["errors"] = out["errors"][:3]
            debug[name] = dbg
            if out.get("links"):
                result_holder.update(out)
                sources.append(name)
        except Exception as exc:  # noqa: BLE001
            debug[name] = {"error": str(exc)[:120]}

    result_holder: Dict[str, Any] = {}

    # 🔧 v2.2 FIX: pehle Invidious/Piped (dead instances) 13-19s kha jate the aur
    # yt-dlp ko mauka hi nahi milta tha. Ab yt-dlp PEHLE (fix hone ke baad ~1-3s).
    if (hard_deadline - (time.time() - started)) > 5:
        ytdlp_skipped = time.time() < _YT_STATE["ytdlp_fail_until"]
        if ytdlp_skipped:
            debug["yt_dlp"] = {"links": 0, "error": None, "skipped_recent_failure": True}
        else:
            try:
                out = await loop.run_in_executor(
                    None, lambda: _yt_extract(watch, "audio" if mode == "audio" else mode,
                                              quality,
                                              timeout=int(min(25, max(8, hard_deadline - (time.time() - started) - 4))),
                                              budget=float(min(6, max(4, hard_deadline - (time.time() - started) - 10)))))
            except Exception as exc:  # noqa: BLE001
                out = {"error": str(exc)[:200]}
            ytdlp_err = out.get("error")
            debug["yt_dlp"] = {"links": len(out.get("links") or []), "error": ytdlp_err,
                               "skipped_recent_failure": False}
            if out.get("links"):
                result_holder.update(out)
                sources.append("yt-dlp")
            elif ytdlp_err:
                _YT_STATE["ytdlp_fail_until"] = time.time() + 300
    # 🆕 v2.3 — 1.5) PUBLIC PROVIDERS: Render jaise blocked IP se bhi kaam karte hain
    if not result_holder.get("links"):
        _left = hard_deadline - (time.time() - started)
        if _left > 8:
            # v2.4: VIDEO ke liye loader.to (1080p) PEHLE — savetube sirf 480p deta hai (fallback).
            # AUDIO ke liye savetube (fast mp3) pehle, phir loader.to.
            if mode == "audio":
                _order = [_provider_savetube, _provider_loaderto]
            else:
                _order = [_provider_loaderto, _provider_savetube]
            _collected: List[Dict[str, Any]] = []
            _ptitle = ""
            for _pf in _order:
                _left = hard_deadline - (time.time() - started)
                if _left < 8:
                    break
                try:
                    _pbudget = (_left - 8.0) if _pf is _provider_loaderto else _left
                    _pout = await loop.run_in_executor(
                        None, lambda: __import__("asyncio").run(
                            _pf(vid, watch, mode, max(9.0, _pbudget), quality) if _pf is _provider_loaderto
                            else _pf(vid, watch, mode, _left)))
                except Exception as _pexc:  # noqa: BLE001
                    debug[_pf.__name__] = {"error": str(_pexc)[:120]}
                    continue
                debug[_pf.__name__] = {"links": len(_pout.get("links") or []),
                                       "errors": (_pout.get("errors") or [])[:2]}
                if _pout.get("links"):
                    if _pout.get("title") and not _ptitle:
                        _ptitle = str(_pout["title"])
                    _collected.extend(_pout["links"])
                    sources.append(_pout["tried"][0])
                    # 1080p mil gaya → extra provider try karke time barbaad na karo
                    if any(l.get("hd") for l in _pout["links"]):
                        break
                    if mode == "audio" and any(l.get("type") == "audio" for l in _pout["links"]):
                        break          # mp3 mil gaya (savetube fast) — loader.to ki zarurat nahi
                    if mode == "video" and any(l.get("type") == "video" for l in _pout["links"]):
                        break
            # v2.4.1: HD mil gaya ho to bhi ek halka 480p backup link rakho
            # (1080p file >45MB ho sakti hai — Telegram bot limit 50MB; bot fallback use karega)
            if mode != "audio" and any(l.get("hd") for l in _collected) and \
                    (hard_deadline - (time.time() - started)) > 9:
                try:
                    _bout = await loop.run_in_executor(
                        None, lambda: __import__("asyncio").run(
                            _provider_savetube(vid, watch, "video", 9.0)))
                    for _bl in (_bout.get("links") or []):
                        if _bl.get("type") == "video":
                            _bl["backup"] = True
                            _collected.append(_bl)
                    if _bout.get("links"):
                        debug["savetube_backup"] = {"links": len(_bout["links"])}
                except Exception as _bexc:  # noqa: BLE001
                    debug["savetube_backup"] = {"error": str(_bexc)[:80]}
            if _collected:
                _vl = [l for l in _collected if l.get("type") == "video"]
                _al = [l for l in _collected if l.get("type") == "audio"]
                _best = sorted(_vl, key=lambda l: int(str(l.get("quality") or "0").replace("p", "") or 0),
                               reverse=True)
                result_holder.update({"video_id": vid, "title": _ptitle or "",
                                      "links": _best + _al,
                                      "thumbnail": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"})
    # 2) Invidious (proxied links)
    if not result_holder.get("links") and (hard_deadline - (time.time() - started)) > 8:
        await _try(_invidious_streams, "invidious",
                   {"vid": vid, "mode": "audio" if mode == "audio" else mode,
                    "quality": quality, "tries": 2, "per_timeout": 6.0})
    # 3) Piped
    if not result_holder.get("links") and (hard_deadline - (time.time() - started)) > 6:
        await _try(_piped_streams, "piped",
                   {"vid": vid, "mode": "audio" if mode == "audio" else mode,
                    "quality": quality, "tries": 2, "per_timeout": 5.0})
    # 4) upstream YouTube metadata/links (aakhri koshish)
    if not result_holder.get("links") and (time.time() - started) < hard_deadline * 0.9:
        up, _ = await upstream_call("youtube-all", {"url": watch},
                                    timeout=int(max(8, hard_deadline - (time.time() - started) - 2)),
                                    retries=0)
        if isinstance(up, dict):
            sources.append("youtube-all")
            dl = up.get("download_links") or {}
            for provider, items in dl.items():
                if isinstance(items, list):
                    for item in items:
                        if isinstance(item, dict) and item.get("url"):
                            upstream_links.append({
                                "type": "video", "provider": provider,
                                "quality": item.get("quality") or item.get("format") or "",
                                "url": item.get("url")})
            result_holder.update({
                "video_id": up.get("video_id") or vid,
                "title": ((up.get("video_info") or {}) or {}).get("title"),
                "channel": ((up.get("channel_info") or {}) or {}).get("title"),
                "duration": ((up.get("video_info") or {}) or {}).get("lengthSeconds"),
                "thumbnail": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                "links": upstream_links,
            })

    result = result_holder
    error = result.get("error") or ""

    all_links = (result.get("links") or []) if isinstance(result, dict) else []
    if mode != "audio":
        _vv = [l for l in all_links if l.get("type") == "video"]
        _vv.sort(key=lambda l: int(str(l.get("quality") or "0").replace("p", "") or 0), reverse=True)
        all_links = _vv + [l for l in all_links if l.get("type") != "video"]
    video_link = next((l for l in all_links if l.get("type") == "video"), None)
    audio_link = next((l for l in all_links if l.get("type") == "audio"), None)

    lines = ["╔══════════════════════════════════════╗",
             "║       ▶️ YOUTUBE DOWNLOAD LINKS      ║",
             "╚══════════════════════════════════════╝", ""]
    title = (result.get("title") if isinstance(result, dict) else "") or "NA"
    lines.append(f"🎬 {title[:70]}")
    if result.get("channel"):
        lines.append(f"📺 {result['channel'][:60]}")
    if result.get("duration"):
        lines.append(f"⏱️ {int(result['duration']) // 60}:{int(result['duration']) % 60:02d}")
    lines.append("")
    if all_links:
        for idx, l in enumerate(all_links[:6], 1):
            label = "🎥 VIDEO" if l.get("type") == "video" else "🎵 AUDIO"
            lines.append(f"{label} {idx}: {l.get('quality') or ''} · {l.get('ext') or ''}")
            lines.append(f"┗ 🔗 {str(l.get('url'))[:200]}")
        lines.append("")
        lines.append("⚠️ Ye direct links 2–6 ghante me expire ho jate hain.")
    else:
        lines.append("❌ Direct link nahi mil paya.")
        if error:
            lines.append(f"Reason: {error[:160]}")
        lines.append("💡 Server par `yt-dlp` installed hona chahiye (requirements.txt me hai).")
        lines.append("   Agar YouTube ne server IP block kiya hai to thodi der baad try karein.")
    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    # v2.2: proxy links — googlevideo direct links IP-locked hote hain, ye kisi bhi
    # device/IP se chalte hain (hub ke through stream hota hai).
    try:
        _base = str(request.base_url).rstrip("/")
        for l in all_links:
            if not l.get("url"):
                continue
            _h = urllib.parse.urlparse(str(l["url"])).hostname or ""
            # CDN links (savetube/savenow) IP-free hote hain — unhe proxy ki zaroorat nahi;
            # googlevideo links IP-locked hote hain — unke liye proxy_url banao.
            if any(_h == h or _h.endswith("." + h) for h in YDL_ALLOWED_HOSTS):
                l["proxy_url"] = (f"{_base}/api/ydl/stream?key={DEMO_KEY}"
                                  f"&url={urllib.parse.quote(str(l['url']), safe='')}")
    except Exception:
        pass

    return {
        "success": bool(all_links),
        "video_id": vid,
        "url": watch,
        "title": title,
        "channel": result.get("channel") if isinstance(result, dict) else None,
        "duration": result.get("duration") if isinstance(result, dict) else None,
        "thumbnail": result.get("thumbnail") if isinstance(result, dict) else f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
        "requested_type": mode,
        "links": all_links,
        "download_url": (video_link or audio_link or {}).get("url"),
        "audio_url": (audio_link or {}).get("url"),
        "proxy_download_url": (video_link or audio_link or {}).get("proxy_url"),
        "proxy_audio_url": (audio_link or {}).get("proxy_url"),
        "error": error or (result.get("error") if isinstance(result, dict) else None),
        "sources_used": sources or ["none"],
        "response_time": f"{round(time.time() - started, 2)}s",
        "debug": debug if str(params.get("debug", "")).lower() in ("1", "true", "yes") else None,
        "timestamp_ist": now_ist("%d-%m-%Y %H:%M:%S"),
        "note": "Direct links expire ho jate hain ~2-6 ghante me; Telegram bot me turant use karein.",
        "formatted": "\n".join(lines),
    }, not bool(all_links)


async def native_num_info_full(params: Dict[str, Any], request: Request) -> Tuple[Optional[Dict], bool]:
    """Own number-intelligence API: local DB + num-info + leak-v1/v2, merged & formatted."""
    started = time.time()
    raw_query = (params.get("q") or params.get("number") or params.get("phone")
                 or params.get("num") or params.get("query") or "").strip()
    if not raw_query:
        return None, True
    variants = phone_variants(raw_query)
    deep = str(params.get("deep", "0")).lower() in ("1", "true", "yes")
    if not deep:
        variants = variants[:2]

    collected: List[Tuple[Dict[str, Any], str]] = []
    sources: List[str] = []
    raw_payloads: Dict[str, Any] = {}
    tried: List[str] = []

    # 1) our own database first
    for variant in variants:
        for category in ("phone", "leak"):
            for rec in custom_lookup(category, variant):
                collected.append((rec, f"own-db:{category}"))
                if f"own-db:{category}" not in sources:
                    sources.append(f"own-db:{category}")

    # 2) upstream sources — SAB EK SAATH (parallel), warna har query 25-30s leti thi
    deadline = float(get_setting("max_request_seconds", "50") or 50)
    paths = ("num-info", "leak-v1", "leak-v2") if deep else ("num-info", "leak-v1")

    for variant in variants:
        remaining = deadline - (time.time() - started)
        if remaining < 5:
            break
        tmo = int(min(35, max(6, remaining - 2)))
        tried.extend(f"{p_}:{variant}" for p_ in paths)
        results = await asyncio.gather(
            *[upstream_call(p_, {"q": variant}, timeout=tmo, retries=0) for p_ in paths],
            return_exceptions=True)
        for source_path, res in zip(paths, results):
            if isinstance(res, BaseException):
                continue
            data = res[0] if isinstance(res, (tuple, list)) and res else None
            if not data:
                continue
            records = _norm_records(data)
            if records:
                if source_path not in sources:
                    sources.append(source_path)
                if params.get("raw") in ("1", "true", "yes"):
                    raw_payloads[f"{source_path}:{variant}"] = data
                for rec in records:
                    collected.append((rec, source_path))
        if collected and not deep:
            break

    people = merge_people(collected, variants[0] if variants else raw_query)
    elapsed = round(time.time() - started, 2)
    formatted = format_number_report(raw_query, people, sources or ["no-source"], variants)
    payload = {
        "success": bool(people),
        "query": raw_query,
        "query_variants": variants,
        "record_count": len(people),
        "people": people,
        "sources_used": sources,
        "sources_tried": tried if not people else tried[:len(tried)],
        "response_time": f"{elapsed}s",
        "timestamp_ist": now_ist("%d-%m-%Y %H:%M:%S"),
        "indian_time_stamp": f"{now_ist('%Y-%m-%d %I:%M:%S %p')} IST",
        "formatted": formatted,
    }
    if raw_payloads:
        payload["raw"] = raw_payloads
    if not people:
        payload["error"] = "No record found for this number."
        payload["_no_cache"] = True
    return payload, not bool(people)


async def native_vehicle_report(params: Dict[str, Any], request: Request) -> Tuple[Optional[Dict], bool]:
    """Own vehicle API: RC + RTO + insurance + PUC + challans, merged & formatted."""
    started = time.time()
    number = (params.get("number") or params.get("rc") or params.get("vehicle_number")
              or params.get("reg") or params.get("q") or "").strip()
    if not number:
        return None, True
    number = re.sub(r"[^A-Za-z0-9]", "", number).upper()

    sources: List[str] = []
    rc_payload: Optional[Dict[str, Any]] = None
    info_payload: Optional[Dict[str, Any]] = None
    challan_payload: Optional[Dict[str, Any]] = None
    summary_payload: Optional[Dict[str, Any]] = None

    deadline = float(get_setting("max_request_seconds", "50") or 50)

    def out_of_time() -> bool:
        return (time.time() - started) > deadline

    # 1) RC details (main source)
    rc_payload, _ = await upstream_call("vehicle-rc", {"number": number}, timeout=35, retries=0)
    if rc_payload is None:
        for fb in ("vehicle-info", "vehicle-details", "vehicle-v"):
            if out_of_time():
                break
            rc_payload, _ = await upstream_call(fb, {"number": number, "rc": number,
                                                     "vehicle_number": number}, timeout=30, retries=0)
            if rc_payload is not None:
                sources.append(fb)
                break
    else:
        sources.append("vehicle-rc")
    if isinstance(rc_payload, dict) and rc_payload.get("errorMsg"):
        rc_payload = None

    # 2) Extra make/model data
    if (not rc_payload or not (rc_payload.get("data") or {}).get("vehicle_info")) and not out_of_time():
        info_payload, _ = await upstream_call("vehicle-info", {"vehicle_number": number},
                                              timeout=25, retries=0)
        if isinstance(info_payload, dict) and not info_payload.get("errorMsg"):
            sources.append("vehicle-info")
        else:
            info_payload = None

    # 3) Challans (detailed list + summary)
    if not out_of_time():
        challan_payload, _ = await upstream_call("vehicle-challan", {"number": number},
                                                 timeout=30, retries=0)
    if isinstance(challan_payload, dict) and (challan_payload.get("data") or {}).get("challan_details"):
        sources.append("vehicle-challan")
    else:
        challan_payload = None
    if not out_of_time():
        summary_payload, _ = await upstream_call("vehicle-challan-v4", {"number": number},
                                                 timeout=30, retries=0)
    if isinstance(summary_payload, dict) and (summary_payload.get("data") or {}).get("challan_summary"):
        sources.append("vehicle-challan-v4")
    else:
        summary_payload = None

    local = parse_vehicle(number)
    # 4) own database records
    own = custom_lookup("vehicle", number.lower())

    sections = ((rc_payload or {}).get("data") or {}).get("sections") or {}
    if not sections and isinstance(rc_payload, dict):
        sections = rc_payload.get("sections") or {}
    dates = sections.get("important_dates") or {}
    ins_raw = sections.get("insurance_information") or {}
    other = sections.get("other_information") or {}
    own_sec = sections.get("ownership_details") or {}
    veh_sec = sections.get("vehicle_details") or {}
    vinfo = ((rc_payload or {}).get("data") or {}).get("vehicle_info") or {}
    if not vinfo and isinstance(rc_payload, dict):
        vinfo = rc_payload.get("vehicle_info") or {}

    maker = veh_sec.get("Model Name") or vinfo.get("model_name") or ""
    model = veh_sec.get("Maker Model") or vinfo.get("model_name") or ""
    _mm_parts: List[str] = []
    for _part in (maker, model):
        if _part and _part not in _mm_parts:
            _mm_parts.append(_part)
    maker_model = " ".join(_mm_parts)

    info_data = ((info_payload or {}).get("data") or {})
    info_owner = info_data.get("owner") if isinstance(info_data.get("owner"), dict) else {}
    owner = {
        "owner_name": (own_sec.get("Owner Name") or vinfo.get("owner_name")
                       or info_owner.get("name") or "NA"),
        "financer": other.get("Financer Name") or "NA",
        "blacklist_status": other.get("Blacklist Status") or "NA",
        "noc": other.get("NOC Details") or "NA",
        "permit_type": other.get("Permit Type") or "NA",
    }
    local_rto_label = ""
    if local.get("rto_office") and local.get("state"):
        local_rto_label = f"{local['rto_office']}, {local['state']}".upper() + " (approx, local map)"
    elif local.get("state"):
        local_rto_label = f"{local['state']}".upper() + " (approx, local map)"
    rto = {
        "registered_rto": own_sec.get("Registered RTO") or local_rto_label or "NA",
        "code": vinfo.get("code") or local.get("rto_code") or "NA",
        "city_name": vinfo.get("city_name") or (local.get("rto_office") or "NA"),
        "address": vinfo.get("address") or "NA",
        "phone": vinfo.get("phone") or "NA",
        "website": vinfo.get("website") or "NA",
        "state": local.get("state"),
    }
    rc = {
        "registration_number": own_sec.get("Registration Number") or number,
        "registration_date": dates.get("Registration Date") or "NA",
        "fitness_upto": dates.get("Fitness Upto") or "NA",
        "tax_upto": dates.get("Tax Upto") or "NA",
        "insurance_upto": dates.get("Insurance Upto") or "NA",
        "vehicle_age": dates.get("Vehicle Age") or "NA",
        "insurance_expiry_in": dates.get("Insurance Expiry In") or "NA",
    }
    insurance = {
        "company": ins_raw.get("Insurance Company") or "NA",
        "expiry": ins_raw.get("Insurance Expiry") or dates.get("Insurance Upto") or "NA",
        "status": ins_raw.get("Insurance Status") or "NA",
        "validity": ins_raw.get("Insurance Validity") or "NA",
    }
    puc = {
        "upto": dates.get("PUC Upto") or "NA",
        "status": dates.get("PUC Expiry In") or "NA",
    }

    challan_details = []
    if challan_payload:
        challan_details = ((challan_payload.get("data") or {}).get("challan_details") or [])
    summary = ((summary_payload or {}).get("data") or {}).get("challan_summary") or {}
    pending_amount, total_amount, pending_count = 0, 0, 0
    for c in challan_details:
        try:
            amt = int(float(str(c.get("amount", 0)).replace(",", "") or 0))
        except Exception:
            amt = 0
        total_amount += amt
        if "pend" in str(c.get("challan_status", "")).lower():
            pending_amount += amt
            pending_count += 1
    if summary:
        total_amount = summary.get("total_amount", total_amount)
        pending_amount = ((summary.get("type_a") or {}).get("amount", pending_amount))
        pending_count = ((summary.get("type_a") or {}).get("count", pending_count))

    report = {
        "number": number,
        "vehicle": {
            "maker": maker or "NA", "model": model or "NA", "maker_model": maker_model or "NA",
            "vehicle_class": veh_sec.get("Vehicle Class") or "NA",
            "fuel": veh_sec.get("Fuel Type") or "NA",
            "cubic_capacity": other.get("Cubic Capacity") or "NA",
            "seating_capacity": other.get("Seating Capacity") or "NA",
            "fuel_norms": veh_sec.get("Fuel Norms") or "NA",
            "vertical": (info_payload or {}).get("vertical") or local.get("vertical_guess"),
        },
        "owner": owner,
        "rto": rto,
        "rc": rc,
        "insurance": insurance,
        "puc": puc,
        "challans": {
            "count": summary.get("total_challans", len(challan_details)),
            "pending_count": pending_count,
            "pending_amount": pending_amount,
            "total_amount": total_amount,
            "disposed_count": ((summary.get("type_b") or {}).get("count", 0)),
            "list": challan_details,
        },
        "local_analysis": local,
        "custom_database_records": own,
        "sources_used": sources,
        "response_time": f"{round(time.time() - started, 2)}s",
        "timestamp_ist": now_ist("%d-%m-%Y %H:%M:%S"),
    }
    if not sources and not own:
        report["success"] = False
        report["error"] = ("Vehicle data not available right now (upstream sources down). "
                           "Offline RTO parsing is included under local_analysis.")
        report["formatted"] = format_vehicle_report(report, sources or ["offline-parse"])
        report["_no_cache"] = True
        return report, True

    report["success"] = True
    report["formatted"] = format_vehicle_report(report, sources)
    return report, False


# =====================================================================
# v2.5 — INSTAGRAM (native: web_profile_info → DDG snippet fallback)
# =====================================================================
_IG_APP_ID = "936619743392459"
_IG_CACHE: Dict[str, Dict[str, Any]] = {}


async def _ig_web_profile(username: str) -> Optional[Dict[str, Any]]:
    """Instagram ka public web_profile_info (login nahi chahiye, par IP rate-limit hota hai)."""
    import httpx
    hosts = ("https://www.instagram.com", "https://i.instagram.com")
    for host in hosts:
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True,
                                         headers={"User-Agent": UA}) as client:
                r = await client.get(f"{host}/api/v1/users/web_profile_info/",
                                     params={"username": username},
                                     headers={"x-ig-app-id": _IG_APP_ID, "Accept": "*/*",
                                              "Referer": f"https://www.instagram.com/{username}/"})
                if r.status_code != 200:
                    continue
                data = r.json().get("data") or {}
                user = data.get("user") or {}
                if user.get("username"):
                    return user
        except Exception:  # noqa: BLE001
            continue
    return None


async def _ig_ddg(username: str, _q: str = "") -> Dict[str, Any]:
    """DDG search se public profile summary (followers/following/posts + bio) — keyless fallback."""
    import httpx
    out: Dict[str, Any] = {}
    try:
        async with httpx.AsyncClient(timeout=18, follow_redirects=True,
                                     headers={"User-Agent": UA}) as client:
            r = await client.get("https://html.duckduckgo.com/html/",
                                 params={"q": (_q or f'site:instagram.com "{username}"')})
            txt = r.text
    except Exception:  # noqa: BLE001
        return out

    def _block(marker: str) -> str:
        i = txt.find(marker)
        if i < 0:
            return ""
        a = txt.find(">", i)
        b = txt.find("</a>", a)
        if a < 0 or b < 0:
            return ""
        return _clean_html(txt[a + 1:b])

    def _num_before(text: str, word: str) -> str:
        i = text.find(word)
        if i < 0:
            return ""
        j = i
        while j > 0 and (text[j - 1].isdigit() or text[j - 1] in ".," or text[j - 1] in "KMBkmb"):
            j -= 1
        val = text[j:i].strip(" .,")
        return val if val and val[0].isdigit() else ""

    t_clean = _block("result__a")
    s_clean = _block("result__snippet")

    if t_clean:
        k = t_clean.find("(@")
        if k > 0:
            out["full_name"] = t_clean[:k].strip()[:60]
            e = t_clean.find(")", k)
            handle = t_clean[k + 2:e] if e > k else ""
            if handle:
                out["username"] = handle.strip()
    if s_clean:
        f1 = _num_before(s_clean, "Followers")
        f2 = _num_before(s_clean, "Following")
        f3 = _num_before(s_clean, "Posts")
        if f1:
            out["followers"] = f1
        if f2:
            out["following"] = f2
        if f3:
            out["posts"] = f3
        bio = s_clean
        k2 = bio.find("on Instagram:")
        if k2 >= 0:
            bio = bio[k2 + len("on Instagram:"):]
        else:
            k3 = bio.find("Followers")
            if k3 >= 0:
                bio = bio[k3 + len("Followers"):]
                bio = bio.lstrip(" -–—")
        out["bio"] = bio.strip()[:160]
    return out


async def _ig_og(username: str) -> Dict[str, Any]:
    """Profile page ke OG meta tags (bot UA se) — followers/bio/photo keyless."""
    import httpx
    out: Dict[str, Any] = {}
    uas = ("Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
           "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
           UA)
    for ua in uas:
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True,
                                         headers={"User-Agent": ua, "Accept-Language": "en-US,en;q=0.9"}) as client:
                r = await client.get(f"https://www.instagram.com/{username}/")
                if r.status_code != 200 or len(r.text) < 500:
                    continue
                h = r.text

                def _meta(prop: str) -> str:
                    key = f'property="{prop}" content="'
                    i = h.find(key)
                    if i < 0:
                        key2 = 'content="' 
                        j = h.find(f'property="{prop}"')
                        if j < 0:
                            return ""
                        k = h.find(key2, j)
                        if k < 0 or k - j > 80:
                            return ""
                        e = h.find('"', k + len(key2))
                        return h[k + len(key2):e] if e > 0 else ""
                    e = h.find('"', i + len(key))
                    return h[i + len(key):e] if e > 0 else ""

                desc = _meta("og:description")
                title = _meta("og:title")
                img = _meta("og:image")
                if title:
                    out["full_name"] = title.replace("(@%s)" % username, "").replace("• Instagram photos and videos", "").strip(" -•")[:60]
                if img:
                    out["profile_pic"] = img
                if desc:
                    out["_desc"] = desc[:250]
                if out:
                    return out
        except Exception:  # noqa: BLE001
            continue
    return out


def _ig_parse_desc(desc: str, out: Dict[str, Any]) -> None:
    """'680M Followers, 649 Following, 4,138 Posts - ... Instagram' → numbers + bio (regex-free)."""
    d = desc or ""
    for word, key in (("Followers", "followers"), ("Following", "following"), ("Posts", "posts")):
        i = d.find(word)
        if i < 0:
            continue
        j = i
        while j > 0 and (d[j - 1].isdigit() or d[j - 1] in ".," or d[j - 1] in "KMBkmb"):
            j -= 1
        val = d[j:i].strip(" .,")
        if val and val[0].isdigit():
            out[key] = val
    bio = d
    for marker in ("See Instagram photos and videos from", "on Instagram:", "Instagram:"):
        k = bio.find(marker)
        if k >= 0:
            bio = bio[k + len(marker):]
            break
    bio = bio.strip(" -–—•\"'")
    if bio:
        out["bio"] = bio[:160]


async def native_instagram_profile(params: Dict[str, Any], request: Request) -> Tuple[Optional[Dict], bool]:
    """Instagram public profile — 3-layer chain: API → OG meta → search index (kabhi error nahi)."""
    user = re.sub(r"[^A-Za-z0-9._]", "", str(
        params.get("username") or params.get("user") or params.get("q") or "").lstrip("@"))
    if not user:
        return None, True
    if user.lower() in _IG_CACHE:
        return dict(_IG_CACHE[user.lower()]), False

    profile_url = f"https://www.instagram.com/{user}/"

    # 1) official-ish web API
    node = await _ig_web_profile(user)
    if node:
        result: Dict[str, Any] = {
            "success": True, "username": node.get("username") or user,
            "full_name": node.get("full_name") or "",
            "biography": node.get("biography") or "",
            "followers": (node.get("edge_followed_by") or {}).get("count"),
            "following": (node.get("edge_follow") or {}).get("count"),
            "posts": (node.get("edge_owner_to_timeline_media") or {}).get("count"),
            "verified": bool(node.get("is_verified")),
            "private": bool(node.get("is_private")),
            "business": bool(node.get("is_business_account")),
            "category": node.get("category_name") or "",
            "profile_pic": node.get("profile_pic_url_hd") or node.get("profile_pic_url") or "",
            "external_url": node.get("external_url") or "",
            "user_id": str(node.get("id") or ""),
            "profile_url": profile_url,
            "source": "instagram (web_profile_info)",
        }
        _IG_CACHE[user.lower()] = dict(result)
        return result, False

    # 2) OG meta tags (bot UA)
    og = await _ig_og(user)
    if og:
        res2: Dict[str, Any] = {"success": True, "partial": True, "username": user,
                                "profile_url": profile_url,
                                "full_name": og.get("full_name") or "",
                                "profile_pic": og.get("profile_pic") or "",
                                "source": "instagram (public meta tags)"}
        if og.get("_desc"):
            _ig_parse_desc(og["_desc"], res2)
        res2["note"] = ("Live API ne rate-limit kiya — ye public page metadata hai "
                        "(counts approx ho sakte hain).")
        _IG_CACHE[user.lower()] = dict(res2)
        return res2, False

    # 3) search index (DDG, multi-query)
    dd: Dict[str, Any] = {}
    for q in (f"site:instagram.com {user}", f"instagram {user} followers"):
        try:
            dd = await _ig_ddg(user, _q=q)
        except TypeError:
            dd = await _ig_ddg(user)
        if dd.get("followers") or dd.get("full_name"):
            break
        dd = dd or {}
    if dd:
        res3: Dict[str, Any] = {"success": True, "partial": True,
                                "username": dd.get("username") or user,
                                "full_name": dd.get("full_name") or "",
                                "biography": dd.get("bio") or "",
                                "followers": dd.get("followers") or None,
                                "following": dd.get("following") or None,
                                "posts": dd.get("posts") or None,
                                "profile_pic": "", "profile_url": profile_url,
                                "source": "search index (partial)",
                                "note": "Live API blocked tha; ye public search index se hai (approx)."}
        _IG_CACHE[user.lower()] = dict(res3)
        return res3, False

    # 4) Sab block → phir bhi kaam ka jawab (link + status), error nahi
    res4: Dict[str, Any] = {
        "success": True, "partial": True, "data_limited": True, "username": user,
        "profile_url": profile_url,
        "followers": None, "following": None, "posts": None,
        "source": "instagram",
        "note": ("Instagram ne is server IP se data block kar diya (rate-limit). "
                 "Profile link neeche diya hai — app me kholein. 10-15 min baad dobara try karein."),
        "links": [{"name": "Open in Instagram", "url": profile_url},
                  {"name": "Search on Google", "url": f"https://www.google.com/search?q=instagram+{user}"}],
    }
    _IG_CACHE[user.lower()] = dict(res4)
    return res4, False


async def native_instagram_posts(params: Dict[str, Any], request: Request) -> Tuple[Optional[Dict], bool]:
    """Instagram ke recent public posts (thumbnail / video + permalink + likes/comments)."""
    user = re.sub(r"[^A-Za-z0-9._]", "", str(
        params.get("username") or params.get("user") or params.get("q") or "").lstrip("@"))
    if not user:
        return None, True
    profile_url = f"https://www.instagram.com/{user}/"
    node = await _ig_web_profile(user)
    if not node:
        return {"success": True, "partial": True, "data_limited": True, "username": user,
                "count": 0, "posts": [], "profile_url": profile_url,
                "note": ("Instagram ne is server IP se data block kar diya (rate-limit) — "
                         "posts list abhi nahi mili. Profile link se khud dekh sakte hain."),
                "links": [{"name": "Open profile", "url": profile_url}]}, False
    edges = ((node.get("edge_owner_to_timeline_media") or {}).get("edges") or [])
    items = []
    for e in edges[:12]:
        n = e.get("node") or {}
        cap_edge = ((n.get("edge_media_to_caption") or {}).get("edges") or [{}])
        caption = ((cap_edge[0] or {}).get("node") or {}).get("text") or ""
        items.append({
            "shortcode": n.get("shortcode"),
            "type": "video" if n.get("is_video") else "image",
            "caption": caption[:220],
            "thumbnail": n.get("thumbnail_src") or n.get("display_url") or "",
            "video_url": n.get("video_url") or "",
            "likes": (n.get("edge_liked_by") or {}).get("count")
                     or (n.get("edge_media_preview_like") or {}).get("count") or 0,
            "comments": (n.get("edge_media_to_comment") or {}).get("count") or 0,
            "taken_at": n.get("taken_at_timestamp"),
            "permalink": f"https://www.instagram.com/p/{n.get('shortcode')}/" if n.get("shortcode") else "",
        })
    return {"success": True, "username": node.get("username") or user,
            "full_name": node.get("full_name") or "",
            "profile_pic": node.get("profile_pic_url_hd") or node.get("profile_pic_url") or "",
            "count": len(items), "posts": items, "profile_url": profile_url,
            "source": "instagram (web_profile_info)"}, False


# =====================================================================
# v2.5 — TERABOX (native: share page → share/list API → files + links)
# =====================================================================
_TB_CACHE: Dict[str, Dict[str, Any]] = {}
TB_RESOLVERS = [
    ("TeraBoxDL", "https://teraboxdl.site/"),
    ("TeraDL", "https://teradl.com/"),
    ("WpMedia", "https://www.wpmedia.xyz/terabox"),
]


def _tb_short(url: str) -> str:
    m = re.search(r"(?:surl=|/s/)([A-Za-z0-9_-]{6,40})", str(url or ""))
    return m.group(1) if m else ""


def _tb_extract(page: str) -> Dict[str, str]:
    """Share page me se shareid / uk / jsToken nikaalo (mobile UA wale page me hote hain).

    Regex ke bajaye simple find-parsing — koi escaping bug nahi.
    """
    out: Dict[str, str] = {}

    def _num_after(key: str, minlen: int = 5) -> str:
        for q in ('"', "'"):
            i = page.find(f"{q}{key}{q}")
            if i < 0:
                i = page.find(f"{key}{q}")
            if i < 0:
                i = page.find(key)
            if i < 0:
                continue
            j = page.find(":", i)
            if j < 0 or j - i > 40:
                continue
            k = j + 1
            while k < len(page) and page[k] in " \t":
                k += 1
            if k < len(page) and page[k] in "\"'":
                k += 1
            digits = ""
            while k < len(page) and page[k].isdigit():
                digits += page[k]
                k += 1
            if len(digits) >= minlen:
                return digits
        return ""

    def _token_after(key: str, minlen: int = 10) -> str:
        i = page.find(key)
        while i >= 0:
            j = page.find(":", i)
            if j < 0 or j - i > 40:
                i = page.find(key, i + 1)
                continue
            k = j + 1
            while k < len(page) and page[k] in " \t\"'":
                k += 1
            tok = ""
            while k < len(page) and (page[k].isalnum() or page[k] in "_-"):
                tok += page[k]
                k += 1
            if len(tok) >= minlen and not tok.startswith("function"):
                return tok
            i = page.find(key, i + 1)
        return ""

    def _text_after(key: str, maxlen: int = 120) -> str:
        i = page.find(f'"{key}"')
        if i < 0:
            return ""
        j = page.find(":", i)
        if j < 0:
            return ""
        k = j + 1
        while k < len(page) and page[k] in " \t":
            k += 1
        if k < len(page) and page[k] in "\"'":
            q = page[k]
            e = page.find(q, k + 1)
            if e > k:
                return page[k + 1:e][:maxlen]
        return ""

    out["shareid"] = _num_after("shareid", 6)
    out["uk"] = _num_after("uk", 5)
    out["jsToken"] = _token_after("jsToken", 10)
    fn = _text_after("server_filename")
    if fn:
        out["filename"] = fn
    sz = _num_after("size", 4)
    if sz:
        out["size"] = sz
    return out


async def native_terabox(params: Dict[str, Any], request: Request) -> Tuple[Optional[Dict], bool]:
    """TeraBox share link → file list + direct links (ya link dead hone par saaf jawab)."""
    import httpx
    url = str(params.get("url") or params.get("link") or params.get("q") or "").strip()
    surl = _tb_short(url)
    if not surl:
        return None, True
    if surl in _TB_CACHE:
        return dict(_TB_CACHE[surl]), False

    mob = {"User-Agent": "Mozilla/5.0 (Linux; Android 10; SM-G975F) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/126.0 Mobile Safari/537.36"}
    result: Dict[str, Any] = {"success": False, "shorturl": surl, "share_url": url}
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers=mob) as client:
            r = await client.get("https://www.terabox.com/sharing/link", params={"surl": surl})
            page = r.text
            info = _tb_extract(page)
            result.update({k: v for k, v in info.items() if k in ("filename", "size")})
            files: List[Dict[str, Any]] = []
            if info.get("shareid"):
                p = {"app_id": "250528", "web": "1", "channel": "dubox", "clienttype": "0",
                     "jsToken": info.get("jsToken") or "", "shorturl": surl, "root": "1"}
                if info.get("uk"):
                    p["uk"] = info["uk"]
                rr = await client.get("https://www.terabox.com/share/list", params=p,
                                      headers={"Referer": str(r.url)})
                if rr.status_code == 200:
                    j = rr.json()
                    result["errno"] = j.get("errno")
                    for f in (j.get("list") or []):
                        if not isinstance(f, dict):
                            continue
                        size = f.get("size")
                        try:
                            size = int(size)
                        except Exception:  # noqa: BLE001
                            size = 0
                        files.append({
                            "name": f.get("server_filename") or f.get("filename") or "file",
                            "size_bytes": size,
                            "size": (f"{round(size / 1048576, 2)} MB" if size else "N/A"),
                            "isdir": str(f.get("isdir")) == "1",
                            "path": f.get("path"),
                            "dlink": f.get("dlink") or f.get("downloadLink") or "",
                            "fs_id": f.get("fs_id"),
                        })
            if files:
                result.update({"success": True, "count": len(files), "files": files,
                               "title": files[0].get("name") if len(files) == 1 else
                                        (result.get("filename") or f"{len(files)} files"),
                               "provider": "terabox (native)",
                               "note": "Direct link 2-6 ghante me expire ho jata hai — turant use karein."})
                _TB_CACHE[surl] = dict(result)
                return result, False
            # link dead / files nahi mile → saaf jawab + resolver list (error nahi)
            result.update({
                "success": True, "count": 0, "files": [],
                "title": result.get("filename") or "",
                "provider": "terabox",
                "note": ("Is share link par koi file nahi mili — link delete/expire ho gaya hai. "
                         "Naya link banakar dobara try karein."),
                "resolvers": [{"name": n, "url": u} for n, u in TB_RESOLVERS],
            })
    except Exception as exc:  # noqa: BLE001
        result.update({"success": True, "count": 0, "files": [], "provider": "terabox",
                       "note": f"TeraBox server se baat nahi ho payi: {str(exc)[:100]}",
                       "resolvers": [{"name": n, "url": u} for n, u in TB_RESOLVERS]})
    _TB_CACHE[surl] = dict(result)
    return result, False


async def native_bgmi(params: Dict[str, Any], request: Request) -> Tuple[Optional[Dict], bool]:
    """BGMI / PUBG Mobile player info — provider key ho to live, warna saaf jawab + guide.

    Official PUBG API mobile support nahi karta, isliye mobile stats ke liye authorized
    provider key chahiye. Env me set karo:
        BGMI_API_URL=https://provider.example/api/player
        BGMI_API_KEY=xxxxx
    """
    user = str(params.get("user") or params.get("id") or params.get("uid")
               or params.get("player") or params.get("q") or "").strip()
    if not user:
        return None, True
    api_url = (os.environ.get("BGMI_API_URL") or "").strip()
    api_key = (os.environ.get("BGMI_API_KEY") or "").strip()
    if api_url:
        import httpx
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True,
                                         headers={"User-Agent": UA}) as client:
                r = await client.get(api_url, params={"id": user, "uid": user, "key": api_key},
                                     headers={"Authorization": f"Bearer {api_key}"} if api_key else {})
                if r.status_code == 200:
                    j = r.json()
                    if j:
                        return {"success": True, "player_id": user, "provider": api_url,
                                "source": "authorized provider", "data": j}, False
        except Exception:  # noqa: BLE001
            pass
    return {
        "success": True, "available": False, "player_id": user,
        "note": ("BGMI / PUBG Mobile ka player data official PUBG API me nahi aata (mobile support "
                 "nahi hai) — iske liye authorized provider key chahiye. Key lagte hi ye endpoint "
                 "live stats dega."),
        "how_to_setup": ["BGMI_API_URL=<provider endpoint>", "BGMI_API_KEY=<your key>"],
        "links": [
            {"name": "Official BGMI site", "url": "https://www.battlegroundsmobileindia.com/"},
            {"name": "Official PUBG API (PC/console)", "url": "https://developer.pubg.com/"},
            {"name": "How to find your in-game ID", "url": "https://www.google.com/search?q=how+to+find+bgmi+character+id"},
        ],
    }, False

NATIVE_FUNCS: Dict[str, Callable[..., Awaitable[Tuple[Optional[Dict], bool]]]] = {
    "ip_v1": native_ip_v1,
    "ip_v2": native_ip_v2,
    "ip_v3": native_ip_v3,
    "imei": native_imei,
    "country": native_country,
    "ai_gf": native_ai_gf,
    "github": native_github,
    "ifsc": native_ifsc,
    "pincode": native_pincode,
    "song": native_song,
    "youtube": native_youtube,
    "image_to_prompt": native_image_to_prompt,
    "num_info": native_num_info,
    "leak": native_leak,
    "gst": native_gst,
    "gst_search": native_gst_search,
    "pan": native_pan,
    "pan_to_gst": native_pan_to_gst,
    "num_info_full": native_num_info_full,
    "vehicle_report": native_vehicle_report,
    "family": native_family,
    "email_info": native_email_info,
    "pass_check": native_pass_check,
    "aadhaar_family": native_aadhaar_family,
    "youtube_download": native_youtube_download,
    "device_specs": native_device_specs,
    "snap_stories": native_snap_stories,
    "snap_highlights": native_snap_highlights,
    "instagram_profile": native_instagram_profile,
    "instagram_posts": native_instagram_posts,
    "terabox": native_terabox,
    "bgmi": native_bgmi,
}
for _kind in ("challan", "challan-v2", "challan-v4", "info", "info-v2", "rc", "details", "v"):
    NATIVE_FUNCS[f"vehicle_{_kind.replace('-', '_')}"] = None  # filled after loop (await below)


async def _mk_vehicle(kind: str):
    return await native_vehicle(kind)


# =====================================================================
# ENDPOINT CATALOG
# =====================================================================
P = lambda name, sample, required=False: {"name": name, "sample": sample, "required": required}

ENDPOINTS: List[Dict[str, Any]] = [
    # ---------- IP & Network ----------
    dict(path="ip-v1", name="IP Info V1", icon="🌐", category="IP & Network", native="ip_v1",
         mode="native", params=[P("query", "8.8.8.8")],
         desc="IP geolocation (ip-api.com): country, city, ISP, coordinates, timezone."),
    dict(path="ip-v2", name="IP Info V2", icon="🌐", category="IP & Network", native="ip_v2",
         mode="native", params=[P("ip", "157.35.26.44")],
         desc="IP geolocation (ipwho.is) with ASN, proxy detection and calling code."),
    dict(path="ip-v3", name="IP Info V3", icon="🌐", category="IP & Network", native="ip_v3",
         mode="native", params=[P("ip", "157.35.26.44")],
         desc="IP geolocation (ipinfo.io): hostname, org, loc, postal, timezone."),

    # ---------- Device ----------
    dict(path="imei", name="IMEI Info", icon="📱", category="Device", native="imei",
         mode="native", timeout=60, params=[P("imei", "356356426587792"), P("specs", "1")],
         desc="⭐ Full IMEI check: 8-digit TAC se brand + model (255k TAC database) + poori specs + photo "
              "(nanoreview). Full serial digits discard hote hain; no blacklist/owner lookup. specs=0 se sirf model."),
    dict(path="device-specs", name="Device Specs", icon="📲", category="Device", native="device_specs",
         mode="native", timeout=45, params=[P("model", "samsung galaxy tab a9 plus")],
         desc="Kisi bhi phone/tablet ki full specs + photo (nanoreview.net se) — model naam se."),

    # ---------- World ----------
    dict(path="country", name="Country Info", icon="🗺️", category="World", native="country",
         mode="merge_upstream", params=[P("name", "india")],
         desc="Country summary, capital, currency, borders, ISO codes."),
    dict(path="ai-gf", name="AI Girlfriend", icon="💕", category="AI", native="ai_gf",
         mode="merge_upstream", params=[P("prompt", "hi")],
         desc="Chat / AI girlfriend style reply (upstream AI if available, else offline Hinglish bot)."),

    # ---------- Code / Dev ----------
    dict(path="github", name="GitHub Search", icon="🐙", category="Code", native="github",
         mode="native", params=[P("q", "@Rohit")],
         desc="Search GitHub users, repositories and fetch a profile."),

    # ---------- India Utilities ----------
    dict(path="ifsc", name="IFSC Info", icon="🏦", category="India Utilities", native="ifsc",
         mode="native", params=[P("ifsc", "SBIN0000001")],
         desc="Bank branch details from IFSC code (branch, address, UPI/RTGS/NEFT support)."),
    dict(path="pincode", name="Pincode Info", icon="📮", category="India Utilities", native="pincode",
         mode="native", params=[P("pincode", "110001")],
         desc="Post office list, district and state for any Indian PIN code."),

    # ---------- Vehicle ----------
    dict(path="vehicle-challan", name="Vehicle Challan (disabled)", icon="🔒", category="Vehicle",
         native="vehicle_challan", mode="merge_native", params=[P("number", "XX00XX0000")],
         desc="Disabled until an authorized vehicle/challan provider is configured; use official e-Challan portal."),
    dict(path="vehicle-challan-v2", name="Vehicle Challan V2 (disabled)", icon="🔒", category="Vehicle",
         native="vehicle_challan_v2", mode="merge_native", params=[P("number", "XX00XX0000")],
         desc="Disabled for privacy/authorization; use official e-Challan portal."),
    dict(path="vehicle-challan-v4", name="Vehicle Challan V4 (disabled)", icon="🔒", category="Vehicle",
         native="vehicle_challan_v4", mode="merge_native", params=[P("number", "XX00XX0000")],
         desc="Disabled for privacy/authorization; use official e-Challan portal."),
    dict(path="vehicle-info", name="Vehicle Info (disabled)", icon="🔒", category="Vehicle",
         native="vehicle_info", mode="merge_native", params=[P("vehicle_number", "XX00XX0000")],
         desc="Live vehicle-owner lookup is disabled; only public RTO parsing/official links are available in the bot."),
    dict(path="vehicle-info-v2", name="Vehicle Info V2 (disabled)", icon="🔒", category="Vehicle",
         native="vehicle_info_v2", mode="merge_native", params=[P("vehicle_number", "XX00XX0000")],
         desc="Disabled for privacy/authorization."),
    dict(path="vehicle-rc", name="Vehicle RC (disabled)", icon="🔒", category="Vehicle",
         native="vehicle_rc", mode="merge_native", params=[P("number", "XX00XX0000")],
         desc="Disabled until an authorized provider is configured; use official VAHAN portal."),
    dict(path="vehicle-details", name="Vehicle Details (disabled)", icon="🔒", category="Vehicle",
         native="vehicle_details", mode="merge_native", params=[P("number", "XX00XX0000")],
         desc="Disabled for privacy/authorization; do not query owner records from an arbitrary plate."),
    dict(path="vehicle-v", name="Vehicle V (disabled)", icon="🔒", category="Vehicle",
         native="vehicle_v", mode="merge_native", params=[P("rc", "XX00XX0000")],
         desc="Disabled for privacy/authorization."),

    # ---------- Media ----------
    dict(path="song", name="Song Downloader", icon="🎵", category="Media", native="song",
         mode="upstream", params=[P("song", "chandani")],
         desc="Search songs: upstream (Saavn download links) else Apple Music preview links."),

    # ---------- Social ----------
    dict(path="snap-stories", name="Snapchat Stories", icon="👻", category="Social",
         native="snap_stories", mode="native", params=[P("username", "priyapanchal272")],
         desc="Snapchat public stories (native parse) + profile info."),
    dict(path="snap-highlights", name="Snapchat Highlights", icon="👻", category="Social",
         native="snap_highlights", mode="native", params=[P("username", "priyapanchal272")],
         desc="Snapchat public highlights + spotlight (native parse)."),
    dict(path="instagram-profile", name="Instagram Profile", icon="📸", category="Social",
         native="instagram_profile",
         mode="upstream", params=[P("username", "sumit_sharma2")],
         desc="Instagram profile info (followers, bio, profile picture)."),
    dict(path="instagram-posts", name="Instagram Posts", icon="📸", category="Social",
         native="instagram_posts",
         mode="upstream", params=[P("username", "sumit_sharma2")],
         desc="Recent Instagram posts / media for a username."),

    # ---------- File / Cloud ----------
    dict(path="terabox-file", name="Terabox File", icon="📦", category="Files", native="terabox",
         mode="upstream", params=[P("url", "https://1024terabox.com/s/1ahJz-qdH7h_9One0lXxDoA")],
         desc="Terabox file metadata from a share link."),
    dict(path="terabox-stream", name="Terabox Stream", icon="📦", category="Files", native="terabox",
         mode="upstream", params=[P("url", "https://1024terabox.com/s/1EqwgqWQgmeOvQxc33258UA")],
         desc="Direct streaming / download links for Terabox content."),
    dict(path="terabox-stream-v2", name="Terabox Stream V2", icon="📦", category="Files",
         native="terabox",
         mode="upstream", params=[P("url", "https://1024terabox.com/s/1EqwgqWQgmeOvQxc33258UA")],
         desc="Terabox streaming, version 2."),
    dict(path="terabox-stream-v3", name="Terabox Stream V3", icon="📦", category="Files",
         native="terabox",
         mode="upstream", params=[P("url", "https://1024terabox.com/s/1EqwgqWQgmeOvQxc33258UA")],
         desc="Terabox streaming, version 3."),

    # ---------- Gaming ----------
    dict(path="bgmi", name="BGMI Info", icon="🎮", category="Gaming", native="bgmi",
         mode="merge_native", params=[P("user", "55622571339")],
         desc="BGMI / PUBG Mobile player stats by in-game ID."),

    # ---------- AI / Media ----------
    dict(path="image-to-prompt", name="Image To Prompt", icon="🖼️", category="AI",
         native="image_to_prompt", mode="upstream",
         params=[P("url", "https://i.ytimg.com/vi/X8X-XyK4CYE/mqdefault.jpg")],
         desc="Describe an image / generate an AI prompt from an image URL."),

    # ---------- YouTube ----------
    dict(path="youtube-all", name="YouTube All", icon="▶️", category="YouTube", native="youtube",
         mode="merge_upstream", params=[P("url", "https://youtube.com/watch?v=X8X-XyK4CYE")],
         desc="Everything for a video: metadata, thumbnails, channel, formats."),
    dict(path="youtube-info", name="YouTube Info", icon="▶️", category="YouTube", native="youtube",
         mode="merge_upstream", params=[P("url", "https://youtube.com/watch?v=X8X-XyK4CYE")],
         desc="Video information from a YouTube URL."),
    dict(path="youtube-info-id", name="YouTube Info By ID", icon="▶️", category="YouTube",
         native="youtube", mode="merge_upstream", params=[P("id", "X8X-XyK4CYE")],
         desc="Video information from a YouTube video id."),

    # ---------- Leak OSINT ----------
    dict(path="leak-v1", name="Leak OSINT V1 (disabled)", icon="🔒", category="Privacy", native="leak",
         mode="upstream", params=[P("q", "")], db_category="leak", cache_ttl=0,
         desc="Disabled for privacy; no leaked personal records are searched or returned."),
    dict(path="leak-v2", name="Leak OSINT V2 (disabled)", icon="🔒", category="Privacy", native="leak",
         mode="upstream", params=[P("q", "")], db_category="leak", cache_ttl=0,
         desc="Disabled for privacy; no leaked personal records are searched or returned."),
    dict(path="num-info", name="Number Info (disabled in hub)", icon="🔒", category="Privacy",
         native="num_info_full", mode="native", timeout=1, cache_ttl=0,
         params=[P("q", "")],
         desc="Hub lookup disabled for privacy. Telegram bot me sirf local non-sensitive phone metadata aur official safety links milte hain."),
    dict(path="number-info", name="Number Info (disabled alias)", icon="🔒", category="Privacy",
         native="num_info_full", mode="native", timeout=1, cache_ttl=0,
         params=[P("q", "")],
         desc="Disabled for privacy; no name, family, linked number, address or government ID is returned."),
    dict(path="num", name="Number (disabled alias)", icon="🔒", category="Privacy",
         native="num_info_full", mode="native", timeout=1, cache_ttl=0,
         params=[P("q", "")],
         desc="Disabled for privacy; no leaked personal records are searched or returned."),

    # ---------- GST / PAN ----------
    dict(path="gst-search", name="GST Search", icon="🧮", category="GST / PAN", native="gst_search",
         mode="merge_native", params=[P("gstin", "19BOKPS7056D1ZI")], db_category="gst",
         desc="Search GSTIN in public GST database."),
    dict(path="gst-direct", name="GST Direct", icon="🧮", category="GST / PAN", native="gst",
         mode="merge_native", params=[P("gstin", "19BOKPS7056D1ZI")], db_category="gst",
         desc="Direct GSTIN lookup."),
    dict(path="gst-info", name="GST Info", icon="🧮", category="GST / PAN", native="gst",
         mode="merge_native", params=[P("gst", "19BOKPS7056D1ZI")], db_category="gst",
         desc="GSTIN details: legal name, status, registration date, jurisdiction."),
    dict(path="gst-info-v2", name="GST Info V2", icon="🧮", category="GST / PAN", native="gst",
         mode="merge_native", params=[P("gst", "19BOKPS7056D1ZI")], db_category="gst",
         desc="GSTIN details, version 2."),
    dict(path="pan-to-gst", name="PAN To GST", icon="🔗", category="GST / PAN", native="pan_to_gst",
         mode="merge_native", params=[P("pan", "BOKPS7056D")], db_category="pan",
         desc="Find GSTIN(s) registered against a PAN."),
    dict(path="pan-to-gst-v2", name="PAN To GST V2", icon="🔗", category="GST / PAN", native="pan_to_gst",
         mode="merge_native", params=[P("pan", "BOKPS7056D")], db_category="pan",
         desc="PAN to GSTIN mapping, version 2."),
    dict(path="pan-to-gst-v3", name="PAN To GST V3", icon="🔗", category="GST / PAN", native="pan_to_gst",
         mode="merge_native", params=[P("pan", "AACCL5754F")], db_category="pan",
         desc="PAN to GSTIN mapping, version 3."),
    dict(path="pan-to-gst-v4", name="PAN To GST V4", icon="🔗", category="GST / PAN", native="pan_to_gst",
         mode="merge_native", params=[P("pan", "AAYFK4129N")], db_category="pan",
         desc="PAN to GSTIN mapping, version 4."),
    dict(path="pan-info", name="PAN Info", icon="🪪", category="GST / PAN", native="pan",
         mode="merge_native", params=[P("pan", "AAYFK4129N")], db_category="pan",
         desc="PAN validation, holder type and linked details."),

    # ---------- Own report APIs (aggregated) ----------
    dict(path="vehicle-report", name="Vehicle Report (disabled)", icon="🔒", category="Vehicle",
         native="vehicle_report", mode="native", timeout=1, cache_ttl=0,
         params=[P("number", "XX00XX0000")],
         desc="Disabled until an authorized provider is configured. Official VAHAN/e-Challan links are available."),
    dict(path="vehicle-full", name="Vehicle Report (disabled alias)", icon="🔒", category="Vehicle",
         native="vehicle_report", mode="native", timeout=1, cache_ttl=0,
         params=[P("number", "XX00XX0000")],
         desc="Disabled for privacy/authorization."),
    dict(path="rc-info", name="RC Info (disabled alias)", icon="🔒", category="Vehicle",
         native="vehicle_report", mode="native", timeout=1, cache_ttl=0,
         params=[P("rc", "XX00XX0000")],
         desc="Disabled for privacy/authorization."),

    # ---------- New: family + email ----------
    dict(path="family", name="Family / Linked Numbers (disabled)", icon="🔒", category="Privacy",
         native="family", mode="native", timeout=1, cache_ttl=0,
         params=[P("q", "")],
         desc="Disabled for privacy; family links and alternate personal numbers are not searched or returned."),
    dict(path="num-family", name="Family (disabled alias)", icon="🔒", category="Privacy",
         native="family", mode="native", timeout=1, cache_ttl=0,
         params=[P("q", "")],
         desc="Disabled for privacy; no family-member search."),
    dict(path="email-info", name="Email OSINT (disabled)", icon="🔒", category="Privacy",
         native="email_info", mode="native", timeout=1, cache_ttl=0,
         params=[P("email", "")],
         desc="Disabled; no leaked email/phone/address/password records are searched or returned."),
    dict(path="email", name="Email OSINT (disabled alias)", icon="🔒", category="Privacy",
         native="email_info", mode="native", timeout=1, cache_ttl=0,
         params=[P("email", "")],
         desc="Disabled for privacy."),
    dict(path="pass-check", name="Password Breach Check", icon="🔑", category="Email",
         native="pass_check", mode="native", timeout=30, cache_ttl=86400,
         params=[P("password", "Example-Only-Not-A-Secret-7Q")],
         desc="Password kisi breach me hai ya nahi (Pwned Passwords k-anonymity - password server "
              "se bahar nahi jata)."),

    # ---------- Aadhaar family + YouTube downloader ----------
    dict(path="aadhaar-family", name="Aadhaar Family Intel (disabled)", icon="🔒", category="Privacy",
         native="aadhaar_family", mode="native", timeout=1, cache_ttl=0,
         params=[P("aadhaar", "")],
         desc="Disabled for privacy. Apne record ke liye UIDAI/NFSA ke official consent-based portal ka use karein."),
    dict(path="aadhaar", name="Aadhaar Family (disabled alias)", icon="🔒", category="Privacy",
         native="aadhaar_family", mode="native", timeout=1, cache_ttl=0,
         params=[P("aadhaar", "")],
         desc="Disabled for privacy; Aadhaar is not sent to a third-party lookup service."),
    dict(path="ration", name="Ration / Family (disabled alias)", icon="🔒", category="Privacy",
         native="aadhaar_family", mode="native", timeout=1, cache_ttl=0,
         params=[P("q", "")],
         desc="Disabled for privacy; official NFSA portal may require account/OTP/CAPTCHA."),
    dict(path="youtube-download", name="YouTube Downloader", icon="⬇️", category="YouTube",
         native="youtube_download", mode="native", timeout=70, cache_ttl=3600,
         params=[P("url", "https://youtube.com/watch?v=X8X-XyK4CYE")],
         desc="⭐ YouTube link daalo → direct video/audio download links (Telegram bot me direct "
              "bhejne layak). &type=audio (mp3/m4a), &quality=720, &format=text se card."),
    dict(path="ytdl", name="YouTube Download (alias)", icon="⬇️", category="YouTube",
         native="youtube_download", mode="native", timeout=70, cache_ttl=3600,
         params=[P("url", "https://youtube.com/watch?v=X8X-XyK4CYE")],
         desc="Alias of /api/youtube-download."),
    dict(path="youtube-mp3", name="YouTube Audio (MP3/M4A)", icon="🎵", category="YouTube",
         native="youtube_download", mode="native", timeout=70, cache_ttl=3600,
         params=[P("url", "https://youtube.com/watch?v=X8X-XyK4CYE")],
         desc="Same as youtube-download with type=audio (sirf audio link)."),
]

ENDPOINT_MAP = {e["path"]: e for e in ENDPOINTS}

# These routes are intentionally unavailable: they would expose personal records or
# live vehicle-owner/challan data without a verified authorization path.
PRIVACY_DISABLED_ENDPOINTS = {
    "num-info", "number-info", "num", "leak-v1", "leak-v2",
    "family", "num-family", "email-info", "email",
    "aadhaar-family", "aadhaar", "ration", "vehicle-report", "vehicle-full", "rc-info",
} | {e["path"] for e in ENDPOINTS if e["path"].startswith("vehicle-")}
DISABLED_RECORD_CATEGORIES = {"leak", "phone", "vehicle"}


def safe_endpoint_allowlist(value: Any) -> Optional[str]:
    """Reject key/reseller plans that try to sell privacy-disabled or unknown routes."""
    if isinstance(value, (list, tuple, set)):
        items = [str(x).strip() for x in value if str(x).strip()]
    else:
        raw = str(value or "*").strip() or "*"
        if raw.lower() in ("*", "all"):
            return "*"
        items = [x.strip() for x in raw.split(",") if x.strip()]
    if not items:
        return "*"
    known = set(ENDPOINT_MAP)
    if any(item not in known or item in PRIVACY_DISABLED_ENDPOINTS or item.startswith("vehicle-")
           for item in items):
        return None
    return ",".join(dict.fromkeys(items))


# If an upstream version is down (HTTP 502 / "Unauthorized"), try a sibling version automatically.
FALLBACKS = {
    "terabox-stream-v3": ["terabox-stream-v2", "terabox-stream", "terabox-file"],
    "terabox-stream-v2": ["terabox-stream", "terabox-file"],
    "terabox-stream": ["terabox-stream-v2", "terabox-file"],
    "terabox-file": ["terabox-stream", "terabox-stream-v2"],
    "vehicle-info": ["vehicle-info-v2", "vehicle-rc", "vehicle-details", "vehicle-v"],
    "vehicle-info-v2": ["vehicle-info", "vehicle-rc", "vehicle-details"],
    "vehicle-rc": ["vehicle-info", "vehicle-details", "vehicle-v"],
    "vehicle-details": ["vehicle-info", "vehicle-rc", "vehicle-v"],
    "vehicle-v": ["vehicle-rc", "vehicle-info"],
    "vehicle-challan": ["vehicle-challan-v2", "vehicle-challan-v4"],
    "vehicle-challan-v2": ["vehicle-challan-v4", "vehicle-challan"],
    "vehicle-challan-v4": ["vehicle-challan-v2", "vehicle-challan"],
    "gst-search": ["gst-direct", "gst-info", "gst-info-v2"],
    "gst-direct": ["gst-search", "gst-info", "gst-info-v2"],
    "gst-info": ["gst-info-v2", "gst-direct", "gst-search"],
    "gst-info-v2": ["gst-info", "gst-direct", "gst-search"],
    "pan-to-gst": ["pan-to-gst-v2", "pan-to-gst-v3", "pan-to-gst-v4"],
    "pan-to-gst-v2": ["pan-to-gst-v3", "pan-to-gst", "pan-to-gst-v4"],
    "pan-to-gst-v3": ["pan-to-gst-v2", "pan-to-gst-v4", "pan-to-gst"],
    "pan-to-gst-v4": ["pan-to-gst-v3", "pan-to-gst-v2", "pan-to-gst"],
    "youtube-all": ["youtube-info", "youtube-info-id"],
    "youtube-info": ["youtube-info-id", "youtube-all"],
    "leak-v2": ["leak-v1"],
    "leak-v1": ["leak-v2"],
}
for _p, _fbs in FALLBACKS.items():
    if _p in ENDPOINT_MAP:
        ENDPOINT_MAP[_p]["fallbacks"] = _fbs

# attach vehicle native functions
for ep in ENDPOINTS:
    if ep["native"] and ep["native"].startswith("vehicle_") and NATIVE_FUNCS.get(ep["native"]) is None:
        kind = ep["path"].replace("vehicle-", "")
        NATIVE_FUNCS[ep["native"]] = native_vehicle(kind)


# =====================================================================
# API KEY HANDLING
# =====================================================================
def key_row(api_key: str) -> Optional[sqlite3.Row]:
    with db() as conn:
        return conn.execute("SELECT * FROM api_keys WHERE api_key=?", (api_key,)).fetchone()


def validate_key(api_key: Optional[str]) -> Tuple[bool, str, str]:
    """Returns (ok, key_used, error_message)"""
    k = (api_key or "").strip()
    if not k:
        return False, "", "API key missing. Add ?key=Demo to the URL (or use your own key)."
    if MASTER_API_KEYS and k in MASTER_API_KEYS:      # v2.6: env master key — hamesha valid
        return True, k, ""
    if get_setting("demo_key_enabled", "1") == "1" and k == DEMO_KEY:
        return True, "Demo", ""
    row = key_row(k)
    if row and int(row["is_active"]) == 1:
        return True, k, ""
    if row:
        return False, k, "This API key has been disabled by the admin."
    return False, k, "Invalid API key. Use ?key=Demo or create a key in the dashboard."


def key_record(api_key: Optional[str]) -> Optional[Dict[str, Any]]:
    """Full key row (or a synthetic record for the Demo key)."""
    k = (api_key or "").strip()
    if not k:
        return None
    if MASTER_API_KEYS and k in MASTER_API_KEYS:
        return {"id": -1, "api_key": k, "name": "MASTER (env)", "is_active": 1,
                "expires_at": None, "allowed_endpoints": "*", "device_lock": 0,
                "bound_devices": "", "max_devices": 1, "rate_limit": 0,
                "customer": "HIMANSHU", "price": "0", "is_master": True,
                "requests": 0, "note": "MASTER_API_KEY env se — restart par bhi zinda"}
    if get_setting("demo_key_enabled", "1") == "1" and k == DEMO_KEY:
        return {"id": 0, "api_key": DEMO_KEY, "name": "Demo", "is_active": 1,
                "expires_at": None, "allowed_endpoints": "*", "device_lock": 0,
                "bound_devices": "", "max_devices": 0, "rate_limit": 0,
                "customer": "public", "price": "0", "is_demo": True,
                "requests": 0, "note": "Public demo key"}
    row = key_row(k)
    return dict(row) if row else None


def days_left(expires_at: Optional[str]) -> Optional[int]:
    if not expires_at:
        return None
    try:
        exp = datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S")
        return (exp - datetime.now().replace(tzinfo=None)).days
    except Exception:
        return None


def bind_device(rec: Dict[str, Any], device_value: str) -> Tuple[bool, int]:
    """Device lock: bind the first device(s). Returns (allowed, bound_count)."""
    if not device_value:
        return True, 0
    dev_hash = hashlib.sha256(device_value.encode()).hexdigest()[:16]
    bound = [d for d in (rec.get("bound_devices") or "").split(",") if d]
    if dev_hash in bound:
        return True, len(bound)
    limit = max(1, int(rec.get("max_devices") or 1))
    if len(bound) >= limit:
        return False, len(bound)
    bound.append(dev_hash)
    try:
        with db() as conn:
            conn.execute("UPDATE api_keys SET bound_devices=? WHERE api_key=?",
                         (",".join(bound), rec["api_key"]))
            conn.commit()
    except Exception:
        pass
    return True, len(bound)


def over_rate_limit(api_key: str, per_key_limit: int = 0) -> bool:
    try:
        limit = int(per_key_limit or get_setting("rate_limit_per_min", "120") or 0)
    except Exception:
        limit = 120
    if limit <= 0:
        return False
    cutoff = (datetime.now(IST) - timedelta(seconds=60)).strftime("%Y-%m-%d %H:%M:%S")
    with db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM request_logs WHERE api_key=? AND ts>=?", (api_key, cutoff)).fetchone()
    return int(row["c"]) > limit


# ---------------------------------------------------------------------
# ydl stream proxy (v2.2) — googlevideo links IP-locked hote hain, isliye
# hub se hi stream/forward karte hain. Sirf YouTube hosts allowed (SSRF-safe).
# ---------------------------------------------------------------------
YDL_ALLOWED_HOSTS = ("googlevideo.com", "ytimg.com", "googleusercontent.com", "ggpht.com")


@app.get("/api/ydl/stream")
async def ydl_stream(request: Request, url: str = "", key: str = ""):
    """YouTube ke direct links ko hub ke through stream/download karo (Range support)."""
    api_key = key or request.query_params.get("key", DEMO_KEY)
    okk, _k, kerr = validate_key(api_key)
    if not okk:
        return JSONResponse({"success": False, "error": kerr}, status_code=401)
    try:
        from urllib.parse import urlparse as _urlparse
        host = (_urlparse(url).hostname or "").lower()
    except Exception:
        host = ""
    if not host or not any(host == h or host.endswith("." + h) for h in YDL_ALLOWED_HOSTS):
        return JSONResponse({
            "success": False,
            "error": "Sirf YouTube (googlevideo) links stream ho sakte hain.",
            "allowed_hosts": list(YDL_ALLOWED_HOSTS),
        }, status_code=400)

    import httpx
    from fastapi.responses import StreamingResponse
    rng = request.headers.get("range")
    fwd_headers = {"User-Agent": UA, "Accept": "*/*"}
    if rng:
        fwd_headers["Range"] = rng

    async def _iter():
        client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=120.0), follow_redirects=True)
        try:
            async with client.stream("GET", url, headers=fwd_headers) as resp:
                async for chunk in resp.aiter_bytes(64 * 1024):
                    yield chunk
        finally:
            await client.aclose()

    try:
        client0 = httpx.AsyncClient(timeout=30.0, follow_redirects=True)
        head = await client0.get(url, headers=fwd_headers)
        status = head.status_code
        hdrs = {}
        for h in ("content-type", "content-length", "content-range", "accept-ranges"):
            if h in head.headers:
                hdrs[h] = head.headers[h]
        await head.aclose()
        await client0.aclose()
        if status >= 400:
            return JSONResponse({"success": False, "error": f"Upstream HTTP {status}",
                                 "hint": "Link expire ho gaya hoga — dobara /api/youtube-download chalao."},
                                status_code=502)
        hdrs["content-disposition"] = 'attachment; filename="youtube_download"'
        return StreamingResponse(_iter(), status_code=status, headers=hdrs)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"success": False, "error": f"stream error: {str(exc)[:120]}"}, status_code=502)


# =====================================================================
# ENDPOINT RUNNER
# =====================================================================
PROVIDER_HINTS = {
    "terabox-file": "TeraBox ke liye upstream provider key chahiye (Dashboard → Settings → upstream_key).",
    "terabox-stream": "TeraBox ke liye upstream provider key chahiye (Dashboard → Settings → upstream_key).",
    "terabox-stream-v2": "TeraBox ke liye upstream provider key chahiye.",
    "terabox-stream-v3": "TeraBox ke liye upstream provider key chahiye.",
    "instagram-profile": "Instagram apni website se server requests block karta hai — provider key ya proxy chahiye.",
    "instagram-posts": "Instagram apni website se server requests block karta hai — provider key ya proxy chahiye.",
    "bgmi": "BGMI/PUBG stats ke liye official/paid provider key chahiye.",
    "youtube-download": "YouTube datacenter IP se download block karta hai — upstream provider key chahiye.",
    "ytdl": "YouTube datacenter IP se download block karta hai — upstream provider key chahiye.",
    "youtube-mp3": "YouTube datacenter IP se download block karta hai — upstream provider key chahiye.",
}


def error_payload(ep: Dict[str, Any], message: str, hint: Optional[str] = None) -> Dict[str, Any]:
    params = "&".join(f"{p['name']}={p['sample']}" for p in ep["params"])
    return {
        "success": False,
        "status": "error",
        "endpoint": ep["path"],
        "error": message,
        "hint": hint or "",
        "example": f"/api/{ep['path']}?key={DEMO_KEY}&{params}",
        "powered_by": brand(),
    }


async def run_endpoint(request: Request, ep: Dict[str, Any],
                       extra_params: Optional[Dict[str, Any]] = None) -> JSONResponse:
    started = time.time()
    path = ep.get("path", "")
    # v2.6: aapka authorized provider laga ho to ye endpoints live ho jate hain
    _vehicle_paths = path.startswith("vehicle-") or path in {"vehicle-report", "vehicle-full", "rc-info"}
    _numinfo_paths = path in {"num-info", "number-info", "num"}
    _provider_live = bool(vehicle_provider().get("url")) if _vehicle_paths else (
        bool(numinfo_provider().get("url")) if _numinfo_paths else False)

    if path in PRIVACY_DISABLED_ENDPOINTS and not _provider_live:
        key = request.query_params.get("key", DEMO_KEY)
        ok, _key_used, key_error = validate_key(key)
        if not ok:
            return JSONResponse(error_payload(ep, key_error), status_code=401)
        if path.startswith("vehicle-") or path in {"vehicle-report", "vehicle-full", "rc-info"}:
            message = ("Live vehicle/owner/challan lookup disabled hai jab tak authorized provider configure na ho. "
                       "Apni licensed API lagao: VEHICLE_PROVIDER_URL + VEHICLE_PROVIDER_KEY (Render env).")
            links = {
                "vahan": "https://vahan.parivahan.gov.in/nrservices/faces/user/searchstatus.xhtml",
                "echallan": "https://echallan.parivahan.gov.in/index/accused-challan",
            }
        elif path in {"num-info", "number-info", "num"}:
            message = ("Number info: LEGAL carrier lookup (operator/circle/type/MNP) ke liye apni API lagao — "
                       "NUMINFO_PROVIDER_URL + NUMINFO_PROVIDER_KEY (Render env). "
                       "Leaked personal records (naam/pata) jaan-boojh kar supported nahi — wo illegal hai.")
            links = {"mnp_verify": "https://tafcop.dgtelecom.gov.in/", "sanchar_saathi": "https://sancharsaathi.gov.in/"}
        elif path.startswith("aadhaar") or path == "ration":
            message = "Aadhaar/family lookup yahan available nahi. UIDAI/NFSA ke official consent-based portal ka use karein."
            links = {"uidai": "https://myaadhaar.uidai.gov.in/", "nfsa": "https://nfsa.gov.in/"}
        else:
            message = "Leaked personal-record lookup yahan supported nahi. Sirf non-sensitive phone metadata aur official safety links use karein."
            links = {"report_spam": "https://sancharsaathi.gov.in/"}
        return JSONResponse({
            "success": False, "status": "disabled", "endpoint": path,
            "error": message, "official_links": links,
            "powered_by": brand(),
        }, status_code=410)

    params = {k: v for k, v in (extra_params if extra_params is not None else request.query_params).items()
              if k not in ("key", "nocache", "_", "format")}
    if ep["path"] == "imei":
        # IMEI ka serial/check digit personal device identifier hai; sirf TAC cache/log/upstream tak jaata hai.
        raw_tac = clean_number(params.get("tac") or params.get("imei") or params.get("q") or "")
        params = {"imei": raw_tac[:8]} if len(raw_tac) >= 8 else {"imei": raw_tac}
    api_key = request.query_params.get("key", DEMO_KEY)

    # --- api key ---
    ok, key_used, err = validate_key(api_key)
    if not ok:
        return JSONResponse(error_payload(ep, err), status_code=401)
    krec = key_record(api_key) or {}

    # --- subscription checks: expiry / plan / device lock ---
    expires_at = krec.get("expires_at")
    if expires_at and now_ist() > expires_at:
        return JSONResponse({
            "success": False, "status": "expired", "endpoint": ep["path"],
            "error": f"Your API key expired on {expires_at} (IST).",
            "hint": "Renew karne ke liye admin se contact karein - dashboard me '+30 days' button se renew hota hai.",
            "key": key_used[:6] + "..." + key_used[-4:] if len(key_used) > 12 else key_used,
        }, status_code=403)

    allowed = (krec.get("allowed_endpoints") or "*").strip()
    if allowed not in ("*", "all", ""):
        permitted = {x.strip() for x in allowed.split(",") if x.strip()}
        if ep["path"] not in permitted:
            return JSONResponse({
                "success": False, "status": "not_in_plan", "endpoint": ep["path"],
                "error": f"Aapka plan is endpoint ko allow nahi karta: /api/{ep['path']}",
                "your_plan": sorted(permitted),
                "hint": "Admin se apna plan upgrade karwao (dashboard -> API Keys -> plan edit).",
            }, status_code=403)

    if int(krec.get("device_lock") or 0) == 1:
        device_value = (request.query_params.get("device")
                        or request.headers.get("x-device-id")
                        or client_ip(request))
        ok_device, bound_count = bind_device(krec, device_value)
        if not ok_device:
            return JSONResponse({
                "success": False, "status": "device_locked", "endpoint": ep["path"],
                "error": f"Ye key kisi aur device se lock hai ({bound_count}/{krec.get('max_devices') or 1} devices).",
                "hint": "Apne device se pehli baar call karte waqt ?device=<apna-naam> lagao, ya admin se unlock karwao.",
            }, status_code=403)

    if over_rate_limit(key_used, int(krec.get("rate_limit") or 0)):
        return JSONResponse(error_payload(
            ep, "Rate limit exceeded. Wait a minute or raise the limit in the dashboard."), status_code=429)

    # --- required params ---
    missing = [p["name"] for p in ep["params"] if p.get("required") and not params.get(p["name"])]
    if missing:
        return JSONResponse(error_payload(ep, f"Missing required parameter(s): {', '.join(missing)}"),
                            status_code=400)

    # --- cache ---
    nocache = request.query_params.get("nocache") in ("1", "true", "yes")
    cache_key = f"{ep['path']}?" + urllib_safe(params)
    ttl = int(ep.get("cache_ttl") or get_setting("cache_ttl", "3600") or 0)
    if not nocache and get_setting("cache_enabled", "1") == "1":
        cached = cache_get(cache_key, ttl)
        if cached is not None:
            ms = int((time.time() - started) * 1000)
            cached = apply_brand(mark(cached, "cache", ep))
            fmt_c = (request.query_params.get("format") or "").lower()
            if fmt_c in ("text", "txt", "card", "plain") and isinstance(cached, dict) \
                    and cached.get("formatted"):
                log_request(ep["path"], key_used, params, "cache+text", 200, ms, client_ip(request))
                return PlainTextResponse(cached["formatted"])
            log_request(ep["path"], key_used, params, "cache", 200, ms, client_ip(request))
            return JSONResponse(cached, headers={"X-Source": "cache"})

    native_fn_key = ep.get("native")
    native_fn = NATIVE_FUNCS.get(native_fn_key) if native_fn_key else None
    mode = ep.get("mode", "upstream")
    timeout = float(ep.get("timeout", 45))

    native_data: Optional[Dict[str, Any]] = None
    native_partial = True
    native_error: Optional[str] = None

    up_data: Optional[Any] = None
    up_error: Optional[str] = None

    async def get_native():
        nonlocal native_data, native_partial, native_error
        if not native_fn:
            return
        try:
            native_data, native_partial = await native_fn(params, request)
        except Exception as exc:
            native_data, native_partial, native_error = None, True, f"{type(exc).__name__}: {exc}"

    async def get_upstream():
        nonlocal up_data, up_error
        up_data, up_error = await upstream_call(ep["path"], params, timeout=timeout)
        if up_data is None:
            for fallback_path in ep.get("fallbacks", []):
                up_data, up_error = await upstream_call(fallback_path, params, timeout=timeout)
                if up_data is not None:
                    break

    if mode == "native":
        await get_native()
        if native_data is None:
            await get_upstream()
    elif mode == "upstream":
        await get_upstream()
        if up_data is None and native_fn:
            await get_native()
    elif mode == "merge_native":
        await get_native()
        await get_upstream()
    else:  # merge_upstream
        await get_upstream()
        if up_data is None and native_fn:
            await get_native()
        elif up_data is not None and native_fn:
            await get_native()

    # --- merge ---
    payload: Any = None
    source = "none"
    if mode in ("merge_native", "merge_upstream"):
        base: Dict[str, Any] = {}
        if isinstance(up_data, dict):
            base.update(up_data)
            source = "upstream"
        if isinstance(native_data, dict):
            if base:
                base["local_analysis"] = native_data
                source = "upstream+native"
            else:
                base = dict(native_data)
                source = "native"
        if not base:
            base = None
        payload = base
        if payload is None:
            source = "none"
    else:
        if native_data is not None and source != "upstream":
            payload, source = native_data, "native"
        elif up_data is not None:
            payload, source = up_data, "upstream"
        elif native_data is not None:
            payload, source = native_data, "native"

    # --- custom database records (leak / phone / gst / pan) ---
    if ep.get("db_category"):
        key_value = ""
        for pname in ("q", "query", "gstin", "gst", "pan", "number"):
            if params.get(pname):
                key_value = str(params[pname]).strip().lower()
                break
        custom = custom_lookup(ep["db_category"], key_value)
        if custom:
            if isinstance(payload, dict):
                payload["custom_database_records"] = custom
                payload["custom_database_count"] = len(custom)
                source += "+db"
            elif payload is None:
                payload = {"success": True, "query": params, "data": [],
                           "custom_database_records": custom,
                           "custom_database_count": len(custom)}
                source = "local-database"

    # --- last resort: serve an older cached copy (upstream down / rate limited) ---
    if payload is None and not nocache:
        stale = cache_get(cache_key, 7 * 24 * 3600)
        if stale is not None:
            stale = apply_brand(mark(stale, "stale-cache", ep))
            ms = int((time.time() - started) * 1000)
            log_request(ep["path"], key_used, params, "stale-cache", 200, ms, client_ip(request))
            return JSONResponse(stale, headers={"X-Source": "stale-cache",
                                                "X-Response-Time": f"{ms}ms"})

    if payload is None:
        ms = int((time.time() - started) * 1000)
        detail = up_error or native_error or "No data available from native source or upstream."
        if "disabled" in (up_error or ""):
            detail = ("Upstream is switched off in the dashboard (Settings tab) and no native data "
                      "was available for this endpoint.")
        log_request(ep["path"], key_used, params, "error", 502, ms, client_ip(request))
        _hint = PROVIDER_HINTS.get(ep["path"]) or detail
        return JSONResponse(error_payload(
            ep, "Upstream / native source returned no data.", _hint), status_code=502)

    payload = apply_brand(mark(payload, source, ep))
    ms = int((time.time() - started) * 1000)

    # ?format=text -> ready to share card (Telegram / WhatsApp friendly)
    fmt = (request.query_params.get("format") or "").lower()
    if fmt in ("text", "txt", "card", "plain") and isinstance(payload, dict) and payload.get("formatted"):
        log_request(ep["path"], key_used, params, source + "+text", 200, ms, client_ip(request))
        return PlainTextResponse(payload["formatted"])

    log_request(ep["path"], key_used, params, source, 200, ms, client_ip(request))
    skip_cache = isinstance(payload, dict) and payload.pop("_no_cache", False)
    if get_setting("cache_enabled", "1") == "1" and ttl and not skip_cache:
        cache_set(cache_key, ep["path"], payload)
    return JSONResponse(payload, headers={"X-Source": source, "X-Response-Time": f"{ms}ms"})


def urllib_safe(params: Dict[str, Any]) -> str:
    import urllib.parse
    return urllib.parse.urlencode(sorted((k, str(v)) for k, v in params.items() if v not in (None, "")))


def mark(payload: Any, source: str, ep: Dict[str, Any]) -> Any:
    """Add small meta block without destroying upstream payload shape."""
    if not isinstance(payload, dict):
        return payload
    try:
        payload["_meta"] = {
            "endpoint": ep["path"],
            "name": ep["name"],
            "source": source,
            "server_time_ist": now_ist(),
            "api_version": APP_VERSION,
            "powered_by": brand(),
        }
        if _UPSTREAM_STATE.get("key_ok") is False:
            payload["_meta"]["upstream"] = ("key invalid — native data use hua; "
                                            "SETTING_UPSTREAM_KEY lagao live data ke liye")
    except Exception:
        pass
    return payload


# =====================================================================
# ROUTE REGISTRATION (all 42 endpoints)
# =====================================================================
def make_handler(ep: Dict[str, Any]):
    async def handler(request: Request, **kwargs) -> JSONResponse:
        params = dict(request.query_params)
        if ep["path"] == "youtube-mp3" and not params.get("type"):
            params["endpoint_hint"] = "youtube-mp3"
        return await run_endpoint(request, ep, extra_params=params)

    sig = [inspect.Parameter("request", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=Request)]
    sig.append(inspect.Parameter(
        "key", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=str,
        default=Query(DEMO_KEY, description=f"API key (default: {DEMO_KEY})")))
    for p in ep["params"]:
        desc = f"Example: {p['sample']}" + (" (required)" if p.get("required") else "")
        sig.append(inspect.Parameter(
            p["name"], inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=str,
            default=Query(None, description=desc)))
    handler.__signature__ = inspect.Signature(sig)
    handler.__name__ = f"api_{ep['path'].replace('-', '_')}"
    handler.__doc__ = ep["desc"]
    return handler


for _ep in ENDPOINTS:
    app.get(f"/api/{_ep['path']}", tags=[_ep["category"]], summary=_ep["name"])(make_handler(_ep))

# =====================================================================
# ADMIN / MANAGEMENT API  (used by the dashboard)
# =====================================================================
def admin_authorized(request: Request) -> bool:
    token = (request.headers.get("x-admin-token", "") or request.query_params.get("token", "")
             or request.headers.get("authorization", "").removeprefix("Bearer ").strip())
    if not token:
        return False
    if token == get_setting("admin_password", ADMIN_PASSWORD):
        return True
    # v2.6.6: MASTER_API_KEY env wali key se bhi admin access — env kabhi nahi badalta,
    # isliye password bhool jane / badal jane par bhi dashboard kabhi lock na ho.
    return bool(MASTER_API_KEYS) and token in MASTER_API_KEYS


def require_admin(request: Request):
    if not admin_authorized(request):
        raise HTTPException(status_code=401, detail="Admin login required")


from fastapi import Body, HTTPException  # noqa: E402  (imported late to keep the file readable)


@app.post("/admin/login")
async def admin_login(request: Request, payload: Dict[str, Any] = Body(default={})):
    password = payload.get("password", "")
    if password == get_setting("admin_password", ADMIN_PASSWORD) or (MASTER_API_KEYS and password in MASTER_API_KEYS):
        return {"success": True, "message": "Login ok",
                "note": "MASTER_API_KEY se login" if (MASTER_API_KEYS and password in MASTER_API_KEYS) else ""}
    return JSONResponse({"success": False, "error": "Wrong password"}, status_code=401)


@app.get("/admin/upstream/test")
async def admin_upstream_test(request: Request, path: str = "ip-v2", probe: str = "8.8.8.8"):
    """Upstream (reference hub) ki key theek hai ya nahi — ek call me pata karo."""
    require_admin(request)
    res = await upstream_self_test(path, probe)
    res["state"] = upstream_key_status()
    return res


@app.get("/admin/env/status")
async def admin_env_status(request: Request):
    """Render ke env vars service ko mil rahe hain ya nahi — ek call me (sirf set/MISSING, value nahi)."""
    require_admin(request)
    names = [
        "ADMIN_PASSWORD", "MASTER_API_KEY", "GITHUB_BACKUP_REPO", "GITHUB_BACKUP_TOKEN",
        "GITHUB_BACKUP_MINUTES", "UPSTREAM_BASE", "UPSTREAM_KEY", "UPSTREAM_ENABLED",
        "VEHICLE_PROVIDER_URL", "VEHICLE_PROVIDER_KEY", "VEHICLE_PROVIDER_PARAM",
        "NUMINFO_PROVIDER_URL", "NUMINFO_PROVIDER_KEY", "DB_PATH", "PORT", "BRAND_TAG",
        "UPI_ID", "HIBP_API_KEY", "DEMO_KEY", "PYTHON_VERSION",
    ]
    return {"success": True,
            "env": {n: ("set ✅" if os.environ.get(n) not in (None, "") else "MISSING ❌") for n in names},
            "note": ("Jo env var yahan MISSING hai wo service ko nahi mila — Render -> Environment me "
                     "naam bilkul sahi likha hai ya nahi dekho, phir Save karke restart karo."),
            "version": APP_VERSION}


@app.get("/admin/upstream/status")
async def admin_upstream_status(request: Request):
    require_admin(request)
    return {"success": True, "upstream": upstream_key_status(),
            "base": get_setting("upstream_base", DEFAULT_UPSTREAM),
            "enabled": get_setting("upstream_enabled", "1") == "1"}


@app.post("/admin/backup/github")
async def admin_backup_github(request: Request):
    """Abhi turant DB ka GitHub backup banao (auto har BACKUP_MINUTES minute me bhi hota hai)."""
    require_admin(request)
    res = backup_db_to_github("manual")
    res["state"] = dict(_BACKUP_STATE)
    return res


@app.post("/admin/restore/github")
async def admin_restore_github(request: Request):
    """GitHub backup se DB wapas lao (service restart karne par saaf lagu hota hai)."""
    require_admin(request)
    res = restore_db_from_github()
    res["hint"] = "Restart the service (Render -> Manual Deploy) so poora fresh state load ho."
    return res


@app.get("/admin/overview")
async def admin_overview(request: Request):
    require_admin(request)
    with db() as conn:
        keys = conn.execute("SELECT COUNT(*) c FROM api_keys").fetchone()["c"]
        records = conn.execute("SELECT COUNT(*) c FROM custom_records").fetchone()["c"]
        cache = conn.execute("SELECT COUNT(*) c FROM response_cache").fetchone()["c"]
        logs = conn.execute("SELECT COUNT(*) c FROM request_logs").fetchone()["c"]
        today = now_ist("%Y-%m-%d")
        today_hits = conn.execute(
            "SELECT COUNT(*) c FROM request_logs WHERE ts LIKE ?", (f"{today}%",)).fetchone()["c"]
        top = conn.execute(
            "SELECT endpoint, COUNT(*) c FROM request_logs GROUP BY endpoint ORDER BY c DESC LIMIT 8"
        ).fetchall()
        errors = conn.execute(
            "SELECT COUNT(*) c FROM request_logs WHERE status>=400").fetchone()["c"]
    return {
        "success": True,
        "endpoints": len(ENDPOINTS),
        "api_keys": keys,
        "records": records,
        "cache_entries": cache,
        "total_requests": logs,
        "requests_today": today_hits,
        "errors": errors,
        "top_endpoints": [{"endpoint": r["endpoint"], "count": r["c"]} for r in top],
        "upstream": get_setting("upstream_base", DEFAULT_UPSTREAM),
        "server_time_ist": now_ist(),
    }


@app.get("/admin/records")
async def admin_list_records(request: Request, category: str = "", q: str = "", limit: int = 100):
    require_admin(request)
    sql = "SELECT * FROM custom_records"
    args: List[Any] = []
    where = []
    if category:
        where.append("category=?")
        args.append(category)
    if q:
        where.append("(key_value LIKE ? OR data LIKE ? OR note LIKE ?)")
        args += [f"%{q}%", f"%{q}%", f"%{q}%"]
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(min(limit, 1000))
    with db() as conn:
        rows = conn.execute(sql, args).fetchall()
    return {"success": True, "count": len(rows),
            "records": [dict(r) for r in rows]}


@app.post("/admin/records")
async def admin_add_record(request: Request, payload: Dict[str, Any] = Body(default={})):
    require_admin(request)
    category = (payload.get("category") or "general").strip().lower()
    if category in DISABLED_RECORD_CATEGORIES:
        return JSONResponse({"success": False, "status": "disabled",
                             "error": "Leak/phone/vehicle personal-record storage privacy ke liye disabled hai."},
                            status_code=410)
    key_value = str(payload.get("key_value") or "").strip().lower()
    data = payload.get("data")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            data = {"value": data}
    if not key_value:
        return JSONResponse({"success": False, "error": "key_value is required"}, status_code=400)
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO custom_records(category,key_value,data,note,source,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (category, key_value, json.dumps(data, ensure_ascii=False),
             payload.get("note", ""), payload.get("source", "dashboard"), now_ist(), now_ist()))
        conn.commit()
        new_id = cur.lastrowid
    return {"success": True, "id": new_id}


@app.put("/admin/records/{record_id}")
async def admin_update_record(record_id: int, request: Request,
                              payload: Dict[str, Any] = Body(default={})):
    require_admin(request)
    new_category = payload.get("category")
    if new_category is not None and str(new_category).strip().lower() in DISABLED_RECORD_CATEGORIES:
        return JSONResponse({"success": False, "status": "disabled",
                             "error": "Leak/phone/vehicle personal-record storage privacy ke liye disabled hai."},
                            status_code=410)
    data = payload.get("data")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            data = {"value": data}
    with db() as conn:
        existing = conn.execute("SELECT category FROM custom_records WHERE id=?", (record_id,)).fetchone()
        if existing and str(existing["category"]).strip().lower() in DISABLED_RECORD_CATEGORIES:
            return JSONResponse({"success": False, "status": "disabled",
                                 "error": "Purane private-record rows edit nahi hote; unhe delete kar sakte hain."},
                                status_code=410)
        conn.execute(
            "UPDATE custom_records SET category=COALESCE(?,category), key_value=COALESCE(?,key_value),"
            " data=COALESCE(?,data), note=COALESCE(?,note), updated_at=? WHERE id=?",
            (new_category, payload.get("key_value"),
             json.dumps(data, ensure_ascii=False) if data is not None else None,
             payload.get("note"), now_ist(), record_id))
        conn.commit()
    return {"success": True}


@app.delete("/admin/records/{record_id}")
async def admin_delete_record(record_id: int, request: Request):
    require_admin(request)
    with db() as conn:
        conn.execute("DELETE FROM custom_records WHERE id=?", (record_id,))
        conn.commit()
    return {"success": True}


@app.post("/admin/records/import")
async def admin_import_records(request: Request, payload: Dict[str, Any] = Body(default={})):
    require_admin(request)
    text = payload.get("csv") or payload.get("text") or ""
    category = (payload.get("category") or "general").strip().lower()
    if category in DISABLED_RECORD_CATEGORIES:
        return JSONResponse({"success": False, "status": "disabled",
                             "error": "Leak/phone/vehicle personal-record import privacy ke liye disabled hai."},
                            status_code=410)
    added = 0
    reader = csv.reader(io.StringIO(text))
    with db() as conn:
        for row in reader:
            if not row or not row[0].strip():
                continue
            if len(row) == 1:
                key_value, data, note = row[0].strip(), {}, ""
            elif len(row) == 2:
                key_value, data, note = row[0].strip(), {"value": row[1].strip()}, ""
            else:
                key_value = row[0].strip()
                raw = row[1].strip()
                note = row[2].strip() if len(row) > 2 else ""
                try:
                    data = json.loads(raw)
                except Exception:
                    data = {"value": raw}
            conn.execute(
                "INSERT INTO custom_records(category,key_value,data,note,source,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?)",
                (category, key_value.lower(), json.dumps(data, ensure_ascii=False), note,
                 "import", now_ist(), now_ist()))
            added += 1
        conn.commit()
    return {"success": True, "imported": added}


@app.get("/admin/records/export")
async def admin_export_records(request: Request, category: str = ""):
    token = request.query_params.get("token", "")
    if token != get_setting("admin_password", ADMIN_PASSWORD):
        raise HTTPException(status_code=401, detail="Admin login required")
    with db() as conn:
        if category:
            rows = conn.execute("SELECT * FROM custom_records WHERE category=? ORDER BY id",
                                (category,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM custom_records ORDER BY id").fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "category", "key_value", "data", "note", "source", "created_at"])
    for r in rows:
        writer.writerow([r["id"], r["category"], r["key_value"], r["data"], r["note"],
                         r["source"], r["created_at"]])
    return PlainTextResponse(output.getvalue(),
                             headers={"Content-Disposition": "attachment; filename=osint_records.csv"})


def _key_dict(row: sqlite3.Row) -> Dict[str, Any]:
    rec = dict(row)
    rec["days_left"] = days_left(rec.get("expires_at"))
    rec["is_expired"] = bool(rec.get("expires_at") and now_ist() > rec["expires_at"])
    rec["bound_device_count"] = len([d for d in (rec.get("bound_devices") or "").split(",") if d])
    return rec


@app.get("/admin/keys")
async def admin_list_keys(request: Request):
    require_admin(request)
    with db() as conn:
        rows = conn.execute("SELECT * FROM api_keys ORDER BY id DESC").fetchall()
    return {"success": True, "keys": [_key_dict(r) for r in rows],
            "endpoints": [e["path"] for e in ENDPOINTS]}


@app.post("/admin/keys")
async def admin_create_key(request: Request, payload: Dict[str, Any] = Body(default={})):
    """Create a sellable key: name, validity days, plan (endpoints), device lock."""
    require_admin(request)
    import string
    alphabet = string.ascii_letters + string.digits
    new_key = (payload.get("custom_key") or "").strip()
    if not new_key:
        new_key = "osint-" + "".join(secrets.choice(alphabet) for _ in range(24))
    days = int(payload.get("days") or 0)
    expires_at = (datetime.now(IST) + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S") if days > 0 else None
    endpoints = safe_endpoint_allowlist(payload.get("allowed_endpoints") or payload.get("plan") or "*")
    if endpoints is None:
        return JSONResponse({"success": False, "status": "disabled",
                             "error": "Privacy-disabled ya unknown endpoint ko plan me bech nahi sakte."},
                            status_code=410)
    with db() as conn:
        exists = conn.execute("SELECT id FROM api_keys WHERE api_key=?", (new_key,)).fetchone()
        if exists:
            return JSONResponse({"success": False, "error": "Key already exists"}, status_code=400)
        cur = conn.execute(
            "INSERT INTO api_keys(api_key,name,note,is_active,requests,created_at,expires_at,"
            "allowed_endpoints,device_lock,bound_devices,max_devices,rate_limit,customer,price)"
            " VALUES(?,?,?,1,0,?,?,?,?,?,?,?,?,?)",
            (new_key, payload.get("name", "") or payload.get("customer", ""),
             payload.get("note", ""), now_ist(), expires_at, endpoints,
             int(payload.get("device_lock") or 0), "",
             max(1, int(payload.get("max_devices") or 1)),
             int(payload.get("rate_limit") or 0),
             payload.get("customer", "") or payload.get("name", ""),
             str(payload.get("price", ""))))
        conn.commit()
        new_id = cur.lastrowid
    return {"success": True, "api_key": new_key, "id": new_id, "expires_at": expires_at,
            "days": days, "allowed_endpoints": endpoints}


@app.post("/admin/keys/{key_id}/extend")
async def admin_extend_key(key_id: int, request: Request,
                           payload: Dict[str, Any] = Body(default={})):
    """Renew a key: add N days (from today, or from its old expiry if still valid)."""
    require_admin(request)
    days = int(payload.get("days") or 30)
    with db() as conn:
        row = conn.execute("SELECT * FROM api_keys WHERE id=?", (key_id,)).fetchone()
        if not row:
            return JSONResponse({"success": False, "error": "Key not found"}, status_code=404)
        current = row["expires_at"]
        base = datetime.now(IST).replace(tzinfo=None)
        if current:
            try:
                old = datetime.strptime(current, "%Y-%m-%d %H:%M:%S")
                if old > base:
                    base = old
            except Exception:
                pass
        new_expiry = (base + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("UPDATE api_keys SET expires_at=?, is_active=1 WHERE id=?", (new_expiry, key_id))
        conn.commit()
    return {"success": True, "expires_at": new_expiry, "days_added": days}


@app.post("/admin/keys/{key_id}/plan")
async def admin_set_plan(key_id: int, request: Request,
                         payload: Dict[str, Any] = Body(default={})):
    """Change plan (allowed endpoints), device lock, device limit, rate limit, expiry date."""
    require_admin(request)
    fields, values = [], []
    if payload.get("allowed_endpoints") is not None:
        endpoints = safe_endpoint_allowlist(payload["allowed_endpoints"])
        if endpoints is None:
            return JSONResponse({"success": False, "status": "disabled",
                                 "error": "Privacy-disabled ya unknown endpoint ko plan me bech nahi sakte."},
                                status_code=410)
        fields.append("allowed_endpoints=?")
        values.append(endpoints)
    if payload.get("device_lock") is not None:
        fields.append("device_lock=?")
        values.append(int(payload["device_lock"]))
    if payload.get("max_devices") is not None:
        fields.append("max_devices=?")
        values.append(max(1, int(payload["max_devices"])))
    if payload.get("rate_limit") is not None:
        fields.append("rate_limit=?")
        values.append(int(payload["rate_limit"]))
    if payload.get("expires_at") is not None:
        fields.append("expires_at=?")
        values.append(payload["expires_at"] or None)
    if payload.get("name") is not None:
        fields.append("name=?")
        values.append(payload["name"])
    if payload.get("customer") is not None:
        fields.append("customer=?")
        values.append(payload["customer"])
    if payload.get("price") is not None:
        fields.append("price=?")
        values.append(str(payload["price"]))
    if not fields:
        return {"success": True, "updated": 0}
    values.append(key_id)
    with db() as conn:
        conn.execute(f"UPDATE api_keys SET {', '.join(fields)} WHERE id=?", values)
        conn.commit()
    return {"success": True, "updated": len(fields)}


@app.post("/admin/keys/{key_id}/unbind")
async def admin_unbind_devices(key_id: int, request: Request):
    """Device lock reset: key ko kisi bhi naye device par chalne do."""
    require_admin(request)
    with db() as conn:
        conn.execute("UPDATE api_keys SET bound_devices='' WHERE id=?", (key_id,))
        conn.commit()
    return {"success": True}


@app.post("/admin/keys/{key_id}/toggle")
async def admin_toggle_key(key_id: int, request: Request):
    require_admin(request)
    with db() as conn:
        conn.execute("UPDATE api_keys SET is_active = CASE WHEN is_active=1 THEN 0 ELSE 1 END WHERE id=?",
                     (key_id,))
        conn.commit()
    return {"success": True}


@app.delete("/admin/keys/{key_id}")
async def admin_delete_key(key_id: int, request: Request):
    require_admin(request)
    with db() as conn:
        conn.execute("DELETE FROM api_keys WHERE id=?", (key_id,))
        conn.commit()
    return {"success": True}


@app.get("/admin/logs")
async def admin_logs(request: Request, limit: int = 200):
    require_admin(request)
    with db() as conn:
        rows = conn.execute("SELECT * FROM request_logs ORDER BY id DESC LIMIT ?",
                            (min(limit, 1000),)).fetchall()
    return {"success": True, "logs": [dict(r) for r in rows]}


@app.get("/admin/settings")
async def admin_get_settings(request: Request):
    require_admin(request)
    keys = ["upstream_base", "upstream_key", "upstream_enabled", "demo_key_enabled",
            "cache_ttl", "cache_enabled", "rate_limit_per_min", "github_token",
            "max_request_seconds", "brand_tag", "hibp_api_key", "upi_id", "upi_name",
            "telegram_support", "store_title", "store_tagline", "store_plans",
            "webhook_secret"]
    return {"success": True, "settings": {k: get_setting(k, "") for k in keys}}


@app.post("/admin/settings")
async def admin_save_settings(request: Request, payload: Dict[str, Any] = Body(default={})):
    require_admin(request)
    data = payload.get("settings") or payload
    for k, v in data.items():
        if k in ("upstream_base", "upstream_key", "upstream_enabled", "demo_key_enabled",
                 "cache_ttl", "cache_enabled", "rate_limit_per_min", "github_token",
                 "max_request_seconds", "brand_tag", "hibp_api_key", "admin_password",
                 "upi_id", "upi_name", "telegram_support", "store_title", "store_tagline",
                 "store_plans", "webhook_secret"):
            set_setting(k, str(v))
    return {"success": True}


@app.post("/admin/cache/clear")
async def admin_clear_cache(request: Request):
    require_admin(request)
    with db() as conn:
        conn.execute("DELETE FROM response_cache")
        conn.commit()
    return {"success": True}


@app.post("/admin/logs/clear")
async def admin_clear_logs(request: Request):
    require_admin(request)
    with db() as conn:
        conn.execute("DELETE FROM request_logs")
        conn.commit()
    return {"success": True}


# =====================================================================
# PUBLIC META ENDPOINTS
# =====================================================================
# =====================================================================
# RESELLERS  (dealers khud keys banayein)
# =====================================================================
def hash_pw(password: str) -> str:
    return hashlib.sha256((str(password) + "osint-hub-salt").encode()).hexdigest()


def init_extra_tables():
    with db() as conn:
        c = conn.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS resellers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            name TEXT DEFAULT '',
            telegram TEXT DEFAULT '',
            is_active INTEGER DEFAULT 1,
            max_days INTEGER DEFAULT 30,
            allowed_endpoints TEXT DEFAULT '*',
            credit INTEGER DEFAULT 0,
            keys_created INTEGER DEFAULT 0,
            created_at TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS reseller_tokens (
            token TEXT PRIMARY KEY, reseller_id INTEGER, created_at TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_code TEXT UNIQUE,
            customer_name TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            plan_name TEXT DEFAULT '',
            days INTEGER DEFAULT 30,
            endpoints TEXT DEFAULT '*',
            amount REAL DEFAULT 0,
            status TEXT DEFAULT 'pending',
            key_id INTEGER,
            utr TEXT DEFAULT '',
            payer TEXT DEFAULT '',
            created_at TEXT,
            paid_at TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            utr TEXT, amount REAL, remark TEXT, payer TEXT, raw TEXT,
            order_id INTEGER, status TEXT DEFAULT 'unmatched', received_at TEXT
        )""")
        # api_keys extra columns
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(api_keys)")}
        if "reseller_id" not in cols:
            conn.execute("ALTER TABLE api_keys ADD COLUMN reseller_id INTEGER DEFAULT 0")
        if "order_id" not in cols:
            conn.execute("ALTER TABLE api_keys ADD COLUMN order_id INTEGER DEFAULT 0")
        conn.commit()


def make_key(name: str, days: int, endpoints: str, device_lock: int = 0, max_devices: int = 1,
             customer: str = "", price: str = "", reseller_id: int = 0, order_id: int = 0,
             rate_limit: int = 0, custom_key: str = "") -> Dict[str, Any]:
    import string
    alphabet = string.ascii_letters + string.digits
    new_key = (custom_key or "").strip() or "osint-" + "".join(secrets.choice(alphabet) for _ in range(24))
    expires_at = (datetime.now(IST) + timedelta(days=int(days))).strftime("%Y-%m-%d %H:%M:%S") if int(days) > 0 else None
    with db() as conn:
        if conn.execute("SELECT id FROM api_keys WHERE api_key=?", (new_key,)).fetchone():
            return {"success": False, "error": "Key already exists"}
        cur = conn.execute(
            "INSERT INTO api_keys(api_key,name,note,is_active,requests,created_at,expires_at,"
            "allowed_endpoints,device_lock,bound_devices,max_devices,rate_limit,customer,price,"
            "reseller_id,order_id) VALUES(?,?,?,1,0,?,?,?,?,?,?,?,?,?,?,?)",
            (new_key, name or customer, "", now_ist(), expires_at, endpoints or "*",
             int(device_lock), "", max(1, int(max_devices)), int(rate_limit), customer or name,
             str(price), int(reseller_id), int(order_id)))
        conn.commit()
        kid = cur.lastrowid
    return {"success": True, "api_key": new_key, "id": kid, "expires_at": expires_at,
            "days": int(days), "allowed_endpoints": endpoints or "*"}


def reseller_by_token(token: str) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    with db() as conn:
        row = conn.execute(
            "SELECT r.* FROM reseller_tokens t JOIN resellers r ON r.id=t.reseller_id WHERE t.token=?",
            (token,)).fetchone()
    return dict(row) if row else None


def endpoint_subset_ok(child: str, parent: str) -> bool:
    if not parent or parent.strip() in ("*", "all", ""):
        return True
    allowed = {x.strip() for x in parent.split(",") if x.strip()}
    wanted = {x.strip() for x in (child or "*").split(",") if x.strip()}
    if "*" in wanted:
        return False
    return wanted.issubset(allowed)


@app.post("/reseller/login")
async def reseller_login(payload: Dict[str, Any] = Body(default={})):
    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""
    with db() as conn:
        row = conn.execute("SELECT * FROM resellers WHERE username=? AND password_hash=?",
                           (username, hash_pw(password))).fetchone()
    if not row:
        return JSONResponse({"success": False, "error": "Galat username/password"}, status_code=401)
    rec = dict(row)
    if not int(rec.get("is_active") or 0):
        return JSONResponse({"success": False, "error": "Ye reseller account band hai"}, status_code=403)
    token = secrets.token_hex(24)
    with db() as conn:
        conn.execute("INSERT INTO reseller_tokens(token,reseller_id,created_at) VALUES(?,?,?)",
                     (token, rec["id"], now_ist()))
        conn.commit()
    return {"success": True, "token": token,
            "reseller": {"id": rec["id"], "username": rec["username"], "name": rec["name"],
                         "credit": rec["credit"], "max_days": rec["max_days"],
                         "allowed_endpoints": rec["allowed_endpoints"]}}


def require_reseller(request: Request) -> Dict[str, Any]:
    token = request.headers.get("x-reseller-token", "") or request.query_params.get("token", "")
    rec = reseller_by_token(token)
    if not rec:
        raise HTTPException(status_code=401, detail="Reseller login required")
    return rec


@app.get("/reseller/me")
async def reseller_me(request: Request):
    rec = require_reseller(request)
    with db() as conn:
        row = conn.execute("SELECT * FROM resellers WHERE id=?", (rec["id"],)).fetchone()
        keys = conn.execute("SELECT * FROM api_keys WHERE reseller_id=? ORDER BY id DESC LIMIT 200",
                            (rec["id"],)).fetchall()
    return {"success": True,
            "reseller": {k: row[k] for k in ("id", "username", "name", "credit", "max_days",
                                             "allowed_endpoints", "keys_created", "is_active")},
            "keys": [_key_dict(k) for k in keys]}


@app.get("/reseller/keys")
async def reseller_keys(request: Request):
    rec = require_reseller(request)
    with db() as conn:
        rows = conn.execute("SELECT * FROM api_keys WHERE reseller_id=? ORDER BY id DESC LIMIT 200",
                            (rec["id"],)).fetchall()
    return {"success": True, "keys": [_key_dict(r) for r in rows],
            "credit": rec.get("credit"), "max_days": rec.get("max_days")}


@app.post("/reseller/keys")
async def reseller_create_key(request: Request, payload: Dict[str, Any] = Body(default={})):
    rec = require_reseller(request)
    with db() as conn:
        row = conn.execute("SELECT * FROM resellers WHERE id=?", (rec["id"],)).fetchone()
    if not int(row["is_active"] or 0):
        return JSONResponse({"success": False, "error": "Account disabled"}, status_code=403)
    if int(row["credit"] or 0) <= 0:
        return JSONResponse({"success": False,
                             "error": "Credit khatam - admin se credit lein (1 key = 1 credit)"},
                            status_code=402)
    days = int(payload.get("days") or 30)
    if days > int(row["max_days"] or 30):
        return JSONResponse({"success": False,
                             "error": f"Aap max {row['max_days']} din ka key bana sakte ho"},
                            status_code=403)
    endpoints = safe_endpoint_allowlist(payload.get("allowed_endpoints") or "*")
    if endpoints is None:
        return JSONResponse({"success": False, "status": "disabled",
                             "error": "Privacy-disabled ya unknown endpoint ko reseller plan me bech nahi sakte."},
                            status_code=410)
    if not endpoint_subset_ok(endpoints, row["allowed_endpoints"]):
        return JSONResponse({"success": False,
                             "error": "Ye endpoints aapke plan me nahi hain",
                             "your_endpoints": row["allowed_endpoints"]}, status_code=403)
    result = make_key(name=payload.get("name") or payload.get("customer") or "", days=days,
                      endpoints=endpoints, device_lock=int(payload.get("device_lock") or 0),
                      max_devices=int(payload.get("max_devices") or 1),
                      customer=payload.get("customer") or payload.get("name") or "",
                      price=str(payload.get("price") or ""), reseller_id=int(row["id"]))
    if result.get("success"):
        with db() as conn:
            conn.execute("UPDATE resellers SET credit=credit-1, keys_created=keys_created+1 WHERE id=?",
                         (row["id"],))
            conn.commit()
    return result


@app.post("/reseller/keys/{key_id}/extend")
async def reseller_extend_key(key_id: int, request: Request,
                              payload: Dict[str, Any] = Body(default={})):
    rec = require_reseller(request)
    days = int(payload.get("days") or 30)
    if days > int(rec.get("max_days") or 30):
        return JSONResponse({"success": False, "error": f"Max {rec.get('max_days')} din allowed"},
                            status_code=403)
    with db() as conn:
        row = conn.execute("SELECT * FROM api_keys WHERE id=? AND reseller_id=?",
                           (key_id, rec["id"])).fetchone()
        if not row:
            return JSONResponse({"success": False, "error": "Key nahi mili"}, status_code=404)
        current = row["expires_at"]
        base = datetime.now(IST).replace(tzinfo=None)
        if current:
            try:
                old = datetime.strptime(current, "%Y-%m-%d %H:%M:%S")
                if old > base:
                    base = old
            except Exception:
                pass
        new_expiry = (base + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("UPDATE api_keys SET expires_at=?, is_active=1 WHERE id=?", (new_expiry, key_id))
        conn.commit()
    return {"success": True, "expires_at": new_expiry}


@app.post("/reseller/keys/{key_id}/toggle")
async def reseller_toggle_key(key_id: int, request: Request):
    rec = require_reseller(request)
    with db() as conn:
        conn.execute("UPDATE api_keys SET is_active = CASE WHEN is_active=1 THEN 0 ELSE 1 END "
                     "WHERE id=? AND reseller_id=?", (key_id, rec["id"]))
        conn.commit()
    return {"success": True}


# ---------- admin: resellers ----------
@app.get("/admin/resellers")
async def admin_list_resellers(request: Request):
    require_admin(request)
    with db() as conn:
        rows = conn.execute("SELECT * FROM resellers ORDER BY id DESC").fetchall()
    return {"success": True, "resellers": [dict(r) for r in rows]}


@app.post("/admin/resellers")
async def admin_create_reseller(request: Request, payload: Dict[str, Any] = Body(default={})):
    require_admin(request)
    username = (payload.get("username") or "").strip().lower()
    password = payload.get("password") or ""
    if not username or not password:
        return JSONResponse({"success": False, "error": "username + password chahiye"}, status_code=400)
    reseller_endpoints = safe_endpoint_allowlist(payload.get("allowed_endpoints") or "*")
    if reseller_endpoints is None:
        return JSONResponse({"success": False, "status": "disabled",
                             "error": "Privacy-disabled ya unknown endpoint reseller ko assign nahi kar sakte."},
                            status_code=410)
    with db() as conn:
        if conn.execute("SELECT id FROM resellers WHERE username=?", (username,)).fetchone():
            return JSONResponse({"success": False, "error": "Username already exists"}, status_code=400)
        conn.execute("INSERT INTO resellers(username,password_hash,name,telegram,is_active,max_days,"
                     "allowed_endpoints,credit,keys_created,created_at) VALUES(?,?,?,?,1,?,?,?,0,?)",
                     (username, hash_pw(password), payload.get("name", ""),
                      payload.get("telegram", ""), int(payload.get("max_days") or 30),
                      reseller_endpoints,
                      int(payload.get("credit") or 0), now_ist()))
        conn.commit()
    return {"success": True, "username": username, "password": password,
            "credit": int(payload.get("credit") or 0)}


@app.post("/admin/resellers/{rid}/credit")
async def admin_reseller_credit(rid: int, request: Request,
                                payload: Dict[str, Any] = Body(default={})):
    require_admin(request)
    amount = int(payload.get("amount") or 0)
    with db() as conn:
        conn.execute("UPDATE resellers SET credit=credit+? WHERE id=?", (amount, rid))
        conn.commit()
        row = conn.execute("SELECT credit FROM resellers WHERE id=?", (rid,)).fetchone()
    return {"success": True, "credit": row["credit"] if row else 0}


@app.post("/admin/resellers/{rid}/plan")
async def admin_reseller_plan(rid: int, request: Request,
                              payload: Dict[str, Any] = Body(default={})):
    require_admin(request)
    endpoints = None
    if payload.get("allowed_endpoints") is not None:
        endpoints = safe_endpoint_allowlist(payload["allowed_endpoints"])
        if endpoints is None:
            return JSONResponse({"success": False, "status": "disabled",
                                 "error": "Privacy-disabled ya unknown endpoint reseller ko assign nahi kar sakte."},
                                status_code=410)
    with db() as conn:
        if payload.get("max_days") is not None:
            conn.execute("UPDATE resellers SET max_days=? WHERE id=?",
                         (int(payload["max_days"]), rid))
        if endpoints is not None:
            conn.execute("UPDATE resellers SET allowed_endpoints=? WHERE id=?", (endpoints, rid))
        conn.commit()
    return {"success": True}


@app.post("/admin/resellers/{rid}/toggle")
async def admin_reseller_toggle(rid: int, request: Request):
    require_admin(request)
    with db() as conn:
        conn.execute("UPDATE resellers SET is_active = CASE WHEN is_active=1 THEN 0 ELSE 1 END WHERE id=?",
                     (rid,))
        conn.commit()
    return {"success": True}


@app.delete("/admin/resellers/{rid}")
async def admin_delete_reseller(rid: int, request: Request):
    require_admin(request)
    with db() as conn:
        conn.execute("DELETE FROM resellers WHERE id=?", (rid,))
        conn.commit()
    return {"success": True}


# =====================================================================
# ORDERS + UPI PAYMENTS (auto key activation)
# =====================================================================
def gen_order_code() -> str:
    return "OS" + "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(8))


def store_plans() -> List[Dict[str, Any]]:
    """Only advertise working endpoints; filter privacy-disabled paths from saved plans too."""
    raw = get_setting("store_plans", "")
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, list) and data:
                safe_plans = []
                for item in data:
                    if not isinstance(item, dict):
                        continue
                    plan = dict(item)
                    raw_eps = plan.get("endpoints", "*")
                    if isinstance(raw_eps, (list, tuple, set)):
                        eps = [str(x).strip() for x in raw_eps if str(x).strip()]
                        wildcard = False
                    else:
                        raw_eps = str(raw_eps or "*").strip()
                        wildcard = raw_eps in ("*", "all", "")
                        eps = [] if wildcard else [x.strip() for x in raw_eps.split(",") if x.strip()]
                    if not wildcard:
                        includes_disabled = any(x in PRIVACY_DISABLED_ENDPOINTS or x.startswith("vehicle-")
                                                for x in eps)
                        if includes_disabled:
                            continue
                        if not eps:
                            continue
                        plan["endpoints"] = ",".join(eps)
                    else:
                        note = "Privacy-restricted personal-data/Aadhaar/email/vehicle-owner routes disabled hain."
                        description = str(plan.get("description") or "").strip()
                        if note not in description:
                            plan["description"] = (description + " " + note).strip()
                    safe_plans.append(plan)
                if safe_plans:
                    return safe_plans
        except Exception:
            pass
    return [
        {"id": "trial", "name": "Utility Trial", "days": 3,
         "endpoints": "imei,ip-v1,ifsc,pincode", "price": 29,
         "description": "3 din · TAC-only device hint, IP, IFSC aur pincode utilities"},
        {"id": "utility", "name": "Utility Pack", "days": 30,
         "endpoints": "imei,ip-v1,ip-v2,ip-v3,ifsc,pincode,youtube-download,ytdl,youtube-mp3",
         "price": 100, "description": "30 din · available utility APIs (₹100/month per key)"},
        {"id": "full", "name": "Full Access", "days": 30, "endpoints": "*",
         "price": 299,
         "description": "30 din · currently available APIs; privacy-restricted lookups disabled"},
        {"id": "reseller", "name": "Reseller Pack", "days": 365, "endpoints": "*",
         "price": 999,
         "description": "1 saal · available APIs ke reseller access; privacy-restricted lookups disabled"},
    ]


@app.get("/api/plans")
async def api_plans():
    upi = get_setting("upi_id", "") or ""
    return {"success": True, "upi_id": upi, "upi_name": get_setting("upi_name", "") or "OSINT API Hub",
            "support": get_setting("telegram_support", "") or brand(),
            "brand": brand(), "plans": store_plans()}


@app.post("/api/create-order")
async def api_create_order(request: Request, payload: Dict[str, Any] = Body(default={})):
    """Customer plan chunta hai → order banta hai + UPI link/QR milta hai."""
    plan_id = (payload.get("plan") or payload.get("plan_id") or "").strip()
    plans = store_plans()
    plan = next((p for p in plans if p.get("id") == plan_id), None)
    if not plan and payload.get("days"):
        plan = {"id": "custom", "name": payload.get("plan_name") or "Custom Plan",
                "days": int(payload.get("days") or 30),
                "endpoints": (payload.get("endpoints") or "*").strip(),
                "price": float(payload.get("amount") or 0)}
    if not plan:
        return JSONResponse({"success": False, "error": "Plan nahi mila", "available": plans},
                            status_code=400)
    # 🔒 v2.1 SECURITY: client ka bheja amount sirf standard plans me IGNORE hota hai.
    if plan.get("id") == "custom":
        rate = float(get_setting("custom_price_per_day", "0") or 0)
        days = int(plan.get("days") or 30)
        if rate <= 0:
            return JSONResponse({
                "success": False,
                "error": "Custom plan ka price admin se confirm karo (ya store ke plan list se chuno).",
                "hint": "Dashboard → Settings → store_plans me plan add karo, ya custom_price_per_day set karo.",
                "available": plans,
            }, status_code=400)
        amount = round(rate * days, 2)
    else:
        amount = float(plan.get("price") or 0)      # server-side price (client amount ignore)
        if float(payload.get("amount") or 0) > 0 and abs(float(payload.get("amount")) - amount) > 0.01:
            amount = float(plan.get("price") or 0)  # chhoti rakam se order banane ki koshish block
    code = gen_order_code()
    with db() as conn:
        cur = conn.execute("INSERT INTO orders(order_code,customer_name,phone,plan_name,days,endpoints,"
                           "amount,status,created_at) VALUES(?,?,?,?,?,?,?,'pending',?)",
                           (code, (payload.get("name") or "")[:120], (payload.get("phone") or "")[:40],
                            plan.get("name", ""), int(plan.get("days") or 30),
                            (plan.get("endpoints") or "*"), amount, now_ist()))
        conn.commit()
        oid = cur.lastrowid
    upi_id = get_setting("upi_id", "") or ""
    upi_name = get_setting("upi_name", "") or "OSINT API Hub"
    upi_link = (f"upi://pay?pa={urllib.parse.quote(upi_id)}&pn={urllib.parse.quote(upi_name)}"
                f"&am={amount}&cu=INR&tn={code}") if upi_id else ""
    qr_url = (f"https://api.qrserver.com/v1/create-qr-code/?size=320x320&data="
              f"{urllib.parse.quote(upi_link)}") if upi_link else ""
    return {"success": True, "order_code": code, "order_id": oid,
            "plan": plan.get("name"), "days": int(plan.get("days") or 30),
            "endpoints": plan.get("endpoints"), "amount": amount,
            "upi_id": upi_id, "upi_link": upi_link, "qr_url": qr_url,
            "status": "pending",
            "message": (f"₹{amount} UPI ID {upi_id} par bhejein, remark/notes me ye code zaroor "
                        f"likhein: {code}"),
            "check_url": f"/api/order-status?code={code}",
            "powered_by": brand()}


@app.get("/api/order-status")
async def api_order_status(code: str = ""):
    code = (code or "").strip().upper()
    if not code:
        return JSONResponse({"success": False, "error": "order code chahiye"}, status_code=400)
    with db() as conn:
        row = conn.execute("SELECT * FROM orders WHERE order_code=?", (code,)).fetchone()
        if not row:
            return JSONResponse({"success": False, "error": "Order nahi mila"}, status_code=404)
        api_key = ""
        if row["key_id"]:
            k = conn.execute("SELECT api_key FROM api_keys WHERE id=?", (row["key_id"],)).fetchone()
            if k:
                api_key = k["api_key"]
    return {"success": True, "order_code": code, "status": row["status"],
            "plan": row["plan_name"], "days": row["days"], "amount": row["amount"],
            "created_at": row["created_at"], "paid_at": row["paid_at"], "utr": row["utr"],
            "api_key": api_key if row["status"] == "paid" else "",
            "powered_by": brand()}


def find_pending_order(remark: str, amount: float) -> Optional[sqlite3.Row]:
    remark = (remark or "").upper()
    with db() as conn:
        rows = conn.execute("SELECT * FROM orders WHERE status='pending' ORDER BY id").fetchall()
    for row in rows:
        if row["order_code"] and row["order_code"].upper() in remark:
            return row
    try:
        amt = float(amount or 0)
    except Exception:
        amt = 0
    if amt > 0:
        # 🔒 v2.1: sirf EXACT match, aur wo bhi tab jab ek hi pending order ho (galat match na ho)
        exact = []
        for row in rows:
            try:
                if abs(float(row["amount"]) - amt) < 0.01:
                    exact.append(row)
            except Exception:
                continue
        if len(exact) == 1:
            return exact[0]
    return None


def activate_order(order: sqlite3.Row, utr: str = "", payer: str = "") -> Dict[str, Any]:
    result = make_key(name=order["customer_name"] or "Customer", days=int(order["days"] or 30),
                      endpoints=order["endpoints"] or "*", device_lock=0, max_devices=1,
                      customer=order["customer_name"] or "Customer",
                      price=str(order["amount"]), order_id=int(order["id"]))
    if not result.get("success"):
        return result
    with db() as conn:
        conn.execute("UPDATE orders SET status='paid', key_id=?, utr=?, payer=?, paid_at=? WHERE id=?",
                     (result["id"], utr, payer, now_ist(), order["id"]))
        conn.commit()
    return {"success": True, "order_code": order["order_code"], "status": "paid",
            "api_key": result["api_key"], "expires_at": result["expires_at"],
            "days": result["days"], "plan": order["plan_name"],
            "customer": order["customer_name"]}


@app.post("/webhook/payment")
async def webhook_payment(request: Request, payload: Dict[str, Any] = Body(default={})):
    """UPI payment aate hi key auto-activate.
    Body: {"utr":"...", "amount":100, "remark":"OSXXXXXXXX", "payer":"NAME", "raw":"sms text"}
    """
    secret = get_setting("webhook_secret", "")
    if secret:
        provided = request.headers.get("x-webhook-secret", "") or payload.get("secret", "")
        if provided != secret:
            return JSONResponse({"success": False, "error": "invalid webhook secret"}, status_code=401)
    elif not admin_authorized(request):
        # 🔒 v2.1 SECURITY: secret set na ho to webhook OPEN nahi rahega (warna koi bhi
        # fake payment bhej kar key activate kar sakta tha).
        return JSONResponse({
            "success": False,
            "error": "Webhook secure nahi hai — pehle webhook secret set karo.",
            "hint": "Dashboard → Settings → webhook_secret me ek lamba secret daalo, phir SMS forwarder me wahi bhejo. (Ya admin token ke saath call karo.)",
        }, status_code=401)
    amount = float(payload.get("amount") or 0)
    remark = str(payload.get("remark") or payload.get("note") or payload.get("tn") or "")
    utr = str(payload.get("utr") or payload.get("ref") or "")
    payer = str(payload.get("payer") or payload.get("from") or "")
    raw = str(payload.get("raw") or payload.get("sms") or "")

    order = find_pending_order(remark or raw, amount)
    with db() as conn:
        cur = conn.execute("INSERT INTO payments(utr,amount,remark,payer,raw,order_id,status,received_at)"
                           " VALUES(?,?,?,?,?,?,?,?)",
                           (utr, amount, remark, payer, raw,
                            int(order["id"]) if order else 0,
                            "matched" if order else "unmatched", now_ist()))
        conn.commit()
        pid = cur.lastrowid
    if not order:
        return {"success": True, "matched": False, "payment_id": pid,
                "message": "Payment save ho gaya, koi pending order match nahi hua "
                           "(admin dashboard se manually claim kar sakte ho)"}
    result = activate_order(order, utr=utr, payer=payer)
    result["matched"] = True
    result["payment_id"] = pid
    return result


UPI_SMS_PATTERNS = [
    r"(?:rs\.?|inr|₹)\s*([\d,]+(?:\.\d{1,2})?)",
    r"(?:upi[/ ]?ref(?:erence)?\s*(?:no|number)?[:.]?\s*)(\d{6,20})",
    r"(?:ref no|refno|utr)[:.]?\s*([A-Za-z0-9]{6,25})",
]


@app.post("/webhook/upi-sms")
async def webhook_upi_sms(request: Request, payload: Dict[str, Any] = Body(default={})):
    """Android SMS forwarder (Macrodroid/Tasker/HTTP Shortcuts) se raw SMS bhejein."""
    sms = str(payload.get("sms") or payload.get("text") or payload.get("message") or "")
    if not sms:
        return JSONResponse({"success": False, "error": "sms text chahiye"}, status_code=400)
    low = sms.lower()
    if "credited" not in low and "received" not in low:
        return {"success": True, "ignored": True, "message": "Credit SMS nahi lag raha"}
    amount = 0.0
    m = re.search(UPI_SMS_PATTERNS[0], low)
    if m:
        try:
            amount = float(m.group(1).replace(",", ""))
        except Exception:
            amount = 0.0
    utr = ""
    for pat in UPI_SMS_PATTERNS[1:]:
        mm = re.search(pat, sms, flags=re.I)
        if mm:
            utr = mm.group(1)
            break
    payer = ""
    pm = re.search(r"(?:from|by)\s+([A-Z][A-Za-z .]{2,40})", sms)
    if pm:
        payer = pm.group(1).strip()
    return await webhook_payment(request, {"amount": amount, "remark": sms.upper(), "utr": utr,
                                           "payer": payer, "raw": sms})


@app.post("/admin/orders/{oid}/mark-paid")
async def admin_mark_paid(oid: int, request: Request, payload: Dict[str, Any] = Body(default={})):
    require_admin(request)
    with db() as conn:
        row = conn.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
        if not row:
            return JSONResponse({"success": False, "error": "Order nahi mila"}, status_code=404)
        if row["status"] == "paid":
            return {"success": True, "already": True, "order_code": row["order_code"]}
    result = activate_order(row, utr=str(payload.get("utr") or "MANUAL"),
                            payer=str(payload.get("payer") or "manual"))
    return result


@app.get("/admin/orders")
async def admin_orders(request: Request, status: str = ""):
    require_admin(request)
    with db() as conn:
        if status:
            rows = conn.execute("SELECT * FROM orders WHERE status=? ORDER BY id DESC LIMIT 200",
                                (status,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 200").fetchall()
        pays = conn.execute("SELECT * FROM payments ORDER BY id DESC LIMIT 100").fetchall()
    return {"success": True, "orders": [dict(r) for r in rows], "payments": [dict(p) for p in pays]}


@app.get("/api/endpoints")
async def list_endpoints(request: Request):
    base = str(request.base_url).rstrip("/")
    return {
        "status": "active",
        "version": APP_VERSION,
        "developer": brand(),
        "powered_by": brand_line(),
        "telegram": brand(),
        "total_endpoints": len(ENDPOINTS),
        "demo_key": DEMO_KEY,
        "base_url": base,
        "endpoints": [{
            "path": f"/api/{e['path']}",
            "name": e["name"],
            "category": e["category"],
            "description": e["desc"],
            "params": [p["name"] for p in e["params"]],
            "example": f"{base}/api/{e['path']}?key={DEMO_KEY}&" +
                       "&".join(f"{p['name']}={p['sample']}" for p in e["params"]),
        } for e in ENDPOINTS],
    }


# =====================================================================
# BACKUP / RESTORE  (Render free tier ka disk ephemeral hai —
# restart pe data udd sakta hai, isliye JSON backup lo aur zaroorat
# padne par wapas restore kar do. Dashboard → Settings → Backup.)
# =====================================================================
BACKUP_TABLES = ["settings", "api_keys", "resellers", "reseller_tokens", "orders", "payments"]


@app.get("/admin/backup")
def admin_backup(request: Request, download: str = "1"):
    """Poora business data (settings, keys, resellers, orders, payments) JSON me."""
    require_admin(request)
    data = {"backup_version": 1, "created_at": now_ist(),
            "powered_by": brand(), "tables": {}}
    with db() as conn:
        for t in BACKUP_TABLES:
            try:
                rows = conn.execute(f"SELECT * FROM {t}").fetchall()
                data["tables"][t] = [dict(r) for r in rows]
            except Exception:  # noqa: BLE001
                data["tables"][t] = []
    from fastapi.responses import JSONResponse
    if download in ("1", "true", "yes"):
        fname = "osint-hub-backup-" + now_ist("%Y%m%d-%H%M") + ".json"
        return JSONResponse(content=data, headers={
            "Content-Disposition": f'attachment; filename="{fname}"'})
    return data


@app.post("/admin/restore")
def admin_restore(request: Request, payload: dict = Body(...)):
    """Backup JSON wapas daalo — purana data replace ho jayega."""
    require_admin(request)
    tables = payload.get("tables") or {}
    if not isinstance(tables, dict) or not tables:
        raise HTTPException(status_code=400, detail="Backup file galat hai (tables nahi mile)")
    restored = {}
    with db() as conn:
        for t, rows in tables.items():
            if t not in BACKUP_TABLES or not isinstance(rows, list):
                continue
            try:
                conn.execute(f"DELETE FROM {t}")
                for r in rows:
                    if not isinstance(r, dict):
                        continue
                    cols = ",".join(r.keys())
                    qs = ",".join("?" for _ in r)
                    conn.execute(f"INSERT OR REPLACE INTO {t} ({cols}) VALUES ({qs})",
                                 list(r.values()))
                restored[t] = len(rows)
            except Exception as e:  # noqa: BLE001
                restored[t] = f"error: {e}"
    return {"success": True, "restored": restored, "powered_by": brand()}


@app.get("/api/key-info")
async def api_key_info(request: Request, key: str = ""):
    """Customers can check their own key: plan, expiry, remaining days, usage."""
    ok, key_used, err = validate_key(key)
    if not ok:
        return JSONResponse({"success": False, "error": err}, status_code=401)
    rec = key_record(key_used) or {}
    allowed = (rec.get("allowed_endpoints") or "*").strip()
    permitted = sorted({x.strip() for x in allowed.split(",") if x.strip()}) if allowed not in ("*", "all") else ["ALL"]
    return {
        "success": True,
        "key": key_used if rec.get("is_demo") else key_used[:6] + "..." + key_used[-4:],
        "customer": rec.get("customer") or rec.get("name") or "",
        "plan": "ALL ENDPOINTS" if permitted == ["ALL"] else permitted,
        "allowed_endpoints": permitted,
        "expires_at_ist": rec.get("expires_at") or "Never (lifetime)",
        "days_left": days_left(rec.get("expires_at")),
        "status": ("expired" if (rec.get("expires_at") and now_ist() > rec["expires_at"])
                   else ("active" if int(rec.get("is_active", 1)) else "disabled")),
        "device_lock": bool(int(rec.get("device_lock") or 0)),
        "devices_bound": len([d for d in (rec.get("bound_devices") or "").split(",") if d]),
        "max_devices": int(rec.get("max_devices") or 1),
        "requests_used": rec.get("requests", 0),
        "last_used_at": rec.get("last_used_at") or "",
        "rate_limit_per_min": int(rec.get("rate_limit") or 0) or int(get_setting("rate_limit_per_min", "120")),
        "server_time_ist": now_ist(),
    }


@app.on_event("startup")
async def _startup_tac_index():
    """v2.4: TAC database index background me banao (255k rows ~2-4s)."""
    import threading as _th
    _th.Thread(target=_build_tac_index, daemon=True).start()


def _action_list() -> List[str]:
    """/health par saaf list: kya-kya set karna baaki hai (user ke liye)."""
    todo: List[str] = []
    if not MASTER_API_KEYS:
        todo.append("MASTER_API_KEY env lagao — tab aapki main API key restart par bhi chalti rahegi")
    elif not key_row_safe(MASTER_API_KEYS[0]):
        todo.append("MASTER_API_KEY set hai (theek hai) — DB me seed neeche rows me ho jayegi")
    if not (BACKUP_REPO and BACKUP_TOKEN):
        todo.append("GITHUB_BACKUP_REPO + GITHUB_BACKUP_TOKEN lagao — keys/records ka backup chalu ho jayega")
    if get_setting("admin_password", ADMIN_PASSWORD) == "admin123":
        todo.append("ADMIN_PASSWORD env lagao — dashboard default password se badal do (security)")
    if upstream_key_is_placeholder():
        todo.append("UPSTREAM_KEY lagao (agar aapke paas valid key hai) — tab GST/PAN ka live company "
                    "data aayega; abhi offline parsing chal raha hai (GSTIN valid/state/PAN type sab milta hai)")
    elif _UPSTREAM_STATE.get("key_ok") is False:
        todo.append("SETTING_UPSTREAM_KEY lagao — abhi upstream key invalid hai (GST/PAN live data nahi)")
    if not vehicle_provider().get("url"):
        todo.append("VEHICLE_PROVIDER_URL + KEY lagao — vehicle/challan live data chalu ho jayega")
    if not numinfo_provider().get("url"):
        todo.append("NUMINFO_PROVIDER_URL + KEY lagao — number carrier (operator/circle) live data")
    return todo


def key_row_safe(k: str) -> bool:
    try:
        return bool(key_row(k))
    except Exception:
        return False


@app.get("/health")
async def health():
    _vp = vehicle_provider()
    _np = numinfo_provider()
    return {"status": "ok", "time_ist": now_ist(), "version": APP_VERSION,
            "endpoints": len(ENDPOINTS), "database": os.path.abspath(DB_PATH),
            "providers": {
                "vehicle": "on" if _vp.get("url") else "off (VEHICLE_PROVIDER_URL set karo)",
                "carrier": "on" if _np.get("url") else "off (NUMINFO_PROVIDER_URL set karo)",
            },
            "upstream": upstream_key_status(),
            "persistence": {
                "master_keys": len(MASTER_API_KEYS),
                "github_backup": (f"on -> {BACKUP_REPO}/{BACKUP_PATH}" if (BACKUP_REPO and BACKUP_TOKEN)
                                  else "off (GITHUB_BACKUP_REPO + GITHUB_BACKUP_TOKEN set karo)"),
                "restore": _BACKUP_STATE.get("restored"),
                "last_backup": _BACKUP_STATE.get("last_backup"),
                "last_backup_ok": _BACKUP_STATE.get("ok"),
                "last_error": _BACKUP_STATE.get("error"),
                "note": ("MASTER_API_KEY wali key restart par bhi chalti hai; baaki keys/records ke liye "
                         "GitHub backup ya Render disk chahiye"),
            },
            "security": {
                "admin_password_is_default": (get_setting("admin_password", ADMIN_PASSWORD) == "admin123"),
                "hint": ("KHATRA: dashboard password abhi default 'admin123' hai — Render env me "
                         "ADMIN_PASSWORD=apna-password lagao (warna restart ke baad koi bhi login kar sakta hai)")
                        if get_setting("admin_password", ADMIN_PASSWORD) == "admin123" else "ok",
            },
            "action_needed": _action_list(),
            "developer": brand(), "powered_by": brand_line()}


@app.get("/api/v1/health")
async def health_v1():
    return await health()


@app.get("/api/v1/search")
async def legacy_search(q: str = "", limit: int = 50):
    """Backwards compatible search across the custom database."""
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM custom_records WHERE key_value LIKE ? OR data LIKE ? LIMIT ?",
            (f"%{q}%", f"%{q}%", min(limit, 500))).fetchall()
    return {"success": True, "query": q, "count": len(rows), "results": [dict(r) for r in rows]}


# =====================================================================
# CUSTOM LANDING PAGE (API bechne ke liye website)
# =====================================================================
STORE_HTML = r"""<!DOCTYPE html>
<html lang="hi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root{--bg:#0b0f16;--panel:#141a24;--line:#263041;--text:#e8eef7;--muted:#93a1b5;
--acc:#4f8cff;--green:#2ecc71;--gold:#ffc93c;--pink:#ff5c8a}
*{box-sizing:border-box}
body{margin:0;background:radial-gradient(1200px 600px at 50% -10%,#1b3a6b 0%,transparent 60%),var(--bg);
color:var(--text);font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;font-size:17px;line-height:1.6}
.wrap{max-width:1000px;margin:0 auto;padding:18px}
header{padding:34px 18px 24px;text-align:center}
h1{margin:0;font-size:30px;letter-spacing:.5px}
.tag{color:var(--muted);margin-top:8px;font-size:16px}
.badge{display:inline-block;background:#1c2b45;border:1px solid var(--line);color:#9ec5ff;
border-radius:30px;padding:6px 14px;font-size:13px;margin:6px 4px 0}
.hero{display:flex;gap:10px;justify-content:center;flex-wrap:wrap;margin-top:18px}
.btn{background:var(--acc);color:#fff;border:none;border-radius:12px;padding:14px 22px;font-size:17px;
font-weight:700;cursor:pointer;text-decoration:none;display:inline-block}
.btn.ghost{background:transparent;border:1px solid var(--line);color:var(--text)}
.card{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:18px;margin:16px 0}
h2{font-size:21px;margin:0 0 6px}
.grid{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(250px,1fr))}
.plan{border:1px solid var(--line);border-radius:14px;padding:16px;background:#101722;position:relative}
.plan.pop{border-color:var(--gold);box-shadow:0 0 0 1px #ffc93c55}
.plan h3{margin:0 0 4px;font-size:19px}
.price{font-size:28px;font-weight:800;color:var(--gold)}
.price small{font-size:15px;color:var(--muted);font-weight:500}
.plan ul{padding-left:18px;margin:10px 0 14px;color:var(--muted);font-size:15px}
input,select{width:100%;background:#0c121b;color:var(--text);border:1px solid var(--line);
border-radius:11px;padding:13px;font-size:16px;margin:6px 0}
label{font-size:13px;color:var(--muted);display:block;margin-top:8px}
.paybox{background:#0c121b;border:1px dashed var(--line);border-radius:14px;padding:16px;margin-top:12px}
.code{font-family:ui-monospace,Menlo,monospace;background:#1b2534;border-radius:8px;padding:6px 10px;
display:inline-block;font-size:15px;letter-spacing:1px}
.qr{max-width:260px;width:100%;border-radius:12px;background:#fff;padding:8px;margin:10px auto;display:block}
pre{background:#0c121b;border:1px solid var(--line);border-radius:12px;padding:12px;overflow:auto;
white-space:pre-wrap;font-size:14px;max-height:320px}
.ok{color:var(--green)}.warn{color:var(--gold)}.bad{color:var(--pink)}
footer{text-align:center;color:var(--muted);font-size:14px;padding:30px 12px 50px}
a{color:#9ec5ff}
.step{display:flex;gap:10px;align-items:flex-start;margin:8px 0}
.num{background:#1c2b45;border-radius:50%;width:28px;height:28px;min-width:28px;display:flex;
align-items:center;justify-content:center;font-weight:700;font-size:14px}
</style>
</head>
<body>
<header>
  <h1>__TITLE__</h1>
  <div class="tag">__TAGLINE__</div>
  <div>
    <span class="badge">⚡ Powered by __BRAND__</span>
    <span class="badge">🔑 Instant key after UPI payment</span>
    <span class="badge">🤝 Reseller program</span>
  </div>
  <div class="hero">
    <a class="btn" href="#buy">🛒 Buy API Key</a>
    <a class="btn ghost" href="#demo">🔍 Live Demo</a>
    <a class="btn ghost" href="#check">🔑 Check My Key</a>
    <a class="btn ghost" href="__SUPPORT_LINK__">💬 Telegram Support</a>
  </div>
</header>

<div class="wrap">

<div class="card" id="buy">
  <h2>💎 Plans &amp; Pricing</h2>
  <div class="grid" id="plans"></div>

  <h2 style="margin-top:22px">🧾 Order banao</h2>
  <label>Plan</label>
  <select id="planSel" onchange="planChanged()"></select>
  <label>Aapka naam</label>
  <input id="custName" placeholder="Rohit Kumar">
  <label>WhatsApp / Phone (optional)</label>
  <input id="custPhone" placeholder="0000000000">
  <button class="btn" style="margin-top:12px;width:100%" onclick="createOrder()">💳 Pay &amp; Get Key</button>

  <div class="paybox" id="paybox" style="display:none">
    <div id="payInfo"></div>
    <img class="qr" id="qrImg" alt="UPI QR">
    <div style="text-align:center;margin:8px 0">
      <a class="btn" id="upiBtn" href="#">📲 Pay via UPI App</a>
    </div>
    <div class="step"><div class="num">1</div><div>UPI app se payment karein (QR scan ya button)</div></div>
    <div class="step"><div class="num">2</div><div>Remark/Notes me ye code zaroor likhein:<br>
      <span class="code" id="orderCode"></span>
      <button class="btn ghost" style="padding:6px 12px;font-size:14px;margin-left:6px" onclick="copyCode()">📋 Copy</button>
    </div></div>
    <div class="step"><div class="num">3</div><div>Payment ke turant baad key yahin aa jayegi (auto)</div></div>
    <div id="statusLine" style="margin-top:10px;font-weight:600"></div>
    <div id="keyBox"></div>
  </div>
</div>

<div class="card" id="demo">
  <h2>🔍 Live Demo (Demo key se)</h2>
  <label>Kya check karna hai</label>
  <select id="demoEp" onchange="demoChanged()">
    <option value="imei|imei|35301011">IMEI — TAC-only device hint</option>
    <option value="ip-v1|query|8.8.8.8">IP Info</option>
    <option value="ifsc|ifsc|SBIN0000001">IFSC branch info</option>
    <option value="pincode|pincode|110001">Pincode info</option>
    <option value="youtube-download|url|https://youtube.com/watch?v=X8X-XyK4CYE">YouTube Download</option>
  </select>
  <label>Value</label>
  <input id="demoVal" value="35301011">
  <button class="btn" style="margin-top:10px;width:100%" onclick="runDemo()">▶ Try Now</button>
  <pre id="demoOut">Yahan live result aayega…</pre>
</div>

<div class="card" id="check">
  <h2>🔑 Apni key check karein</h2>
  <input id="keyCheck" placeholder="osint-xxxxxxxxxxxx">
  <button class="btn" style="margin-top:10px;width:100%" onclick="checkKey()">Check Status</button>
  <pre id="keyOut">Expiry, plan aur usage yahan dikhega…</pre>
</div>

<div class="card">
  <h2>🤝 Reseller banein</h2>
  <p style="color:var(--muted);margin-top:0">Apne dealers/resellers ke liye alag panel hai — wo khud
  keys bana sakte hain (aap credit control karte hain).</p>
  <a class="btn ghost" href="/dashboard">Admin / Reseller Login</a>
  <a class="btn ghost" href="__SUPPORT_LINK__">Reseller slot ke liye contact</a>
</div>

</div>

<footer>
  ⚡ Powered by <b>__BRAND__</b> · API Developer / Telegram: <b>__BRAND__</b><br>
  <span style="font-size:13px">Data sirf verification ke liye — challan payment hamesha sarkari site par confirm karein.</span>
</footer>

<script>
const PLANS = __PLANS__;
const BRAND = "__BRAND__";
let currentOrder = null, pollTimer = null;

function renderPlans(){
  document.getElementById('plans').innerHTML = PLANS.map((p,i)=>
    '<div class="plan '+(i===1?'pop':'')+'"><h3>'+p.name+(i===1?' 🌟':'')+'</h3>'+
    '<div class="price">₹'+p.price+' <small>/ '+p.days+' din</small></div>'+
    '<ul><li>'+p.description+'</li><li>Endpoints: '+(p.endpoints==='*'?'Available APIs (restricted routes disabled)':p.endpoints.split(',').length+' APIs')+'</li>'+
    '<li>Instant activation · Device lock option</li></ul></div>').join('');
  const sel = document.getElementById('planSel');
  sel.innerHTML = PLANS.map(p=>'<option value="'+p.id+'">'+p.name+' — ₹'+p.price+' / '+p.days+' din</option>').join('');
}
function planChanged(){}
function demoChanged(){
  const v = document.getElementById('demoEp').value.split('|');
  document.getElementById('demoVal').value = v[2];
}
async function createOrder(){
  const payload = {plan: document.getElementById('planSel').value,
                   name: document.getElementById('custName').value,
                   phone: document.getElementById('custPhone').value};
  const r = await fetch('/api/create-order',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(payload)});
  const j = await r.json();
  if(!j.success){ alert(j.error||'Error'); return; }
  currentOrder = j;
  document.getElementById('paybox').style.display='';
  document.getElementById('orderCode').textContent = j.order_code;
  document.getElementById('payInfo').innerHTML =
    '<b>'+j.plan+'</b> · ₹'+j.amount+' · '+j.days+' din<br>'+
    'UPI ID: <span class="code">'+j.upi_id+'</span>';
  if(j.qr_url){ document.getElementById('qrImg').src = j.qr_url; }
  document.getElementById('upiBtn').href = j.upi_link || '#';
  document.getElementById('statusLine').innerHTML = '<span class="warn">⏳ Payment ka intezaar…</span>';
  document.getElementById('keyBox').innerHTML = '';
  if(pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(pollOrder, 8000);
  pollOrder();
}
function copyCode(){
  navigator.clipboard.writeText(currentOrder.order_code);
  alert('Code copy ho gaya: '+currentOrder.order_code);
}
async function pollOrder(){
  if(!currentOrder) return;
  const r = await fetch('/api/order-status?code='+currentOrder.order_code);
  const j = await r.json();
  if(j.status==='paid'){
    clearInterval(pollTimer);
    document.getElementById('statusLine').innerHTML = '<span class="ok">✅ Payment mil gaya — key active!</span>';
    document.getElementById('keyBox').innerHTML =
      '<pre>🔑 Aapki API KEY:\n'+j.api_key+'\n\n📦 Plan: '+j.plan+
      '\n⏳ Valid: '+j.days+' din\n💰 Paid: ₹'+j.amount+'\n\n'+
      'Safe example:\n'+location.origin+'/api/imei?key='+j.api_key+'&imei=35301011</pre>';
  }
}
async function runDemo(){
  const parts = document.getElementById('demoEp').value.split('|');
  const ep = parts[0], pname = parts[1];
  const val = document.getElementById('demoVal').value;
  const url = '/api/'+ep+'?key=Demo&'+pname+'='+encodeURIComponent(val)+'&format=text';
  document.getElementById('demoOut').textContent='Loading…';
  try{
    const r = await fetch(url);
    document.getElementById('demoOut').textContent = await r.text();
  }catch(e){ document.getElementById('demoOut').textContent='Error: '+e.message; }
}
async function checkKey(){
  const k = document.getElementById('keyCheck').value.trim();
  if(!k){ return; }
  const r = await fetch('/api/key-info?key='+encodeURIComponent(k));
  const j = await r.json();
  document.getElementById('keyOut').textContent = JSON.stringify(j,null,2);
}
renderPlans();
</script>
</body>
</html>
"""


def render_store(request: Request) -> HTMLResponse:
    title = get_setting("store_title", "OSINT API Hub") or "OSINT API Hub"
    tagline = get_setting("store_tagline", "") or "Utility APIs — privacy-restricted personal lookups disabled"
    support = get_setting("telegram_support", "") or brand()
    support_link = (f"https://t.me/{support.lstrip('@')}" if support.startswith("@")
                    else (support if support.startswith("http") else "#"))
    plans = store_plans()
    html = (STORE_HTML
            .replace("__TITLE__", title)
            .replace("__TAGLINE__", tagline)
            .replace("__BRAND__", brand())
            .replace("__SUPPORT_LINK__", support_link)
            .replace("__PLANS__", json.dumps(plans, ensure_ascii=False)))
    return HTMLResponse(html)


@app.get("/site")
async def site(request: Request):
    return render_store(request)


@app.get("/store")
async def store_alias(request: Request):
    return render_store(request)


# =====================================================================
# DASHBOARD (tablet friendly)
# =====================================================================
DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=5">
<title>OSINT API Hub - Dashboard</title>
<style>
:root{
  --bg:#0d1117; --panel:#161b22; --panel2:#1c2128; --line:#30363d;
  --text:#e6edf3; --muted:#8b949e; --accent:#2f81f7; --green:#3fb950;
  --red:#f85149; --yellow:#d29922; --purple:#a371f7;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
 font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
 font-size:16px;line-height:1.5;-webkit-text-size-adjust:100%}
header{background:linear-gradient(135deg,#1f6feb,#8957e5);padding:18px 20px;position:sticky;top:0;z-index:20;
 box-shadow:0 2px 12px rgba(0,0,0,.4)}
h1{margin:0;font-size:22px;font-weight:700}
.sub{opacity:.9;font-size:14px;margin-top:4px}
.wrap{padding:14px;max-width:1200px;margin:0 auto}
nav{display:flex;gap:8px;overflow-x:auto;padding:10px 14px;background:var(--panel);
 position:sticky;top:76px;z-index:15;border-bottom:1px solid var(--line)}
nav button{flex:0 0 auto;background:var(--panel2);border:1px solid var(--line);color:var(--text);
 padding:12px 18px;border-radius:12px;font-size:16px;font-weight:600;cursor:pointer}
nav button.active{background:var(--accent);border-color:var(--accent);color:#fff}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:14px;margin-bottom:14px}
.card h2{margin:0 0 10px;font-size:18px}
.grid{display:grid;gap:12px;grid-template-columns:repeat(auto-fill,minmax(280px,1fr))}
.stats{display:grid;gap:10px;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));margin-bottom:14px}
.stat{background:var(--panel2);border:1px solid var(--line);border-radius:12px;padding:12px;text-align:center}
.stat b{display:block;font-size:26px;color:var(--accent)}
.stat span{font-size:13px;color:var(--muted)}
input,select,textarea{width:100%;background:#0d1117;color:var(--text);border:1px solid var(--line);
 border-radius:10px;padding:12px;font-size:16px;font-family:inherit}
textarea{min-height:110px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:14px}
label{display:block;font-size:13px;color:var(--muted);margin:10px 0 4px}
button.action{background:var(--accent);border:none;color:#fff;padding:13px 18px;border-radius:12px;
 font-size:16px;font-weight:600;cursor:pointer;margin:6px 6px 0 0}
button.ghost{background:var(--panel2);border:1px solid var(--line);color:var(--text);padding:13px 18px;
 border-radius:12px;font-size:16px;cursor:pointer;margin:6px 6px 0 0}
button.danger{background:var(--red)}
button.small{padding:8px 12px;font-size:14px;border-radius:9px;margin:2px}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.row>*{flex:1 1 180px}
.ep{border:1px solid var(--line);background:var(--panel2);border-radius:12px;padding:12px;margin-bottom:10px}
.ep h3{margin:0 0 4px;font-size:17px}
.ep code{color:var(--purple);font-size:13px;word-break:break-all}
.ep p{margin:6px 0;color:var(--muted);font-size:14px}
pre{background:#0d1117;border:1px solid var(--line);border-radius:10px;padding:12px;overflow:auto;
 max-height:340px;font-size:13px;white-space:pre-wrap;word-break:break-word}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{border-bottom:1px solid var(--line);padding:9px 6px;text-align:left;vertical-align:top}
th{color:var(--muted);font-size:13px}
.tag{display:inline-block;background:#1f6feb33;color:#79c0ff;border-radius:20px;padding:3px 10px;
 font-size:12px;margin:2px 4px 2px 0}
.ok{color:var(--green)}.bad{color:var(--red)}.warn{color:var(--yellow)}
.hidden{display:none}
.toast{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);background:#238636;color:#fff;
 padding:14px 22px;border-radius:12px;font-weight:600;z-index:99;box-shadow:0 6px 20px rgba(0,0,0,.5)}
.chips button{background:var(--panel2);border:1px solid var(--line);color:var(--text);border-radius:20px;
 padding:8px 14px;margin:4px 4px 0 0;font-size:14px;cursor:pointer}
.chips button.active{background:var(--purple);border-color:var(--purple);color:#fff}
.src{font-size:12px;color:var(--muted)}
.help li{margin-bottom:8px}
a{color:#79c0ff}
</style>
</head>
<body>
<header>
  <h1>🛰️ OSINT &amp; Multi-Utility API Hub</h1>
  <div class="sub" id="baseUrl"></div>
  <div class="sub" id="brandBar" style="font-weight:600"></div>
</header>

<nav>
  <button class="active" data-tab="endpoints">🔌 Endpoints</button>
  <button data-tab="database">🗄️ Database</button>
  <button data-tab="keys">🔑 API Keys</button>
  <button data-tab="resellers">🤝 Resellers</button>
  <button data-tab="payments">💸 Payments</button>
  <button data-tab="backup">💾 Backup</button>
  <button data-tab="logs">📊 Logs</button>
  <button data-tab="settings">⚙️ Settings</button>
  <button data-tab="help">📖 Help</button>
</nav>

<div class="wrap">

<!-- LOGIN -->
<div class="card" id="loginCard">
  <h2>🔐 Admin Login</h2>
  <p style="color:var(--muted);margin-top:0">Database, keys, logs aur settings manage karne ke liye admin password daalo. (Default: <code>admin123</code>)</p>
  <label>Admin Password</label>
  <input id="adminPass" type="password" placeholder="admin123">
  <button class="action" onclick="doLogin()">Login</button>
  <span id="loginMsg"></span>
</div>

<!-- ENDPOINTS -->
<div id="tab-endpoints">
  <div class="stats" id="stats"></div>
  <div class="card">
    <h2>🔎 Test Any Endpoint</h2>
    <label>Search endpoint</label>
    <input id="epSearch" placeholder="ip, gst, vehicle, insta..." oninput="renderEndpoints()">
    <div class="chips" id="catChips" style="margin-top:10px"></div>
  </div>
  <div id="epList"></div>
  <div class="card hidden" id="resultCard">
    <h2>📤 Response</h2>
    <div id="resultUrl" class="src" style="word-break:break-all;margin-bottom:8px"></div>
    <div class="row">
      <button class="ghost small" onclick="copyUrl()">📋 Copy URL</button>
      <button class="ghost small" onclick="downloadResult()">⬇️ Download JSON</button>
    </div>
    <pre id="resultBox">{}</pre>
  </div>
</div>

<!-- DATABASE -->
<div id="tab-database" class="hidden">
  <div class="card">
    <h2>➕ Add Record</h2>
    <div class="row">
      <div><label>Category</label>
        <select id="recCategory">
          <option value="gst">gst</option>
          <option value="pan">pan</option>
          <option value="imei">imei (TAC only)</option>
          <option value="ip">ip</option>
          <option value="general">general</option>
        </select>
      </div>
      <div><label>Key (non-private utility identifier)</label>
        <input id="recKey" placeholder="SAFE-KEY"></div>
    </div>
    <label>Data (JSON)</label>
    <textarea id="recData">{"label":"Example safe utility record"}</textarea>
    <p style="color:var(--muted)">Leak/phone/vehicle owner records ka manual storage/import privacy ke liye disabled hai. Aadhaar ya personal data yahan upload na karein.</p>
    <label>Note (optional)</label>
    <input id="recNote" placeholder="safe source note">
    <button class="action" onclick="addRecord()">Save Record</button>
  </div>

  <div class="card">
    <h2>🔍 Search Database</h2>
    <div class="row">
      <div><label>Category</label>
        <select id="filterCategory"><option value="">All</option></select></div>
      <div><label>Keyword</label><input id="filterQ" placeholder="9199 or name"></div>
    </div>
    <button class="action" onclick="loadRecords()">Search</button>
    <button class="ghost" onclick="exportCsv()">⬇️ Export CSV</button>
    <div id="recList" style="margin-top:12px"></div>
  </div>

  <div class="card">
    <h2>📥 Bulk Import (CSV)</h2>
    <p style="color:var(--muted);margin-top:0">Format: <code>key,json_or_value,note</code> - har line ek record.</p>
    <div class="row"><div><label>Category</label>
      <select id="impCategory">
        <option value="gst">gst</option><option value="pan">pan</option>
        <option value="imei">imei (TAC only)</option><option value="ip">ip</option>
        <option value="general">general</option>
      </select></div></div>
    <textarea id="impCsv" placeholder="SAFE-KEY,{&quot;label&quot;:&quot;Example utility record&quot;},safe source"></textarea>
    <button class="action" onclick="importCsv()">Import</button>
  </div>
</div>

<!-- KEYS -->
<div id="tab-keys" class="hidden">
  <div class="card">
    <h2>💰 Sell Naya API Key (plan + expiry)</h2>
    <div class="row">
      <div><label>Customer naam</label><input id="keyName" placeholder="Rohit bhai"></div>
      <div><label>Validity</label>
        <select id="keyDays">
          <option value="7">7 din (trial)</option>
          <option value="15">15 din</option>
          <option value="30" selected>30 din (1 month - ₹100)</option>
          <option value="90">90 din (3 month)</option>
          <option value="180">180 din</option>
          <option value="365">365 din (1 saal)</option>
          <option value="0">Lifetime (kabhi expire nahi)</option>
        </select>
      </div>
    </div>
    <div class="row">
      <div><label>Plan (available APIs only; privacy-restricted routes disabled)</label>
        <select id="keyPlan" onchange="togglePlanBox()">
          <option value="*">All currently available APIs (restricted routes remain disabled)</option>
          <option value="imei,ip-v1,ifsc,pincode">Utility Starter (TAC / IP / IFSC / Pincode)</option>
          <option value="imei,ip-v1,ip-v2,ip-v3,ifsc,pincode,youtube-download,ytdl,youtube-mp3">Utility Pack — ₹100 / month</option>
          <option value="custom">Custom (sirf available endpoints)</option>
        </select>
      </div>
      <div id="customPlanBox" style="display:none">
        <label>Endpoints (comma separated; disabled paths reject honge)</label>
        <input id="keyEndpoints" placeholder="imei,ip-v1,ifsc,pincode">
      </div>
    </div>
    <div class="row">
      <div><label>Device lock</label>
        <select id="keyDeviceLock">
          <option value="1">ON - 1 device me hi chalega</option>
          <option value="0" selected>OFF - kahin bhi chalega</option>
        </select>
      </div>
      <div><label>Max devices (lock ON ho to)</label><input id="keyMaxDev" type="number" value="1"></div>
    </div>
    <div class="row">
      <div><label>Custom key (optional)</label><input id="keyCustom" placeholder="khali chhod do to auto ban jayega"></div>
      <div><label>Price note ( sirf record ke liye)</label><input id="keyPrice" placeholder="100"></div>
    </div>
    <button class="action" onclick="createKey()">Create &amp; Sell Key</button>
  </div>

  <div class="card">
    <h2>📋 Keys / Subscribers</h2>
    <p style="color:var(--muted);margin-top:0">Demo key <code>Demo</code> sabke liye hai (Settings me band kar sakte ho).
    Customer ko key + niche diya hua URL example bhej do.</p>
    <button class="ghost small" onclick="loadKeys()">Refresh</button>
    <div id="keyList" style="margin-top:10px"></div>
  </div>
</div>

<!-- LOGS -->
<div id="tab-logs" class="hidden">
  <div class="card">
    <h2>📊 Request Logs</h2>
    <button class="action" onclick="loadLogs()">Refresh</button>
    <button class="ghost" onclick="loadOverview()">Stats Refresh</button>
    <button class="danger ghost" onclick="clearLogs()">Clear Logs</button>
    <div id="logList" style="margin-top:12px;overflow-x:auto"></div>
  </div>
</div>

<!-- RESELLERS -->
<div id="tab-resellers" class="hidden">
  <div class="card">
    <h2>🤝 Naya Reseller / Dealer</h2>
    <div class="row">
      <div><label>Username</label><input id="rsUser" placeholder="dealer1"></div>
      <div><label>Password</label><input id="rsPass" placeholder="dealer123"></div>
    </div>
    <div class="row">
      <div><label>Naam</label><input id="rsName" placeholder="Rohit dealer"></div>
      <div><label>Telegram</label><input id="rsTg" placeholder="@dealer"></div>
    </div>
    <div class="row">
      <div><label>Credit (1 key = 1 credit)</label><input id="rsCredit" type="number" value="10"></div>
      <div><label>Max days (kitne din ka key bech sakta hai)</label><input id="rsMaxDays" type="number" value="30"></div>
    </div>
    <div class="row"><div><label>Endpoints (comma separated, * = sab)</label>
      <input id="rsEndpoints" value="*"></div></div>
    <button class="action" onclick="createReseller()">Create Reseller</button>
  </div>
  <div class="card">
    <h2>📋 Resellers</h2>
    <button class="ghost small" onclick="loadResellers()">Refresh</button>
    <div id="rsList" style="margin-top:10px"></div>
  </div>
  <div class="card">
    <h2>🔐 Reseller Panel (dealers ke liye)</h2>
    <p style="color:var(--muted);margin-top:0">Dealer apna username/password yahan daal kar apne keys
    khud bana sakta hai (sirf uske credit aur endpoints ke andar).</p>
    <div class="row">
      <div><label>Username</label><input id="rsLoginUser"></div>
      <div><label>Password</label><input id="rsLoginPass" type="password"></div>
    </div>
    <button class="action" onclick="resellerLogin()">Login</button>
    <button class="ghost" onclick="loadResellerKeys()">Mere Keys</button>
    <div id="rsPanel" style="margin-top:12px"></div>
  </div>
</div>

<!-- PAYMENTS -->
<div id="tab-payments" class="hidden">
  <div class="card">
    <h2>💸 UPI Orders &amp; Payments</h2>
    <p style="color:var(--muted);margin-top:0">
      Customer <b>/site</b> page se order banata hai → UPI payment → webhook se <b>key auto-activate</b>.
      Manual approval bhi yahin se kar sakte ho.
    </p>
    <button class="action" onclick="loadOrders()">Refresh</button>
    <a class="ghost" style="padding:13px 18px;border-radius:12px;text-decoration:none;display:inline-block"
       href="/site" target="_blank">🌐 Landing Page kholo</a>
    <div id="paySummary" style="margin:10px 0"></div>
    <div id="orderList" style="margin-top:10px"></div>
  </div>
  <div class="card">
    <h2>📥 Payments log (webhook)</h2>
    <div id="paymentList"></div>
  </div>
  <div class="card">
    <h2>🔗 Webhook URL (auto activation ke liye)</h2>
    <pre id="webhookInfo"></pre>
  </div>
</div>

<!-- BACKUP -->
<div id="tab-backup" class="hidden">
  <div class="card">
    <h2>💾 Backup &amp; Restore</h2>
    <p style="color:var(--muted);margin-top:0">
      ⚠️ <b>Render free plan</b> ka disk temporary hota hai — server restart/sleep hone par
      <b>keys, resellers, orders</b> sab reset ho sakte hain. Isliye roz ek backup file download
      kar lein, aur zaroorat padne par yahin se restore kar dein.
    </p>
    <button class="action" onclick="doBackup()">⬇️ Backup Download (JSON)</button>
    <button class="ghost" onclick="copyBackup()">📋 Clipboard me copy</button>
    <div class="row" style="margin-top:14px">
      <div><label>Restore — backup JSON yahan paste karein</label>
        <textarea id="restoreBox" placeholder='{"tables":{"settings":[...],"api_keys":[...]}}'></textarea></div>
    </div>
    <input type="file" id="restoreFile" accept=".json" onchange="pickRestore(this)">
    <button class="action" onclick="doRestore()">♻️ Restore</button>
    <div id="backupInfo" style="margin-top:10px"></div>
  </div>
  <div class="card">
    <h2>☁️ Permanent data (optional, ₹0 extra nahi to)</h2>
    <p style="color:var(--muted);margin-top:0">
      Free plan me data safe rakhne ka sabse aasan tarika: <b>roz backup download</b>.
      Agar aap Render ka <b>Starter ($7/month)</b> lete hain to wahan <b>1GB persistent disk</b>
      milta hai — <code>/var/data</code> mount kar ke <code>DB_PATH=/var/data/osint.db</code>
      set kar dein, phir data kabhi reset nahi hoga.
    </p>
  </div>
</div>

<!-- SETTINGS -->
<div id="tab-settings" class="hidden">
  <div class="card">
    <h2>⚙️ Settings</h2>
    <div class="row">
      <div><label>Upstream base URL</label><input id="stUpstream"></div>
      <div><label>Upstream key</label><input id="stUpstreamKey"></div>
    </div>
    <div class="row">
      <div><label>Upstream enabled</label>
        <select id="stUpEnabled"><option value="1">Yes</option><option value="0">No</option></select></div>
      <div><label>Demo key enabled</label>
        <select id="stDemo"><option value="1">Yes</option><option value="0">No</option></select></div>
    </div>
    <div class="row">
      <div><label>Cache TTL (seconds)</label><input id="stTtl" type="number"></div>
      <div><label>Cache enabled</label>
        <select id="stCache"><option value="1">Yes</option><option value="0">No</option></select></div>
    </div>
    <div class="row">
      <div><label>Rate limit (requests / minute / key)</label><input id="stRate" type="number"></div>
      <div><label>GitHub token (optional)</label><input id="stGithub"></div>
    </div>
    <div class="row">
      <div><label>Max seconds per request (slow upstream ke liye)</label><input id="stMaxSec" type="number"></div>
      <div><label>Brand tag (har response me "Powered by ...")</label><input id="stBrand" placeholder="@Supermannn_x"></div>
    </div>
    <div class="row">
      <div><label>HaveIBeenPwned API key (optional)</label><input id="stHibp" placeholder=""></div>
      <div><label>New admin password</label><input id="stAdmin" placeholder="change karne ke liye likho"></div>
    </div>
    <h2 style="margin-top:18px">💸 Store / Payment settings</h2>
    <div class="row">
      <div><label>UPI ID (payment yahan aayega)</label><input id="stUpi" placeholder="aapka@okhdfcbank"></div>
      <div><label>UPI display name</label><input id="stUpiName" placeholder="OSINT API Hub"></div>
    </div>
    <div class="row">
      <div><label>Telegram support (@username)</label><input id="stSupport" placeholder="@Supermannn_x"></div>
      <div><label>Webhook secret (optional, security)</label><input id="stWsec" placeholder=""></div>
    </div>
    <div class="row">
      <div><label>Website title</label><input id="stTitle" placeholder="OSINT API Hub"></div>
      <div><label>Website tagline</label><input id="stTagline" placeholder="Utility APIs — restricted lookups disabled"></div>
    </div>
    <div class="row"><div><label>Plans JSON (advanced - khali chhod do to default plans)</label>
      <textarea id="stPlans" placeholder='[{"id":"utility","name":"Utility Pack","days":30,"endpoints":"imei,ip-v1,ifsc,pincode","price":100,"description":"30 din — privacy-restricted endpoints disabled"}]'></textarea></div></div>
    <button class="action" onclick="saveSettings()">Save Settings</button>
    <button class="ghost" onclick="clearCache()">🧹 Clear Cache</button>
  </div>
</div>

<!-- HELP -->
<div id="tab-help" class="hidden">
  <div class="card help">
    <h2>📖 Kaise use karein (Hinglish)</h2>
    <ol>
      <li><b>Endpoint test karna:</b> "Endpoints" tab me jao, koi bhi API select karo, value bharo aur <b>Run</b> dabao. Response niche dikhega.</li>
      <li><b>Apne app me use karna:</b> Run karne ke baad <b>Copy URL</b> daba lo - wo URL kahin bhi chalega (browser, Postman, Telegram bot, website).</li>
      <li><b>Apna data add karna:</b> "Database" tab me category chuno (jaise leak / phone), key aur JSON data bharo, Save karo. Ye data <code>/api/leak-v1</code>, <code>/api/num-info</code> waghera me <code>custom_database_records</code> ke roop me aayega.</li>
      <li><b>API keys:</b> "API Keys" tab me naya key banao aur apne doston ko do. <code>?key=Demo</code> sabke liye hai.</li>
      <li><b>Logs:</b> kaunsi API kitni baar chali, stats me dekho.</li>
      <li><b>Settings:</b> Upstream URL (default osint-apis-hub.onrender.com), cache, rate limit sab yahan se control hota hai.</li>
    </ol>
    <h2>☁️ Free hosting par kaise chalayein</h2>
    <ol>
      <li>GitHub pe naya repo banao aur is project ke files upload karo (<code>main.py</code>, <code>requirements.txt</code>, <code>Procfile</code>, <code>Dockerfile</code>, <code>render.yaml</code>, <code>README.md</code>).</li>
      <li><a href="https://render.com" target="_blank">render.com</a> par free account banao → <b>New +</b> → <b>Blueprint</b> → apna repo select karo → Apply. Render khud <code>render.yaml</code> padh lega.</li>
      <li>Deploy hone ke baad URL: <code>https://aapka-app.onrender.com</code>. Dashboard khulne par upar likha URL hi aapka base URL hai.</li>
      <li>Environment me <code>ADMIN_PASSWORD</code> set kar dena (Render → Environment) taaki koi aur dashboard na khol sake.</li>
      <li>Render free tier 15 minute bina traffic ke baad sleep karta hai - pehli request thodi slow hogi (30-60 sec).</li>
    </ol>
    <p class="src">Note: SQLite database Render free tier par redeploy ke baad reset ho sakti hai (ephemeral disk). Permanent data ke liye Render paid disk ya external DB use karein, ya apna data Database tab me CSV export kar ke save rakhein.</p>
  </div>
</div>

</div>

<script>
const EP = __ENDPOINTS_JSON__;
let TOKEN = localStorage.getItem('osint_admin') || '';
let LAST_URL = '';
let LAST_JSON = null;

document.getElementById('baseUrl').textContent = 'Base URL: ' + location.origin + '  |  Demo key: Demo  |  Endpoints: ' + EP.length;
document.querySelectorAll('nav button').forEach(b=>{
  b.onclick = ()=>{
    document.querySelectorAll('nav button').forEach(x=>x.classList.remove('active'));
    b.classList.add('active');
    ['endpoints','database','keys','resellers','payments','backup','logs','settings','help'].forEach(t=>{
      document.getElementById('tab-'+t).classList.add('hidden');
    });
    document.getElementById('tab-'+b.dataset.tab).classList.remove('hidden');
    if(b.dataset.tab==='database') loadRecords();
    if(b.dataset.tab==='keys') loadKeys();
    if(b.dataset.tab==='logs'){ loadLogs(); loadOverview(); }
    if(b.dataset.tab==='settings') loadSettings();
    if(b.dataset.tab==='resellers') loadResellers();
    if(b.dataset.tab==='payments') loadOrders();
  };
});

function toast(msg, bad){
  const d=document.createElement('div'); d.className='toast'; d.textContent=msg;
  if(bad) d.style.background='#b62324';
  document.body.appendChild(d); setTimeout(()=>d.remove(),2600);
}
function adminHeaders(extra){
  const h = {'Content-Type':'application/json'};
  if(TOKEN) h['X-Admin-Token']=TOKEN;
  return Object.assign(h, extra||{});
}
async function adminFetch(url, opts){
  const res = await fetch(url, Object.assign({headers: adminHeaders()}, opts||{}));
  if(res.status===401){ document.getElementById('loginCard').classList.remove('hidden');
    toast('Pehle admin login karo', true); throw new Error('unauthorized'); }
  return res.json();
}

async function doLogin(){
  const pass = document.getElementById('adminPass').value;
  const r = await fetch('/admin/login',{method:'POST',headers:{'Content-Type':'application/json'},
    body: JSON.stringify({password: pass})});
  const j = await r.json();
  if(j.success){ TOKEN = pass; localStorage.setItem('osint_admin', pass);
    document.getElementById('loginCard').classList.add('hidden');
    document.getElementById('loginMsg').innerHTML='<span class="ok"> ✅ Login success</span>';
    loadOverview(); toast('Welcome admin!');
  } else {
    document.getElementById('loginMsg').innerHTML='<span class="bad"> ❌ Wrong password</span>';
  }
}
if(TOKEN){ document.getElementById('loginCard').classList.add('hidden'); }

/* ---------- stats ---------- */
async function loadOverview(){
  try{
    const j = await adminFetch('/admin/overview');
    if(!j.success) return;
    document.getElementById('stats').innerHTML = [
      ['Endpoints', j.endpoints], ['Requests today', j.requests_today],
      ['Total requests', j.total_requests], ['DB records', j.records],
      ['API keys', j.api_keys], ['Cache', j.cache_entries], ['Errors', j.errors]
    ].map(x=>'<div class="stat"><b>'+x[1]+'</b><span>'+x[0]+'</span></div>').join('');
  }catch(e){}
}

/* ---------- endpoints ---------- */
let activeCat = '';
function renderEndpoints(){
  const cats = [...new Set(EP.map(e=>e.category))];
  document.getElementById('catChips').innerHTML = cats.map(c=>
    '<button class="'+(activeCat===c?'active':'')+'" onclick="setCat(\''+c+'\')">'+c+'</button>').join('');
  const q = document.getElementById('epSearch').value.toLowerCase();
  const list = EP.filter(e=> (!activeCat || e.category===activeCat) &&
    (!q || (e.name+' '+e.path+' '+e.desc).toLowerCase().includes(q)));
  document.getElementById('epList').innerHTML = list.map(e=>{
    const inputs = e.params.map(p=>
      '<div style="flex:1 1 220px"><label>'+p.name+'</label><input id="p_'+e.path.replace(/-/g,'_')+'_'+p.name+'" value="'+p.sample.replace(/"/g,'&quot;')+'"></div>').join('');
    return '<div class="card ep"><h3>'+e.icon+' '+e.name+'</h3>'+
      '<code>/api/'+e.path+'?key=Demo&'+e.params.map(p=>p.name+'=...').join('&')+'</code>'+
      '<p>'+e.desc+'</p><div class="row">'+inputs+'</div>'+
      '<button class="action small" onclick="runEp(\''+e.path+'\')">▶ Run</button>'+
      '<button class="ghost small" onclick="openEp(\''+e.path+'\')">🔗 Open in new tab</button></div>';
  }).join('') || '<div class="card">Koi endpoint nahi mila.</div>';
}
function setCat(c){ activeCat = (activeCat===c? '' : c); renderEndpoints(); }
function epParams(path){
  const e = EP.find(x=>x.path===path); const out={};
  e.params.forEach(p=>{
    const el = document.getElementById('p_'+path.replace(/-/g,'_')+'_'+p.name);
    out[p.name] = el ? el.value : p.sample;
  });
  return out;
}
function buildUrl(path){
  const params = epParams(path);
  const qs = Object.keys(params).filter(k=>params[k]!=='').map(k=>k+'='+encodeURIComponent(params[k])).join('&');
  return '/api/'+path+'?key=Demo'+(qs?'&'+qs:'');
}
async function runEp(path){
  const url = buildUrl(path);
  LAST_URL = location.origin+url;
  document.getElementById('resultCard').classList.remove('hidden');
  document.getElementById('resultUrl').textContent = '⏳ '+LAST_URL;
  document.getElementById('resultBox').textContent = 'Loading...';
  const t0 = Date.now();
  try{
    const res = await fetch(url);
    const txt = await res.text();
    let json = txt; try{ json = JSON.parse(txt); }catch(e){}
    LAST_JSON = json;
    const src = res.headers.get('X-Source') || '-';
    document.getElementById('resultUrl').innerHTML = '<b>'+res.status+'</b> · source: <b>'+src+
      '</b> · '+((Date.now()-t0)/1000).toFixed(2)+'s<br>'+LAST_URL;
    document.getElementById('resultBox').textContent =
      typeof json==='string' ? json : JSON.stringify(json,null,2).slice(0,40000);
    document.getElementById('resultCard').scrollIntoView({behavior:'smooth'});
  }catch(err){
    document.getElementById('resultBox').textContent = 'Error: '+err;
  }
}
function openEp(path){ window.open(buildUrl(path), '_blank'); }
function copyUrl(){ navigator.clipboard.writeText(LAST_URL); toast('URL copied!'); }
function downloadResult(){
  const blob = new Blob([JSON.stringify(LAST_JSON,null,2)],{type:'application/json'});
  const a=document.createElement('a'); a.href=URL.createObjectURL(blob);
  a.download='response.json'; a.click();
}

/* ---------- database ---------- */
async function loadRecords(){
  try{
    const cat = document.getElementById('filterCategory').value;
    const q = document.getElementById('filterQ').value;
    const j = await adminFetch('/admin/records?category='+encodeURIComponent(cat)+'&q='+encodeURIComponent(q)+'&limit=200');
    if(!j.success) return;
    document.getElementById('recList').innerHTML = j.records.length ?
      '<table><tr><th>ID</th><th>Category</th><th>Key</th><th>Data</th><th>Note</th><th></th></tr>' +
      j.records.map(r=>'<tr><td>'+r.id+'</td><td><span class="tag">'+r.category+'</span></td>'+
      '<td><b>'+r.key_value+'</b></td><td><pre style="max-height:120px">'+escapeHtml(r.data)+'</pre></td>'+
      '<td>'+(r.note||'')+'</td><td><button class="danger small" onclick="delRecord('+r.id+')">🗑</button></td></tr>').join('')
      + '</table>' : '<p style="color:var(--muted)">Koi record nahi mila.</p>';
  }catch(e){}
}
function escapeHtml(s){ return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }
async function addRecord(){
  const payload = {category: document.getElementById('recCategory').value,
    key_value: document.getElementById('recKey').value,
    data: document.getElementById('recData').value,
    note: document.getElementById('recNote').value};
  const r = await fetch('/admin/records',{method:'POST',headers:adminHeaders(),body:JSON.stringify(payload)});
  const j = await r.json();
  if(j.success){ toast('Record saved ✅'); document.getElementById('recKey').value=''; loadRecords(); }
  else toast(j.error||'Error', true);
}
async function delRecord(id){
  if(!confirm('Delete record #'+id+'?')) return;
  await fetch('/admin/records/'+id,{method:'DELETE',headers:adminHeaders()});
  toast('Deleted'); loadRecords();
}
async function importCsv(){
  const payload = {category: document.getElementById('impCategory').value,
    csv: document.getElementById('impCsv').value};
  const r = await fetch('/admin/records/import',{method:'POST',headers:adminHeaders(),body:JSON.stringify(payload)});
  const j = await r.json();
  toast(j.imported+' records imported ✅'); loadRecords();
}
function exportCsv(){ window.open('/admin/records/export?token='+encodeURIComponent(TOKEN),'_blank'); }

/* ---------- keys / subscriptions ---------- */
function togglePlanBox(){
  const v = document.getElementById('keyPlan').value;
  document.getElementById('customPlanBox').style.display = (v==='custom') ? '' : 'none';
}
function planValue(){
  const v = document.getElementById('keyPlan').value;
  if(v==='custom'){ return document.getElementById('keyEndpoints').value || '*'; }
  return v;
}
async function loadKeys(){
  try{
    const j = await adminFetch('/admin/keys');
    if(!j.success) return;
    const short = k => k.length>16 ? k.slice(0,10)+'...'+k.slice(-4) : k;
    document.getElementById('keyList').innerHTML = j.keys.length ?
      '<table><tr><th>Key</th><th>Customer</th><th>Plan</th><th>Expiry</th><th>Devices</th><th>Use</th><th>Actions</th></tr>'+
      j.keys.map(k=>{
        const dl = k.days_left;
        const expTxt = k.expires_at ? (k.expires_at.slice(0,16) + ' (' + (dl===null?'?':dl) + 'd left)') : 'Lifetime';
        const expColor = k.is_expired ? 'bad' : (dl!==null && dl<=3 ? 'warn' : 'ok');
        const plan = (k.allowed_endpoints==='*'||!k.allowed_endpoints) ? 'ALL' :
                      k.allowed_endpoints.split(',').length + ' APIs: '+k.allowed_endpoints;
        return '<tr><td><code>'+short(k.api_key)+'</code> '+
          '<button class="ghost small" onclick=\'copyText("'+k.api_key+'")\'>📋</button></td>'+
          '<td>'+(k.customer||k.name||'')+'</td><td class="src">'+plan+'</td>'+
          '<td class="'+expColor+'">'+expTxt+'</td>'+
          '<td>'+(k.device_lock? ('🔒 '+k.bound_device_count+'/'+k.max_devices) : '🔓 off')+'</td>'+
          '<td>'+k.requests+'</td>'+
          '<td><button class="ghost small" onclick="extendKey('+k.id+',30)">+30d</button>'+
          '<button class="ghost small" onclick="extendKey('+k.id+',7)">+7d</button>'+
          '<button class="ghost small" onclick="extendKey('+k.id+',365)">+1y</button>'+
          '<button class="ghost small" onclick="toggleDeviceLock('+k.id+','+k.device_lock+')">'+(k.device_lock?'🔓 Unlock':'🔒 Lock')+'</button>'+
          '<button class="ghost small" onclick="unbindKey('+k.id+')">♻️ Reset device</button>'+
          '<button class="ghost small" onclick="showCustomerMsg('+k.id+')">📤 Bhejo</button>'+
          '<button class="ghost small" onclick="toggleKey('+k.id+')">'+(k.is_active?'Disable':'Enable')+'</button>'+
          '<button class="danger small" onclick="delKey('+k.id+')">🗑</button></td></tr>';
      }).join('')+'</table>' : '<p style="color:var(--muted)">Abhi koi key nahi bani.</p>';
  }catch(e){}
}
function copyText(t){ navigator.clipboard.writeText(t); toast('Copied!'); }
async function createKey(){
  const payload = {name: document.getElementById('keyName').value,
    days: parseInt(document.getElementById('keyDays').value||'30'),
    allowed_endpoints: planValue(),
    device_lock: parseInt(document.getElementById('keyDeviceLock').value||'0'),
    max_devices: parseInt(document.getElementById('keyMaxDev').value||'1'),
    custom_key: document.getElementById('keyCustom').value,
    customer: document.getElementById('keyName').value,
    price: document.getElementById('keyPrice').value};
  const r = await fetch('/admin/keys',{method:'POST',headers:adminHeaders(),body:JSON.stringify(payload)});
  const j = await r.json();
  if(j.success){
    toast('Key ban gayi ✅');
    document.getElementById('keyName').value=''; document.getElementById('keyCustom').value='';
    loadKeys();
    alert('NAYA API KEY (customer ko bhej do)\n\n'+j.api_key+'\n\nValidity: '+(j.expires_at||'Lifetime')+'\nPlan: '+j.allowed_endpoints);
  } else toast(j.error||'Error', true);
}
async function extendKey(id, days){
  const r = await fetch('/admin/keys/'+id+'/extend',{method:'POST',headers:adminHeaders(),
    body:JSON.stringify({days:days})});
  const j = await r.json();
  toast('Renew ho gaya ✅ naya expiry: '+(j.expires_at||'')); loadKeys();
}
async function toggleDeviceLock(id, current){
  const r = await fetch('/admin/keys/'+id+'/plan',{method:'POST',headers:adminHeaders(),
    body:JSON.stringify({device_lock: current?0:1})});
  toast(current?'Device lock OFF ✅':'Device lock ON ✅'); loadKeys();
}
async function unbindKey(id){
  await fetch('/admin/keys/'+id+'/unbind',{method:'POST',headers:adminHeaders()});
  toast('Device reset ✅ naya device bind hoga'); loadKeys();
}
async function showCustomerMsg(id){
  try{
    const j = await adminFetch('/admin/keys');
    const k = (j.keys||[]).find(x=>x.id===id);
    if(!k) return;
    const base = location.origin;
    const plan = (k.allowed_endpoints==='*'||!k.allowed_endpoints)?'Available endpoints (privacy-restricted routes disabled)':k.allowed_endpoints;
    const msg = '✅ Aapka OSINT API key ready hai\n\n'+
      '🔑 Key: '+k.api_key+'\n'+
      '📦 Plan: '+plan+'\n'+
      '⏳ Valid till: '+(k.expires_at||'Lifetime')+'\n\n'+
      '📌 Examples:\n'+
      base+'/api/imei?key='+k.api_key+'&imei=35301011\n'+
      base+'/api/ifsc?key='+k.api_key+'&ifsc=SBIN0000001\n'+
      base+'/api/pincode?key='+k.api_key+'&pincode=110001\n\n'+
      '🔒 Personal-record/Aadhaar/email/vehicle-owner routes privacy ke liye disabled hain.\n\n'+
      '🔎 Apni key check karo: '+base+'/api/key-info?key='+k.api_key;
    prompt('Ye message copy kar ke customer ko bhej do:', msg);
  }catch(e){}
}
async function toggleKey(id){ await fetch('/admin/keys/'+id+'/toggle',{method:'POST',headers:adminHeaders()}); loadKeys(); }
async function delKey(id){ if(!confirm('Delete key?')) return;
  await fetch('/admin/keys/'+id,{method:'DELETE',headers:adminHeaders()}); loadKeys(); }

/* ---------- resellers ---------- */
let RTOKEN = localStorage.getItem('osint_reseller') || '';
async function rsFetch(url, opts){
  const h = {'Content-Type':'application/json'};
  if(RTOKEN) h['X-Reseller-Token']=RTOKEN;
  const r = await fetch(url, Object.assign({headers:h}, opts||{}));
  if(r.status===401){ toast('Reseller login karo', true); throw new Error('unauthorized'); }
  return r.json();
}
async function loadResellers(){
  try{
    const j = await adminFetch('/admin/resellers');
    if(!j.success) return;
    document.getElementById('rsList').innerHTML = j.resellers.length ?
      '<table><tr><th>ID</th><th>Username</th><th>Naam</th><th>Credit</th><th>Keys</th><th>Max days</th><th>Endpoints</th><th>Status</th><th>Actions</th></tr>'+
      j.resellers.map(r=>'<tr><td>'+r.id+'</td><td><b>'+r.username+'</b></td><td>'+(r.name||'')+'</td>'+
      '<td class="'+(r.credit>0?'ok':'bad')+'">'+r.credit+'</td><td>'+r.keys_created+'</td>'+
      '<td>'+r.max_days+'</td><td class="src">'+r.allowed_endpoints+'</td>'+
      '<td>'+(r.is_active?'<span class="ok">Active</span>':'<span class="bad">Disabled</span>')+'</td>'+
      '<td><button class="ghost small" onclick="addCredit('+r.id+',10)">+10</button>'+
      '<button class="ghost small" onclick="addCredit('+r.id+',50)">+50</button>'+
      '<button class="ghost small" onclick="toggleReseller('+r.id+')">'+(r.is_active?'Disable':'Enable')+'</button>'+
      '<button class="danger small" onclick="delReseller('+r.id+')">🗑</button></td></tr>').join('')+'</table>'
      : '<p style="color:var(--muted)">Koi reseller nahi.</p>';
  }catch(e){}
}
async function createReseller(){
  const payload = {username: document.getElementById('rsUser').value,
    password: document.getElementById('rsPass').value,
    name: document.getElementById('rsName').value,
    telegram: document.getElementById('rsTg').value,
    credit: parseInt(document.getElementById('rsCredit').value||'10'),
    max_days: parseInt(document.getElementById('rsMaxDays').value||'30'),
    allowed_endpoints: document.getElementById('rsEndpoints').value||'*'};
  const r = await fetch('/admin/resellers',{method:'POST',headers:adminHeaders(),body:JSON.stringify(payload)});
  const j = await r.json();
  if(j.success){ toast('Reseller ban gaya ✅'); loadResellers();
    alert('RESELLER LOGIN\nUsername: '+j.username+'\nPassword: '+j.password+'\nCredit: '+j.credit); }
  else toast(j.error||'Error', true);
}
async function addCredit(id, amount){
  const r = await fetch('/admin/resellers/'+id+'/credit',{method:'POST',headers:adminHeaders(),
    body:JSON.stringify({amount:amount})});
  const j = await r.json(); toast('Credit: '+j.credit+' ✅'); loadResellers();
}
async function toggleReseller(id){
  await fetch('/admin/resellers/'+id+'/toggle',{method:'POST',headers:adminHeaders()}); loadResellers();
}
async function delReseller(id){
  if(!confirm('Delete reseller?')) return;
  await fetch('/admin/resellers/'+id,{method:'DELETE',headers:adminHeaders()}); loadResellers();
}
async function resellerLogin(){
  const r = await fetch('/reseller/login',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({username:document.getElementById('rsLoginUser').value,
                         password:document.getElementById('rsLoginPass').value})});
  const j = await r.json();
  if(j.success){ RTOKEN = j.token; localStorage.setItem('osint_reseller', j.token);
    toast('Reseller login ✅'); loadResellerKeys(); }
  else toast(j.error||'Login failed', true);
}
async function loadResellerKeys(){
  try{
    const j = await rsFetch('/reseller/me');
    if(!j.success) return;
    document.getElementById('rsPanel').innerHTML =
      '<div class="stat"><b>'+j.reseller.credit+'</b><span>Credit left</span></div>'+
      '<pre>'+JSON.stringify(j.reseller,null,1)+'</pre>'+
      '<h3>Mere keys ('+j.keys.length+')</h3>'+
      '<table><tr><th>Key</th><th>Customer</th><th>Plan</th><th>Expiry</th><th>Use</th></tr>'+
      j.keys.map(k=>'<tr><td><code>'+k.api_key.slice(0,14)+'...</code></td><td>'+(k.customer||'')+'</td>'+
        '<td class="src">'+k.allowed_endpoints+'</td><td>'+(k.expires_at||'Lifetime')+'</td>'+
        '<td>'+k.requests+'</td></tr>').join('')+'</table>';
  }catch(e){}
}


/* ---------- backup / restore ---------- */
let BACKUP_JSON = '';
async function doBackup(){
  const j = await adminFetch('/admin/backup?download=0');
  BACKUP_JSON = JSON.stringify(j, null, 1);
  document.getElementById('backupInfo').innerHTML =
    '<p class="ok">Backup ready ✅ ('+BACKUP_JSON.length+' chars)</p>'+
    '<pre>'+BACKUP_JSON.slice(0,1500)+'...</pre>';
  const blob = new Blob([BACKUP_JSON], {type:'application/json'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'osint-hub-backup-'+new Date().toISOString().slice(0,10)+'.json';
  document.body.appendChild(a); a.click(); document.body.removeChild(a);
}
async function copyBackup(){
  if(!BACKUP_JSON){ await doBackup(); }
  try{ await navigator.clipboard.writeText(BACKUP_JSON); toast('Copy ho gaya ✅'); }
  catch(e){ toast('Copy fail, download use karein', true); }
}
function pickRestore(inp){
  const f = inp.files[0]; if(!f) return;
  const fr = new FileReader();
  fr.onload = e => document.getElementById('restoreBox').value = e.target.result;
  fr.readAsText(f);
}
async function doRestore(){
  const txt = document.getElementById('restoreBox').value.trim();
  if(!txt) return toast('Pehle backup JSON paste karein', true);
  let obj; try{ obj = JSON.parse(txt); }catch(e){ return toast('JSON galat hai', true); }
  if(!confirm('Purana data replace ho jayega. Restore karein?')) return;
  const r = await fetch('/admin/restore',{method:'POST',headers:adminHeaders(),body:JSON.stringify(obj)});
  const j = await r.json();
  if(j.success){ toast('Restore ho gaya ✅');
    document.getElementById('backupInfo').innerHTML = '<p class="ok">Restored: '+JSON.stringify(j.restored)+'</p>';
    setTimeout(()=>location.reload(), 1200);
  } else toast(j.error||'Error', true);
}

/* ---------- payments / orders ---------- */
async function loadOrders(){
  try{
    const j = await adminFetch('/admin/orders');
    if(!j.success) return;
    const paid = j.orders.filter(o=>o.status==='paid');
    const pend = j.orders.filter(o=>o.status==='pending');
    const total = paid.reduce((a,o)=>a+(o.amount||0),0);
    document.getElementById('paySummary').innerHTML =
      '<div class="stats"><div class="stat"><b>'+paid.length+'</b><span>Paid orders</span></div>'+
      '<div class="stat"><b>₹'+total+'</b><span>Revenue</span></div>'+
      '<div class="stat"><b>'+pend.length+'</b><span>Pending</span></div>'+
      '<div class="stat"><b>'+j.payments.length+'</b><span>Webhook hits</span></div></div>';
    document.getElementById('orderList').innerHTML = j.orders.length ?
      '<table><tr><th>Code</th><th>Customer</th><th>Plan</th><th>Amount</th><th>Status</th><th>Time</th><th>Action</th></tr>'+
      j.orders.map(o=>'<tr><td><span class="code">'+o.order_code+'</span></td><td>'+(o.customer_name||'')+
      '<br><span class="src">'+(o.phone||'')+'</span></td><td>'+o.plan_name+' ('+o.days+'d)</td>'+
      '<td>₹'+o.amount+'</td><td class="'+(o.status==='paid'?'ok':'warn')+'">'+o.status+'</td>'+
      '<td class="src">'+(o.created_at||'')+'</td>'+
      '<td>'+(o.status==='paid'?'✅':'<button class="ghost small" onclick="markPaid('+o.id+')">Mark Paid</button>')+
      '</td></tr>').join('')+'</table>' : '<p style="color:var(--muted)">Koi order nahi.</p>';
    document.getElementById('paymentList').innerHTML = j.payments.length ?
      '<table><tr><th>ID</th><th>Amount</th><th>UTR</th><th>Payer</th><th>Match</th><th>Time</th></tr>'+
      j.payments.map(p=>'<tr><td>'+p.id+'</td><td>₹'+p.amount+'</td><td>'+(p.utr||'')+'</td>'+
      '<td>'+(p.payer||'')+'</td><td class="'+(p.status==='matched'?'ok':'bad')+'">'+p.status+'</td>'+
      '<td class="src">'+(p.received_at||'')+'</td></tr>').join('')+'</table>' :
      '<p style="color:var(--muted)">Abhi koi webhook payment nahi aaya.</p>';
    document.getElementById('webhookInfo').textContent =
      'POST '+location.origin+'/webhook/payment\n'+
      'Headers: Content-Type: application/json, X-Webhook-Secret: <settings wala secret>\n'+
      'Body: {"utr":"123456789012","amount":100,"remark":"OSXXXXXXXX","payer":"NAME"}\n\n'+
      'Ya SMS forwarder ke liye:\n'+
      'POST '+location.origin+'/webhook/upi-sms\n'+
      'Body: {"sms":"Rs.100 credited to your account ... UPI Ref No 123456789012"}';
  }catch(e){}
}
async function markPaid(id){
  const r = await fetch('/admin/orders/'+id+'/mark-paid',{method:'POST',headers:adminHeaders(),
    body:JSON.stringify({utr:'MANUAL'})});
  const j = await r.json();
  if(j.success){ toast('Key activate ho gayi ✅'); alert('API KEY: '+j.api_key); loadOrders(); }
  else toast(j.error||'Error', true);
}

/* ---------- logs ---------- */
async function loadLogs(){
  try{
    const j = await adminFetch('/admin/logs?limit=150');
    if(!j.success) return;
    document.getElementById('logList').innerHTML =
      '<table><tr><th>Time (IST)</th><th>Endpoint</th><th>Key</th><th>Source</th><th>Status</th><th>ms</th><th>Params</th></tr>'+
      j.logs.map(l=>'<tr><td>'+l.ts+'</td><td><b>'+l.endpoint+'</b></td><td>'+l.api_key+'</td>'+
      '<td>'+l.source+'</td><td class="'+(l.status>=400?'bad':'ok')+'">'+l.status+'</td><td>'+l.ms+'</td>'+
      '<td class="src">'+escapeHtml((l.params||'').slice(0,80))+'</td></tr>').join('')+'</table>';
  }catch(e){}
}
async function clearLogs(){
  if(!confirm('Saare logs delete kar dein?')) return;
  await fetch('/admin/logs/clear',{method:'POST',headers:adminHeaders()}); loadLogs(); toast('Logs cleared');
}

/* ---------- settings ---------- */
async function loadSettings(){
  try{
    const j = await adminFetch('/admin/settings');
    if(!j.success) return;
    const s=j.settings;
    document.getElementById('stUpstream').value=s.upstream_base||'';
    document.getElementById('stUpstreamKey').value=s.upstream_key||'';
    document.getElementById('stUpEnabled').value=s.upstream_enabled||'1';
    document.getElementById('stDemo').value=s.demo_key_enabled||'1';
    document.getElementById('stTtl').value=s.cache_ttl||'3600';
    document.getElementById('stCache').value=s.cache_enabled||'1';
    document.getElementById('stRate').value=s.rate_limit_per_min||'120';
    document.getElementById('stGithub').value=s.github_token||'';
    document.getElementById('stMaxSec').value=s.max_request_seconds||'50';
    document.getElementById('stBrand').value=s.brand_tag||'@Supermannn_x';
    document.getElementById('stHibp').value=s.hibp_api_key||'';
    document.getElementById('stUpi').value=s.upi_id||'';
    document.getElementById('stUpiName').value=s.upi_name||'';
    document.getElementById('stSupport').value=s.telegram_support||'';
    document.getElementById('stWsec').value=s.webhook_secret||'';
    document.getElementById('stTitle').value=s.store_title||'';
    document.getElementById('stTagline').value=s.store_tagline||'';
    document.getElementById('stPlans').value=s.store_plans||'';
    document.getElementById('brandBar').textContent='API Developer: '+(s.brand_tag||'@Supermannn_x')+' (Telegram)';
    document.getElementById('brandFoot').textContent=(s.brand_tag||'@Supermannn_x');
    document.getElementById('brandFoot2').textContent=(s.brand_tag||'@Supermannn_x');
  }catch(e){}
}
async function saveSettings(){
  const settings = {
    upstream_base: document.getElementById('stUpstream').value,
    upstream_key: document.getElementById('stUpstreamKey').value,
    upstream_enabled: document.getElementById('stUpEnabled').value,
    demo_key_enabled: document.getElementById('stDemo').value,
    cache_ttl: document.getElementById('stTtl').value,
    cache_enabled: document.getElementById('stCache').value,
    rate_limit_per_min: document.getElementById('stRate').value,
    github_token: document.getElementById('stGithub').value,
    max_request_seconds: document.getElementById('stMaxSec').value,
    brand_tag: document.getElementById('stBrand').value,
    hibp_api_key: document.getElementById('stHibp').value,
    upi_id: document.getElementById('stUpi').value,
    upi_name: document.getElementById('stUpiName').value,
    telegram_support: document.getElementById('stSupport').value,
    webhook_secret: document.getElementById('stWsec').value,
    store_title: document.getElementById('stTitle').value,
    store_tagline: document.getElementById('stTagline').value,
    store_plans: document.getElementById('stPlans').value
  };
  const admin = document.getElementById('stAdmin').value;
  if(admin){ settings.admin_password = admin; TOKEN = admin; localStorage.setItem('osint_admin', admin);
    document.getElementById('stAdmin').value=''; }
  await fetch('/admin/settings',{method:'POST',headers:adminHeaders(),body:JSON.stringify({settings})});
  toast('Settings saved ✅');
}
async function clearCache(){
  await fetch('/admin/cache/clear',{method:'POST',headers:adminHeaders()}); toast('Cache cleared 🧹');
}

/* init */
(function(){
  const opts = ['leak','phone','gst','pan','vehicle','imei','ip','general'];
  document.getElementById('filterCategory').innerHTML = '<option value="">All</option>'+
    opts.map(o=>'<option value="'+o+'">'+o+'</option>').join('');
  renderEndpoints(); loadOverview();
})();
</script>
</body>
</html>
"""


def render_dashboard(request: Request) -> HTMLResponse:
    catalog = [{
        "path": e["path"], "name": e["name"], "icon": e["icon"], "category": e["category"],
        "desc": e["desc"],
        "params": [{"name": p["name"], "sample": p["sample"]} for p in e["params"]],
    } for e in ENDPOINTS]
    html = DASHBOARD_HTML.replace("__ENDPOINTS_JSON__", json.dumps(catalog, ensure_ascii=False))
    return HTMLResponse(html)


@app.get("/dashboard")
async def dashboard(request: Request):
    return render_dashboard(request)


@app.get("/")
async def root(request: Request):
    accept = request.headers.get("accept", "")
    if "text/html" in accept and "application/json" not in accept:
        return render_dashboard(request)
    base = str(request.base_url).rstrip("/")
    return {
        "status": "active",
        "version": APP_VERSION,
        "developer": brand(),
        "powered_by": brand_line(),
        "message": "OSINT & Multi-Utility API Hub. Use ?key=Demo with any endpoint.",
        "dashboard": f"{base}/dashboard",
        "docs": f"{base}/docs",
        "endpoints": [f"{base}/api/{e['path']}" for e in ENDPOINTS],
    }


# =====================================================================
# STARTUP
# =====================================================================
@app.on_event("startup")
async def on_startup():
    init_db()
    # ---- v2.6: env master keys ko DB me seed karo (dashboard me dikhein + permanently zinda) ----
    if MASTER_API_KEYS:
        try:
            with db() as conn:
                for mk in MASTER_API_KEYS:
                    row = conn.execute("SELECT id FROM api_keys WHERE api_key=?", (mk,)).fetchone()
                    if row:
                        conn.execute("UPDATE api_keys SET is_active=1 WHERE api_key=?", (mk,))
                    else:
                        conn.execute(
                            "INSERT INTO api_keys(api_key,name,note,is_active,requests,created_at,"
                            "expires_at,allowed_endpoints,device_lock,bound_devices,max_devices,rate_limit,"
                            "customer,price) VALUES(?,?,?,1,0,?,?,?,?,?,?,?,?,?)",
                            (mk, "MASTER (env)", "MASTER_API_KEY env se auto-seed", now_ist(), None,
                             "*", 0, "", 1, 0, "HIMANSHU", ""))
                conn.commit()
        except Exception:
            pass
    # ---- v2.6: DB ko GitHub par auto-backup (free plan par DB udd jati hai) ----
    if BACKUP_REPO and BACKUP_TOKEN:
        import threading
        if not any(t.name == "hub-db-backup" for t in threading.enumerate()):
            threading.Thread(target=_auto_backup_loop, name="hub-db-backup", daemon=True).start()
        # v2.6.8: startup par turant backup NAHI (warna naya deploy purane backup ko clobber kar deta hai).
        # Pehla auto-backup 15 min baad hoga; turant chahiye to POST /admin/backup/github.


if __name__ == "__main__":
    import uvicorn
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=PORT)

