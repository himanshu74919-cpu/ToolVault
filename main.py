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
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

# =====================================================================
# CONFIGURATION  (everything can be changed from the dashboard too)
# =====================================================================
APP_VERSION = "2.0.0"
DB_PATH = os.environ.get("DB_PATH", "osint_database.db")
PORT = int(os.environ.get("PORT", "8000"))

DEFAULT_BRAND = os.environ.get("BRAND_TAG", "@Supermannn_x")
DEFAULT_UPSTREAM = os.environ.get("UPSTREAM_BASE", "https://osint-apis-hub.onrender.com")
DEFAULT_UPSTREAM_KEY = os.environ.get("UPSTREAM_KEY", "Demo")
DEMO_KEY = os.environ.get("DEMO_KEY", "Demo")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")

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


# =====================================================================
# DATABASE (SQLite)
# =====================================================================
def db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


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
        "store_tagline": "59 Powerful APIs — Number · Vehicle · Aadhaar · YouTube · Email",
        "store_plans": "",
        "webhook_secret": os.environ.get("WEBHOOK_SECRET", ""),
        "admin_password": ADMIN_PASSWORD,
        "github_token": GITHUB_TOKEN,
    }
    for k, v in defaults.items():
        if get_setting(k) is None:
            set_setting(k, v)


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


async def upstream_call(path: str, params: Dict[str, Any], timeout: float = 45,
                        retries: int = 1) -> Tuple[Optional[Any], Optional[str]]:
    """Call the reference hub (osint-apis-hub.onrender.com by default)."""
    if get_setting("upstream_enabled", "1") != "1":
        return None, "upstream disabled"
    base = (get_setting("upstream_base", DEFAULT_UPSTREAM) or DEFAULT_UPSTREAM).rstrip("/")
    key = get_setting("upstream_key", DEFAULT_UPSTREAM_KEY) or "Demo"
    q = {k: v for k, v in params.items() if v not in (None, "")}
    q["key"] = key
    last_err = "unknown upstream error"
    for attempt in range(retries + 1):
        data, err = await http_get(f"{base}/api/{path}", params=q, timeout=timeout)
        if data is not None and not looks_like_upstream_error(data):
            return data, None
        last_err = err or str(data)[:200]
        if attempt == 0:
            await asyncio_sleep(1.0)
    return None, last_err


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


async def native_imei(params: Dict[str, Any], request: Request) -> Tuple[Optional[Dict], bool]:
    imei = clean_number(params.get("imei") or params.get("q") or "")
    if len(imei) not in (14, 15, 16):
        return {"imei": imei, "valid": False,
                "error": "IMEI must be 14-16 digits (15 is standard)"}, True
    tac, snr = imei[:8], imei[8:14]
    cd = imei[14] if len(imei) >= 15 else ""
    brand, model = TAC_HINTS.get(tac[:6], TAC_HINTS.get(tac[:4], (None, None)))
    body = REPORTING_BODIES.get(imei[:2], "Unknown / not in local table")
    known = bool(brand)
    result = {
        "imei": imei, "imei2": None, "valid_length": True,
        "luhn_check": "passed" if luhn_ok(imei) else "failed",
        "is_valid_as_per_luhn": luhn_ok(imei),
        "tac": tac, "reporting_body": body, "serial_number": snr, "check_digit": cd,
        "brand": brand, "model": model,
        "note": ("Local heuristic lookup - enable upstream for full GSMA / imei.info data"
                 if not known else "Matched local TAC table"),
        "requested_at": datetime.now(IST).isoformat(),
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
        info["error"] = "Could not parse registration number (expected e.g. HR26EV0001 / MH12DE1433)"
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
        ym = re.match(r"^(\d{2})BH(\d{4})([A-Z]{1,2})$", clean)
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


def native_vehicle(kind: str):
    """Factory: returns the native handler for a vehicle endpoint."""
    async def _inner(params: Dict[str, Any], request: Request) -> Tuple[Optional[Dict], bool]:
        number = (params.get("number") or params.get("vehicle_number") or params.get("rc")
                  or params.get("vehicle") or params.get("q") or "").strip()
        if not number:
            return None, True
        parsed = parse_vehicle(number)
        parsed["endpoint"] = kind
        parsed["local_analysis"] = True
        if kind.startswith("challan"):
            parsed["challan_data"] = "Live challan data requires upstream (Parivahan) or custom database records"
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
    q = (params.get("q") or params.get("number") or params.get("phone") or params.get("num") or "").strip()
    if not q:
        return None, True
    return phone_analysis(q), True


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
    return {
        "name": _title_name(rec.get("full_name") or rec.get("name") or ""),
        "father_name": _title_name(rec.get("the_name_of_the_father") or rec.get("father_name")
                                   or rec.get("father") or ""),
        "phones": phones,
        "region": _clean_text(rec.get("region") or rec.get("circle") or rec.get("operator") or ""),
        "govt_ids": govt_ids,
        "emails": [_clean_text(rec.get("email"))] if _clean_text(rec.get("email", "")) else [],
        "addresses": [_clean_text(rec.get("address"))] if _clean_text(rec.get("address", "")) else [],
        "sources": [source],
        "record_count": 1,
    }


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


def format_aadhaar_card(data: Dict[str, Any]) -> str:
    lines = ["╔══════════════════════════════════════╗",
             "║       📜 AADHAAR FAMILY INTEL        ║",
             "╚══════════════════════════════════════╝", ""]
    lines.append("💳 Aadhaar Number (searched)")
    lines.append(f"┗ 🎫 {data.get('aadhaar_masked', 'NA')}")
    lines.append("")
    if data.get("ration_card_number") or data.get("fps_id"):
        lines.append("📊 Card Details")
        if data.get("ration_card_number") and data["ration_card_number"] != "NA":
            lines.append(f"┗ 🎫 Ration Card: {data['ration_card_number']}")
        if data.get("fps_id") and data["fps_id"] != "NA":
            lines.append(f"┗ 🏪 FPS ID: {data['fps_id']}")
        lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("")
    members = data.get("members") or []
    lines.append(f"👨‍👩‍👧‍👦 FAMILY MEMBERS ({len(members)})")
    for idx, m in enumerate(members, 1):
        head = " 👑 (Head)" if m.get("is_head") else ""
        lines.append(f"👤 {idx}. {m.get('name') or 'Unknown'}{head}")
        lines.append(f" ┗ 💳 Aadhaar: {m.get('aadhaar_masked', 'XXXXXXXX')}")
        if m.get("relation") and not m.get("is_head"):
            lines.append(f" ┗ 🔗 {m['relation']}")
        lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("")
    lines.append("📍 LOCATION DETAILS")
    loc = data.get("location") or {}
    lines.append("🗺️ District / State")
    district_state = " / ".join([x for x in [loc.get("district"), loc.get("state")] if x]) or "NA"
    lines.append(f"┗ 🏙️ {district_state}")
    if loc.get("pincode"):
        lines.append(f"┗ 📮 PIN: {loc['pincode']}")
    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
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
                              "❌ 12 digit ka Aadhaar number daalo. Example: /api/aadhaar-family?key=Demo&aadhaar=861313813129"),
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
        terms = []
        if len(p_father) >= 8 and " s/o" not in p_father:
            terms.append(primary["father_name"])
        terms.extend(locality_terms(primary, limit=2))
        for term in terms[:3]:
            if time.time() - started > deadline * 0.85:
                break
            recs, used = await _collect_leak_records(term, sources=("leak-v1",),
                                                     timeout=25, deadline_seconds=20)
            expanded.extend(recs)
            sources.extend([s for s in used if s not in sources])

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
                "name": _title_name(person.get("name") or "") or "Unknown",
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
        "name": _title_name(primary.get("name") or "") or "Unknown",
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


# ---------------------------------------------------------------------
# YOUTUBE DOWNLOADER (for Telegram bots)
# ---------------------------------------------------------------------
def _yt_extract(url: str, mode: str, quality: str, timeout: int = 40) -> Dict[str, Any]:
    """Blocking yt-dlp extraction (run inside a thread)."""
    try:
        import yt_dlp  # optional dependency
    except Exception as exc:
        return {"error": f"yt-dlp installed nahi hai: {exc}"}

    if mode == "audio":
        fmt = "bestaudio[ext=m4a]/bestaudio/best"
    elif quality and quality.isdigit():
        fmt = (f"bestvideo[height<={quality}][ext=mp4]+bestaudio[ext=m4a]/"
               f"best[height<={quality}][ext=mp4]/best[ext=mp4]/best")
    else:
        fmt = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"

    opts = {
        "quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True,
        "format": fmt, "socket_timeout": timeout, "nocheckcertificate": True,
        "source_address": None, "geo_bypass": True,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        return {"error": str(exc)[:300]}

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
    deadline = float(get_setting("max_request_seconds", "50") or 50)
    try:
        loop = __import__("asyncio").get_event_loop()
        result = await loop.run_in_executor(
            None, lambda: _yt_extract(watch, "audio" if mode == "audio" else mode, quality,
                                      timeout=int(max(20, min(deadline - 5, 40)))))
    except Exception as exc:
        error = str(exc)[:200]

    # fallback: upstream download links (agar yt-dlp fail ho ya installed na ho)
    links = result.get("links") if isinstance(result, dict) else None
    upstream_links: List[Dict[str, Any]] = []
    sources: List[str] = []
    if not links and (time.time() - started) < deadline * 0.8:
        up, _ = await upstream_call("youtube-all", {"url": watch}, timeout=30, retries=0)
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
            result = {
                "video_id": up.get("video_id") or vid,
                "title": ((up.get("video_info") or {}) or {}).get("title"),
                "channel": ((up.get("channel_info") or {}) or {}).get("title"),
                "duration": ((up.get("video_info") or {}) or {}).get("lengthSeconds"),
                "thumbnail": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                "links": upstream_links,
            }

    all_links = (result.get("links") or []) if isinstance(result, dict) else []
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
        "error": error or (result.get("error") if isinstance(result, dict) else None),
        "sources_used": ["yt-dlp"] if (result.get("links") and not sources) else (sources or ["yt-dlp"]),
        "response_time": f"{round(time.time() - started, 2)}s",
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

    # 2) upstream sources
    deadline = float(get_setting("max_request_seconds", "50") or 50)
    for variant in variants:
        for source_path in ("num-info", "leak-v1", "leak-v2"):
            if time.time() - started > deadline:
                break
            tried.append(f"{source_path}:{variant}")
            data, _ = await upstream_call(source_path, {"q": variant}, timeout=35, retries=0)
            if data is None:
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
         mode="merge_upstream", params=[P("imei", "353010111111110")],
         desc="IMEI validation (Luhn), TAC / reporting body decode, brand hint + upstream device data."),

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
    dict(path="vehicle-challan", name="Vehicle Challan", icon="🚗", category="Vehicle",
         native="vehicle_challan", mode="merge_native", params=[P("number", "HR26EV0001")],
         desc="Vehicle challan lookup (upstream) + offline RTO parsing."),
    dict(path="vehicle-challan-v2", name="Vehicle Challan V2", icon="🚗", category="Vehicle",
         native="vehicle_challan_v2", mode="merge_native", params=[P("number", "HR26EV0001")],
         desc="Vehicle challan lookup version 2."),
    dict(path="vehicle-challan-v4", name="Vehicle Challan V4", icon="🚗", category="Vehicle",
         native="vehicle_challan_v4", mode="merge_native", params=[P("number", "HR26EV0001")],
         desc="Vehicle challan lookup version 4."),
    dict(path="vehicle-info", name="Vehicle Info", icon="🚙", category="Vehicle",
         native="vehicle_info", mode="merge_native", params=[P("vehicle_number", "HR26EV0001")],
         desc="Registration number decode: state, RTO district, series + upstream details."),
    dict(path="vehicle-info-v2", name="Vehicle Info V2", icon="🚙", category="Vehicle",
         native="vehicle_info_v2", mode="merge_native", params=[P("vehicle_number", "HR26EV0001")],
         desc="Vehicle information version 2."),
    dict(path="vehicle-rc", name="Vehicle RC", icon="📄", category="Vehicle",
         native="vehicle_rc", mode="merge_native", params=[P("number", "HR26EV0001")],
         desc="RC / registration details (upstream) with offline RTO decoding."),
    dict(path="vehicle-details", name="Vehicle Details", icon="🧾", category="Vehicle",
         native="vehicle_details", mode="merge_native", params=[P("number", "MH12DE1433")],
         desc="Full vehicle details (owner, insurance, PUCC if upstream provides)."),
    dict(path="vehicle-v", name="Vehicle V", icon="🚘", category="Vehicle",
         native="vehicle_v", mode="merge_native", params=[P("rc", "MH12DE1433")],
         desc="Vehicle verification by RC number."),

    # ---------- Media ----------
    dict(path="song", name="Song Downloader", icon="🎵", category="Media", native="song",
         mode="upstream", params=[P("song", "chandani")],
         desc="Search songs: upstream (Saavn download links) else Apple Music preview links."),

    # ---------- Social ----------
    dict(path="snap-stories", name="Snapchat Stories", icon="👻", category="Social", native=None,
         mode="upstream", params=[P("username", "priyapanchal272")],
         desc="Snapchat public stories for a username."),
    dict(path="snap-highlights", name="Snapchat Highlights", icon="👻", category="Social", native=None,
         mode="upstream", params=[P("username", "priyapanchal272")],
         desc="Snapchat public highlights for a username."),
    dict(path="instagram-profile", name="Instagram Profile", icon="📸", category="Social", native=None,
         mode="upstream", params=[P("username", "sumit_sharma2")],
         desc="Instagram profile info (followers, bio, profile picture)."),
    dict(path="instagram-posts", name="Instagram Posts", icon="📸", category="Social", native=None,
         mode="upstream", params=[P("username", "sumit_sharma2")],
         desc="Recent Instagram posts / media for a username."),

    # ---------- File / Cloud ----------
    dict(path="terabox-file", name="Terabox File", icon="📦", category="Files", native=None,
         mode="upstream", params=[P("url", "https://1024terabox.com/s/1ahJz-qdH7h_9One0lXxDoA")],
         desc="Terabox file metadata from a share link."),
    dict(path="terabox-stream", name="Terabox Stream", icon="📦", category="Files", native=None,
         mode="upstream", params=[P("url", "https://1024terabox.com/s/1EqwgqWQgmeOvQxc33258UA")],
         desc="Direct streaming / download links for Terabox content."),
    dict(path="terabox-stream-v2", name="Terabox Stream V2", icon="📦", category="Files", native=None,
         mode="upstream", params=[P("url", "https://1024terabox.com/s/1EqwgqWQgmeOvQxc33258UA")],
         desc="Terabox streaming, version 2."),
    dict(path="terabox-stream-v3", name="Terabox Stream V3", icon="📦", category="Files", native=None,
         mode="upstream", params=[P("url", "https://1024terabox.com/s/1EqwgqWQgmeOvQxc33258UA")],
         desc="Terabox streaming, version 3."),

    # ---------- Gaming ----------
    dict(path="bgmi", name="BGMI Info", icon="🎮", category="Gaming", native=None,
         mode="upstream", params=[P("user", "55622571339")],
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
    dict(path="leak-v1", name="Leak OSINT V1", icon="🔥", category="Leak OSINT", native="leak",
         mode="upstream", params=[P("q", "919973700987")], db_category="leak", cache_ttl=43200,
         desc="Search leaked / breach databases by phone, email or name."),
    dict(path="leak-v2", name="Leak OSINT V2", icon="🔥", category="Leak OSINT", native="leak",
         mode="upstream", params=[P("q", "919973700987")], db_category="leak", cache_ttl=43200,
         desc="Leak OSINT search, version 2."),
    dict(path="num-info", name="Number Info (Full Report)", icon="📞", category="Leak OSINT",
         native="num_info_full", mode="native", timeout=70, cache_ttl=43200,
         params=[P("q", "919973700984")],
         desc="⭐ Own number API: name, father, all phone/alt numbers, region, govt ID, addresses "
              "(own DB + num-info + leak-v1/v2, merged). Add &format=text for the card message, "
              "&deep=1 to try more number variants, &raw=1 for raw upstream payloads."),
    dict(path="number-info", name="Number Info (alias)", icon="📞", category="Leak OSINT",
         native="num_info_full", mode="native", timeout=70, cache_ttl=43200,
         params=[P("q", "9058390341")],
         desc="Same as /api/num-info (alias for older bots)."),
    dict(path="num", name="Number (short alias)", icon="☎️", category="Leak OSINT",
         native="num_info_full", mode="native", timeout=70, cache_ttl=43200,
         params=[P("q", "9058390341")],
         desc="Short alias of /api/num-info."),

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
    dict(path="vehicle-report", name="Vehicle Report (RC + Challan)", icon="🚘", category="Vehicle",
         native="vehicle_report", mode="native", timeout=90, cache_ttl=21600,
         params=[P("number", "BR30AR0802")],
         desc="⭐ Own vehicle API by number plate: maker/model, class, fuel, owner, RTO, RC dates, "
              "insurance, PUC and full challan list. Add &format=text for the ready-to-share card."),
    dict(path="vehicle-full", name="Vehicle Report (alias)", icon="🚘", category="Vehicle",
         native="vehicle_report", mode="native", timeout=90, cache_ttl=21600,
         params=[P("number", "MH12DE1433")],
         desc="Alias of /api/vehicle-report."),
    dict(path="rc-info", name="RC Info (alias)", icon="📄", category="Vehicle",
         native="vehicle_report", mode="native", timeout=90, cache_ttl=21600,
         params=[P("rc", "BR30AR0802")],
         desc="RC details by registration number (alias of /api/vehicle-report)."),

    # ---------- New: family + email ----------
    dict(path="family", name="Family / Linked Numbers", icon="👨‍👩‍👧", category="Leak OSINT",
         native="family", mode="native", timeout=70, cache_ttl=43200,
         params=[P("q", "9058390341")],
         desc="⭐ Number ya naam daalo → uske linked/family members ke numbers (same address, "
              "same father, alt numbers) ek card me. &format=text se ready message, &deep=0 se fast."),
    dict(path="num-family", name="Family (alias)", icon="👨‍👩‍👧", category="Leak OSINT",
         native="family", mode="native", timeout=70, cache_ttl=43200,
         params=[P("q", "Pramila Hembram")],
         desc="Alias of /api/family - naam se bhi search kar sakte hain."),
    dict(path="email-info", name="Email OSINT", icon="📧", category="Email",
         native="email_info", mode="native", timeout=60, cache_ttl=43200,
         params=[P("email", "ranjitkumarlalgonv@gamil.com")],
         desc="⭐ Email → provider, MX/SPF records, Gravatar, disposable check + leak/combo records "
              "(naam, phone, address, leaked passwords). &format=text se card."),
    dict(path="email", name="Email OSINT (alias)", icon="📧", category="Email",
         native="email_info", mode="native", timeout=60, cache_ttl=43200,
         params=[P("email", "test@gmail.com")],
         desc="Alias of /api/email-info."),
    dict(path="pass-check", name="Password Breach Check", icon="🔑", category="Email",
         native="pass_check", mode="native", timeout=30, cache_ttl=86400,
         params=[P("password", "Katihar@123")],
         desc="Password kisi breach me hai ya nahi (Pwned Passwords k-anonymity - password server "
              "se bahar nahi jata)."),

    # ---------- Aadhaar family + YouTube downloader ----------
    dict(path="aadhaar-family", name="Aadhaar Family Intel", icon="📜", category="Aadhaar",
         native="aadhaar_family", mode="native", timeout=70, cache_ttl=43200,
         params=[P("aadhaar", "861313813129")],
         desc="⭐ 12-digit Aadhaar/UID daalo → parivar ke members (masked Aadhaar, head of family "
              "crown, relation) + district/state. &format=text se ready card."),
    dict(path="aadhaar", name="Aadhaar Family (alias)", icon="📜", category="Aadhaar",
         native="aadhaar_family", mode="native", timeout=70, cache_ttl=43200,
         params=[P("aadhaar", "300664932743")],
         desc="Alias of /api/aadhaar-family."),
    dict(path="ration", name="Ration / Family (alias)", icon="🎫", category="Aadhaar",
         native="aadhaar_family", mode="native", timeout=70, cache_ttl=43200,
         params=[P("q", "861313813129")],
         desc="Alias of /api/aadhaar-family (ration card number se bhi try karein)."),
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


# =====================================================================
# ENDPOINT RUNNER
# =====================================================================
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
    params = {k: v for k, v in (extra_params if extra_params is not None else request.query_params).items()
              if k not in ("key", "nocache", "_", "format")}
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
        return JSONResponse(error_payload(
            ep, "Upstream / native source returned no data.", detail), status_code=502)

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
    token = request.headers.get("x-admin-token", "") or request.query_params.get("token", "")
    return bool(token) and token == get_setting("admin_password", ADMIN_PASSWORD)


def require_admin(request: Request):
    if not admin_authorized(request):
        raise HTTPException(status_code=401, detail="Admin login required")


from fastapi import Body, HTTPException  # noqa: E402  (imported late to keep the file readable)


@app.post("/admin/login")
async def admin_login(request: Request, payload: Dict[str, Any] = Body(default={})):
    password = payload.get("password", "")
    if password == get_setting("admin_password", ADMIN_PASSWORD):
        return {"success": True, "message": "Login ok"}
    return JSONResponse({"success": False, "error": "Wrong password"}, status_code=401)


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
    data = payload.get("data")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            data = {"value": data}
    with db() as conn:
        conn.execute(
            "UPDATE custom_records SET category=COALESCE(?,category), key_value=COALESCE(?,key_value),"
            " data=COALESCE(?,data), note=COALESCE(?,note), updated_at=? WHERE id=?",
            (payload.get("category"), payload.get("key_value"),
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
    endpoints = (payload.get("allowed_endpoints") or payload.get("plan") or "*").strip() or "*"
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
        fields.append("allowed_endpoints=?")
        values.append((payload["allowed_endpoints"] or "*").strip() or "*")
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
    endpoints = (payload.get("allowed_endpoints") or "*").strip()
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
    with db() as conn:
        if conn.execute("SELECT id FROM resellers WHERE username=?", (username,)).fetchone():
            return JSONResponse({"success": False, "error": "Username already exists"}, status_code=400)
        conn.execute("INSERT INTO resellers(username,password_hash,name,telegram,is_active,max_days,"
                     "allowed_endpoints,credit,keys_created,created_at) VALUES(?,?,?,?,1,?,?,?,0,?)",
                     (username, hash_pw(password), payload.get("name", ""),
                      payload.get("telegram", ""), int(payload.get("max_days") or 30),
                      (payload.get("allowed_endpoints") or "*").strip() or "*",
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
    with db() as conn:
        if payload.get("max_days") is not None:
            conn.execute("UPDATE resellers SET max_days=? WHERE id=?",
                         (int(payload["max_days"]), rid))
        if payload.get("allowed_endpoints") is not None:
            conn.execute("UPDATE resellers SET allowed_endpoints=? WHERE id=?",
                         ((payload["allowed_endpoints"] or "*").strip() or "*", rid))
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
    raw = get_setting("store_plans", "")
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, list) and data:
                return data
        except Exception:
            pass
    return [
        {"id": "trial", "name": "Trial Pack", "days": 3, "endpoints": "num-info,family",
         "price": 29, "description": "3 din · Number + Family API"},
        {"id": "num", "name": "Number Pack", "days": 30, "endpoints": "num-info,family,num,number-info",
         "price": 100, "description": "30 din · Number + Family report"},
        {"id": "vehicle", "name": "Vehicle Pack", "days": 30,
         "endpoints": "vehicle-report,vehicle-full,rc-info,vehicle-rc,vehicle-challan",
         "price": 100, "description": "30 din · RC + Challan full report"},
        {"id": "aadhaar", "name": "Aadhaar Pack", "days": 30,
         "endpoints": "aadhaar-family,aadhaar,ration",
         "price": 150, "description": "30 din · Aadhaar family intel"},
        {"id": "full", "name": "Full Access", "days": 30, "endpoints": "*",
         "price": 299, "description": "30 din · SARE 59 endpoints"},
        {"id": "reseller", "name": "Reseller Pack", "days": 365, "endpoints": "*",
         "price": 999, "description": "1 saal · Full access (resale allowed)"},
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
    amount = float(payload.get("amount") or plan.get("price") or 0)
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
        for row in rows:
            try:
                if abs(float(row["amount"]) - amt) < 1:
                    return row
            except Exception:
                continue
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


@app.get("/health")
async def health():
    return {"status": "ok", "time_ist": now_ist(), "version": APP_VERSION,
            "endpoints": len(ENDPOINTS), "database": os.path.abspath(DB_PATH),
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
  <input id="custPhone" placeholder="919973700984">
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
    <option value="num-info|q|919973700984">Number Info</option>
    <option value="vehicle-report|number|BR30AR0802">Vehicle RC + Challan</option>
    <option value="family|q|919973700984">Family / Linked Numbers</option>
    <option value="aadhaar-family|aadhaar|861313813129">Aadhaar Family</option>
    <option value="email-info|email|test@gmail.com">Email OSINT</option>
    <option value="pass-check|password|Katihar@123">Password Breach Check</option>
    <option value="youtube-download|url|https://youtube.com/watch?v=X8X-XyK4CYE">YouTube Download</option>
  </select>
  <label>Value</label>
  <input id="demoVal" value="919973700984">
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
    '<ul><li>'+p.description+'</li><li>Endpoints: '+(p.endpoints==='*'?'SAB (59)':p.endpoints.split(',').length+' APIs')+'</li>'+
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
      'Example:\n'+location.origin+'/api/num-info?key='+j.api_key+'&q=919973700984&format=text</pre>';
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
    tagline = get_setting("store_tagline", "") or "59 Powerful APIs"
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
          <option value="leak">leak (leak OSINT)</option>
          <option value="phone">phone (num-info)</option>
          <option value="gst">gst</option>
          <option value="pan">pan</option>
          <option value="vehicle">vehicle</option>
          <option value="imei">imei</option>
          <option value="ip">ip</option>
          <option value="general">general</option>
        </select>
      </div>
      <div><label>Key (phone / GSTIN / PAN / number)</label>
        <input id="recKey" placeholder="919973700984"></div>
    </div>
    <label>Data (JSON)</label>
    <textarea id="recData">{"full_name":"Example Name","address":"Bihar","phone":"919973700984"}</textarea>
    <label>Note (optional)</label>
    <input id="recNote" placeholder="kahan se mila">
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
        <option value="leak">leak</option><option value="phone">phone</option>
        <option value="gst">gst</option><option value="pan">pan</option>
        <option value="vehicle">vehicle</option><option value="general">general</option>
      </select></div></div>
    <textarea id="impCsv" placeholder="919973700984,{&quot;full_name&quot;:&quot;Ram Kumar&quot;},from telegram"></textarea>
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
      <div><label>Plan (kaunsi API)</label>
        <select id="keyPlan" onchange="togglePlanBox()">
          <option value="*">SAB endpoints (full access)</option>
          <option value="num-info,vehicle-report,family,email-info">Popular pack (num-info + vehicle-report + family + email-info)</option>
          <option value="num-info,family">Sirf Number pack (num-info + family)</option>
          <option value="vehicle-report,vehicle-rc,vehicle-challan">Sirf Vehicle pack</option>
          <option value="email-info,pass-check">Sirf Email pack</option>
          <option value="custom">Custom (khud likho)</option>
        </select>
      </div>
      <div id="customPlanBox" style="display:none">
        <label>Endpoints (comma separated)</label>
        <input id="keyEndpoints" placeholder="num-info,vehicle-report">
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
      <div><label>Website tagline</label><input id="stTagline" placeholder="59 Powerful APIs..."></div>
    </div>
    <div class="row"><div><label>Plans JSON (advanced - khali chhod do to default plans)</label>
      <textarea id="stPlans" placeholder='[{"id":"num","name":"Number Pack","days":30,"endpoints":"num-info,family","price":100,"description":"30 din"}]'></textarea></div></div>
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
    ['endpoints','database','keys','resellers','payments','logs','settings','help'].forEach(t=>{
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
    const plan = (k.allowed_endpoints==='*'||!k.allowed_endpoints)?'All endpoints':k.allowed_endpoints;
    const msg = '✅ Aapka OSINT API key ready hai\n\n'+
      '🔑 Key: '+k.api_key+'\n'+
      '📦 Plan: '+plan+'\n'+
      '⏳ Valid till: '+(k.expires_at||'Lifetime')+'\n\n'+
      '📌 Examples:\n'+
      base+'/api/num-info?key='+k.api_key+'&q=919973700984&format=text\n'+
      base+'/api/vehicle-report?key='+k.api_key+'&number=BR30AR0802&format=text\n'+
      base+'/api/family?key='+k.api_key+'&q=919973700984&format=text\n'+
      base+'/api/email-info?key='+k.api_key+'&email=test@gmail.com&format=text\n\n'+
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


if __name__ == "__main__":
    import uvicorn
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=PORT)

