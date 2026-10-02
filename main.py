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
            last_used_at TEXT
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

    defaults = {
        "upstream_base": DEFAULT_UPSTREAM,
        "upstream_key": DEFAULT_UPSTREAM_KEY,
        "upstream_enabled": "1",
        "demo_key_enabled": "1",
        "cache_ttl": "3600",
        "cache_enabled": "1",
        "rate_limit_per_min": "120",
        "admin_password": ADMIN_PASSWORD,
        "github_token": GITHUB_TOKEN,
    }
    for k, v in defaults.items():
        if get_setting(k) is None:
            set_setting(k, v)


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
    "BR29": "Katihar", "BR30": "Banka", "BR31": "Sheohar", "BR32": "Sheikhpura",
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
        if not person["name"] and not person["phones"] and not person["addresses"]:
            continue
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
    for variant in variants:
        for source_path in ("num-info", "leak-v1", "leak-v2"):
            tried.append(f"{source_path}:{variant}")
            data, _ = await upstream_call(source_path, {"q": variant}, timeout=60, retries=0)
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

    # 1) RC details (main source)
    rc_payload, _ = await upstream_call("vehicle-rc", {"number": number}, timeout=60, retries=1)
    if rc_payload is None:
        for fb in ("vehicle-info", "vehicle-details", "vehicle-v"):
            rc_payload, _ = await upstream_call(fb, {"number": number, "rc": number,
                                                     "vehicle_number": number}, timeout=60, retries=0)
            if rc_payload is not None:
                sources.append(fb)
                break
    else:
        sources.append("vehicle-rc")
    if isinstance(rc_payload, dict) and rc_payload.get("errorMsg"):
        rc_payload = None

    # 2) Extra make/model data
    if not rc_payload or not (rc_payload.get("data") or {}).get("vehicle_info"):
        info_payload, _ = await upstream_call("vehicle-info", {"vehicle_number": number},
                                              timeout=45, retries=0)
        if isinstance(info_payload, dict) and not info_payload.get("errorMsg"):
            sources.append("vehicle-info")
        else:
            info_payload = None

    # 3) Challans (detailed list + summary)
    challan_payload, _ = await upstream_call("vehicle-challan", {"number": number}, timeout=60, retries=0)
    if isinstance(challan_payload, dict) and (challan_payload.get("data") or {}).get("challan_details"):
        sources.append("vehicle-challan")
    else:
        challan_payload = None
    summary_payload, _ = await upstream_call("vehicle-challan-v4", {"number": number}, timeout=60, retries=0)
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
    rto = {
        "registered_rto": own_sec.get("Registered RTO") or local.get("rto_office") or "NA",
        "code": vinfo.get("code") or local.get("rto_code") or "NA",
        "city_name": vinfo.get("city_name") or local.get("rto_office") or "NA",
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
         mode="upstream", params=[P("q", "919973700987")], db_category="leak",
         desc="Search leaked / breach databases by phone, email or name."),
    dict(path="leak-v2", name="Leak OSINT V2", icon="🔥", category="Leak OSINT", native="leak",
         mode="upstream", params=[P("q", "919973700987")], db_category="leak",
         desc="Leak OSINT search, version 2."),
    dict(path="num-info", name="Number Info (Full Report)", icon="📞", category="Leak OSINT",
         native="num_info_full", mode="native", timeout=70,
         params=[P("q", "919973700984")],
         desc="⭐ Own number API: name, father, all phone/alt numbers, region, govt ID, addresses "
              "(own DB + num-info + leak-v1/v2, merged). Add &format=text for the card message, "
              "&deep=1 to try more number variants, &raw=1 for raw upstream payloads."),
    dict(path="number-info", name="Number Info (alias)", icon="📞", category="Leak OSINT",
         native="num_info_full", mode="native", timeout=70,
         params=[P("q", "9058390341")],
         desc="Same as /api/num-info (alias for older bots)."),
    dict(path="num", name="Number (short alias)", icon="☎️", category="Leak OSINT",
         native="num_info_full", mode="native", timeout=70,
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
         native="vehicle_report", mode="native", timeout=90,
         params=[P("number", "BR30AR0802")],
         desc="⭐ Own vehicle API by number plate: maker/model, class, fuel, owner, RTO, RC dates, "
              "insurance, PUC and full challan list. Add &format=text for the ready-to-share card."),
    dict(path="vehicle-full", name="Vehicle Report (alias)", icon="🚘", category="Vehicle",
         native="vehicle_report", mode="native", timeout=90,
         params=[P("number", "MH12DE1433")],
         desc="Alias of /api/vehicle-report."),
    dict(path="rc-info", name="RC Info (alias)", icon="📄", category="Vehicle",
         native="vehicle_report", mode="native", timeout=90,
         params=[P("rc", "BR30AR0802")],
         desc="RC details by registration number (alias of /api/vehicle-report)."),
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


def over_rate_limit(api_key: str) -> bool:
    try:
        limit = int(get_setting("rate_limit_per_min", "120") or 0)
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
    }


async def run_endpoint(request: Request, ep: Dict[str, Any]) -> JSONResponse:
    started = time.time()
    params = {k: v for k, v in request.query_params.items()
              if k not in ("key", "nocache", "_", "format")}
    api_key = request.query_params.get("key", DEMO_KEY)

    # --- api key ---
    ok, key_used, err = validate_key(api_key)
    if not ok:
        return JSONResponse(error_payload(ep, err), status_code=401)
    if over_rate_limit(key_used):
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
    ttl = int(get_setting("cache_ttl", "3600") or 0)
    if not nocache and get_setting("cache_enabled", "1") == "1":
        cached = cache_get(cache_key, ttl)
        if cached is not None:
            ms = int((time.time() - started) * 1000)
            log_request(ep["path"], key_used, params, "cache", 200, ms, client_ip(request))
            return JSONResponse(mark(cached, "cache", ep), headers={"X-Source": "cache"})

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

    if payload is None:
        ms = int((time.time() - started) * 1000)
        detail = up_error or native_error or "No data available from native source or upstream."
        if "disabled" in (up_error or ""):
            detail = ("Upstream is switched off in the dashboard (Settings tab) and no native data "
                      "was available for this endpoint.")
        log_request(ep["path"], key_used, params, "error", 502, ms, client_ip(request))
        return JSONResponse(error_payload(
            ep, "Upstream / native source returned no data.", detail), status_code=502)

    payload = mark(payload, source, ep)
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
        }
    except Exception:
        pass
    return payload


# =====================================================================
# ROUTE REGISTRATION (all 42 endpoints)
# =====================================================================
def make_handler(ep: Dict[str, Any]):
    async def handler(request: Request, **kwargs) -> JSONResponse:
        return await run_endpoint(request, ep)

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


@app.get("/admin/keys")
async def admin_list_keys(request: Request):
    require_admin(request)
    with db() as conn:
        rows = conn.execute("SELECT * FROM api_keys ORDER BY id DESC").fetchall()
    return {"success": True, "keys": [dict(r) for r in rows]}


@app.post("/admin/keys")
async def admin_create_key(request: Request, payload: Dict[str, Any] = Body(default={})):
    require_admin(request)
    import string
    alphabet = string.ascii_letters + string.digits
    new_key = (payload.get("custom_key") or "").strip()
    if not new_key:
        new_key = "osint-" + "".join(secrets.choice(alphabet) for _ in range(24))
    with db() as conn:
        exists = conn.execute("SELECT id FROM api_keys WHERE api_key=?", (new_key,)).fetchone()
        if exists:
            return JSONResponse({"success": False, "error": "Key already exists"}, status_code=400)
        conn.execute("INSERT INTO api_keys(api_key,name,note,is_active,requests,created_at)"
                     " VALUES(?,?,?,1,0,?)",
                     (new_key, payload.get("name", ""), payload.get("note", ""), now_ist()))
        conn.commit()
    return {"success": True, "api_key": new_key}


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
            "cache_ttl", "cache_enabled", "rate_limit_per_min", "github_token"]
    return {"success": True, "settings": {k: get_setting(k, "") for k in keys}}


@app.post("/admin/settings")
async def admin_save_settings(request: Request, payload: Dict[str, Any] = Body(default={})):
    require_admin(request)
    data = payload.get("settings") or payload
    for k, v in data.items():
        if k in ("upstream_base", "upstream_key", "upstream_enabled", "demo_key_enabled",
                 "cache_ttl", "cache_enabled", "rate_limit_per_min", "github_token",
                 "admin_password"):
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
@app.get("/api/endpoints")
async def list_endpoints(request: Request):
    base = str(request.base_url).rstrip("/")
    return {
        "status": "active",
        "version": APP_VERSION,
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


@app.get("/health")
async def health():
    return {"status": "ok", "time_ist": now_ist(), "version": APP_VERSION,
            "endpoints": len(ENDPOINTS), "database": os.path.abspath(DB_PATH)}


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
</header>

<nav>
  <button class="active" data-tab="endpoints">🔌 Endpoints</button>
  <button data-tab="database">🗄️ Database</button>
  <button data-tab="keys">🔑 API Keys</button>
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
    <h2>🔑 Create New API Key</h2>
    <div class="row">
      <div><label>Name / Owner</label><input id="keyName" placeholder="Rohit bhai"></div>
      <div><label>Custom key (optional)</label><input id="keyCustom" placeholder="khali chhod do to auto ban jayega"></div>
    </div>
    <button class="action" onclick="createKey()">Create Key</button>
  </div>
  <div class="card">
    <h2>📋 Existing Keys</h2>
    <p style="color:var(--muted);margin-top:0">Demo key <code>Demo</code> hamesha kaam karta hai (Settings me band kar sakte ho).</p>
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
    <div class="row"><div><label>New admin password</label><input id="stAdmin" placeholder="change karne ke liye likho"></div></div>
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
    ['endpoints','database','keys','logs','settings','help'].forEach(t=>{
      document.getElementById('tab-'+t).classList.add('hidden');
    });
    document.getElementById('tab-'+b.dataset.tab).classList.remove('hidden');
    if(b.dataset.tab==='database') loadRecords();
    if(b.dataset.tab==='keys') loadKeys();
    if(b.dataset.tab==='logs'){ loadLogs(); loadOverview(); }
    if(b.dataset.tab==='settings') loadSettings();
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

/* ---------- keys ---------- */
async function loadKeys(){
  try{
    const j = await adminFetch('/admin/keys');
    if(!j.success) return;
    document.getElementById('keyList').innerHTML =
      '<table><tr><th>ID</th><th>Key</th><th>Name</th><th>Requests</th><th>Status</th><th></th></tr>'+
      j.keys.map(k=>'<tr><td>'+k.id+'</td><td><code>'+k.api_key+'</code></td><td>'+(k.name||'')+'</td>'+
      '<td>'+k.requests+'</td><td>'+(k.is_active?'<span class="ok">Active</span>':'<span class="bad">Disabled</span>')+'</td>'+
      '<td><button class="ghost small" onclick="toggleKey('+k.id+')">'+(k.is_active?'Disable':'Enable')+'</button>'+
      '<button class="danger small" onclick="delKey('+k.id+')">🗑</button></td></tr>').join('')+'</table>';
  }catch(e){}
}
async function createKey(){
  const r = await fetch('/admin/keys',{method:'POST',headers:adminHeaders(),
    body:JSON.stringify({name:document.getElementById('keyName').value, custom_key:document.getElementById('keyCustom').value})});
  const j = await r.json();
  if(j.success){ toast('Key created ✅'); document.getElementById('keyName').value='';
    document.getElementById('keyCustom').value=''; loadKeys(); alert('New API key:\n\n'+j.api_key); }
  else toast(j.error||'Error', true);
}
async function toggleKey(id){ await fetch('/admin/keys/'+id+'/toggle',{method:'POST',headers:adminHeaders()}); loadKeys(); }
async function delKey(id){ if(!confirm('Delete key?')) return;
  await fetch('/admin/keys/'+id,{method:'DELETE',headers:adminHeaders()}); loadKeys(); }

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
    github_token: document.getElementById('stGithub').value
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

