import hashlib
import json
import os
import socket
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

# =====================================================================
# CONFIGURATION
# =====================================================================
DB_PATH = os.environ.get("DB_PATH", "osint_database.db")
API_SECRET_KEY = os.environ.get("API_SECRET_KEY", "")  # Optional API Key protection

app = FastAPI(
    title="OSINT & Breach Exposure Database API",
    description=(
        "Cloud-ready OSINT & Breach Exposure API with built-in SQLite Database. "
        "Designed to be managed easily from Samsung Galaxy Tab A9+ 5G or any browser."
    ),
    version="1.0.0",
)

# Enable CORS for all origins so preview & external clients work seamlessly
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =====================================================================
# DATABASE SETUP (SQLite)
# =====================================================================
def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    # 1. Custom OSINT & Breach Metadata Records Table
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS osint_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            indicator TEXT NOT NULL,
            indicator_type TEXT NOT NULL DEFAULT 'email',
            source_name TEXT NOT NULL,
            breach_date TEXT DEFAULT '',
            exposed_data TEXT DEFAULT '',
            severity TEXT DEFAULT 'Medium',
            notes TEXT DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_indicator ON osint_records(indicator)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_indicator_type ON osint_records(indicator_type)"
    )

    # 2. Query History & Audit Log Table
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS search_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query_type TEXT NOT NULL,
            query_value TEXT NOT NULL,
            result_summary TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    # Seed initial demo records if table is empty
    cursor.execute("SELECT COUNT(*) as count FROM osint_records")
    row = cursor.fetchone()
    if row and row["count"] == 0:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        sample_records = [
            (
                "test@example.com",
                "email",
                "Collection-Demo-Breach",
                "2023-05",
                "Email addresses, Usernames, IP addresses",
                "High",
                "Sample demo record for testing Email Breach DB lookup",
                now,
            ),
            (
                "admin@corp-demo.org",
                "email",
                "CorpPortal-Exposure-2024",
                "2024-11",
                "Email addresses, Password Hashes (bcrypt), Job Titles",
                "Critical",
                "Demo corporate exposure alert",
                now,
            ),
            (
                "demo_user_99",
                "username",
                "GamingForum-Public-Dump-Metadata",
                "2022-08",
                "Usernames, Public Profile URL, Registration Date",
                "Low",
                "Public forum metadata index sample",
                now,
            ),
            (
                "example.com",
                "domain",
                "Public-Domain-Recon",
                "2025-01",
                "Subdomains, MX Records, Public Contact Emails",
                "Info",
                "Domain reconnaissance baseline record",
                now,
            ),
        ]
        cursor.executemany(
            """
            INSERT INTO osint_records
            (indicator, indicator_type, source_name, breach_date, exposed_data, severity, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            sample_records,
        )

    conn.commit()
    conn.close()


init_db()


def log_query(query_type: str, query_value: str, summary: str):
    try:
        conn = get_db_connection()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        conn.execute(
            "INSERT INTO search_logs (query_type, query_value, result_summary, created_at) VALUES (?, ?, ?, ?)",
            (query_type, query_value, summary, now),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# =====================================================================
# OPTIONAL API KEY VERIFICATION
# =====================================================================
def verify_api_key(
    api_key: Optional[str] = Query(None, description="Optional API Key"),
    x_api_key: Optional[str] = Header(None),
):
    if not API_SECRET_KEY:
        return True
    provided = api_key or x_api_key
    if provided != API_SECRET_KEY:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API Key. Pass ?api_key=YOUR_KEY or X-API-Key header.",
        )
    return True


# =====================================================================
# PYDANTIC MODELS
# =====================================================================
class RecordCreate(BaseModel):
    indicator: str = Field(..., example="user@example.com")
    indicator_type: str = Field(
        default="email", example="email (email, username, domain, ip, phone_hash)"
    )
    source_name: str = Field(..., example="Security-Audit-2026")
    breach_date: str = Field(default="2026-01", example="2026-01")
    exposed_data: str = Field(
        default="Email, Username", example="Email addresses, Usernames"
    )
    severity: str = Field(default="Medium", example="High")
    notes: str = Field(default="", example="Found during public OSINT check")


class BulkRecordImport(BaseModel):
    records: List[RecordCreate]


# =====================================================================
# API ENDPOINTS
# =====================================================================


@app.get("/api/v1/health", tags=["System"])
def health_check():
    """Check if the API and SQLite Database are online."""
    conn = get_db_connection()
    count = conn.execute("SELECT COUNT(*) FROM osint_records").fetchone()[0]
    logs_count = conn.execute("SELECT COUNT(*) FROM search_logs").fetchone()[0]
    conn.close()
    return {
        "status": "online",
        "database": "SQLite connected",
        "total_db_records": count,
        "total_searches_logged": logs_count,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/v1/breach/email", tags=["Breach & Leak OSINT"])
async def check_email_breach(
    email: str = Query(..., description="Email address to check for public breach exposure"),
    _: bool = Depends(verify_api_key),
):
    """
    Checks an email address against:
    1. Your local Custom SQLite Database (`osint_records`)
    2. Public XposedOrNot Breach Intelligence API (free legal HIBP-alternative)
    """
    clean_email = email.strip().lower()
    if "@" not in clean_email:
        raise HTTPException(status_code=400, detail="Valid email address required.")

    # 1. Search Local Database
    conn = get_db_connection()
    local_rows = conn.execute(
        "SELECT * FROM osint_records WHERE LOWER(indicator) = ? ORDER BY id DESC",
        (clean_email,),
    ).fetchall()
    conn.close()
    local_matches = [dict(r) for r in local_rows]

    # 2. Query XposedOrNot Public API for known breaches
    public_breaches = []
    public_api_status = "checked"
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(
                f"https://api.xposedornot.com/v1/check-email/{clean_email}",
                headers={"User-Agent": "OSINT-Tablet-API/1.0"},
            )
            if resp.status_code == 200:
                data = resp.json()
                # XposedOrNot returns {"breaches": [["BreachA", "BreachB", ...]]}
                raw_list = data.get("breaches", [])
                if raw_list and isinstance(raw_list, list):
                    for item in raw_list:
                        if isinstance(item, list):
                            public_breaches.extend(item)
                        elif isinstance(item, str):
                            public_breaches.append(item)
            elif resp.status_code == 404:
                public_api_status = "no_public_breach_found"
            else:
                public_api_status = f"http_{resp.status_code}"
        except Exception as e:
            public_api_status = f"offline_or_rate_limited ({type(e).__name__})"

    total_found = len(local_matches) + len(public_breaches)
    is_exposed = total_found > 0

    log_query(
        "email_breach",
        clean_email,
        f"Exposed={is_exposed} (Local={len(local_matches)}, Public={len(public_breaches)})",
    )

    return {
        "query": clean_email,
        "exposed": is_exposed,
        "risk_level": (
            "HIGH"
            if total_found >= 3
            else ("MEDIUM" if total_found >= 1 else "LOW / SAFE")
        ),
        "summary": {
            "local_database_matches": len(local_matches),
            "public_breach_count": len(public_breaches),
            "public_api_status": public_api_status,
        },
        "local_database_records": local_matches,
        "public_breaches": public_breaches,
    }


@app.get("/api/v1/breach/password", tags=["Breach & Leak OSINT"])
async def check_password_leak(
    password: str = Query(
        ...,
        description="Password to check safely using k-Anonymity (only first 5 chars of SHA-1 hash are sent externally)",
    ),
    _: bool = Depends(verify_api_key),
):
    """
    Safely checks if a password has appeared in known data leaks using the
    HaveIBeenPwned k-Anonymity SHA-1 Range API.
    The full password NEVER leaves this server.
    """
    sha1_hash = hashlib.sha1(password.encode("utf-8")).hexdigest().upper()
    prefix = sha1_hash[:5]
    suffix = sha1_hash[5:]

    leak_count = 0
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(
                f"https://api.pwnedpasswords.com/range/{prefix}",
                headers={"User-Agent": "OSINT-Tablet-API/1.0"},
            )
            if resp.status_code == 200:
                for line in resp.text.splitlines():
                    if ":" in line:
                        h_suffix, count_str = line.strip().split(":", 1)
                        if h_suffix.upper() == suffix:
                            leak_count = int(count_str)
                            break
        except Exception as e:
            raise HTTPException(
                status_code=502,
                detail=f"Could not reach PwnedPasswords k-Anonymity service: {str(e)}",
            )

    log_query(
        "password_k_anonymity",
        f"SHA1:{prefix}***",
        f"Leaked={leak_count > 0} (Count={leak_count})",
    )

    return {
        "sha1_prefix_checked": prefix,
        "is_leaked": leak_count > 0,
        "exposure_count": leak_count,
        "verdict": (
            f"DANGER! This password appeared {leak_count:,} times in public leaks. Never use it!"
            if leak_count > 0
            else "SAFE! This password was not found in public breach hash databases."
        ),
    }


@app.get("/api/v1/osint/target", tags=["Network & Domain OSINT"])
async def target_recon_osint(
    target: str = Query(..., description="IP address or Domain name (e.g. google.com or 8.8.8.8)"),
    _: bool = Depends(verify_api_key),
):
    """
    Performs OSINT reconnaissance on a Domain or IP address:
    - DNS Resolution
    - Geolocation, ISP, ASN & Organization lookup (via ip-api.com)
    - Checks local SQLite database for any matching domain/IP indicators
    """
    clean_target = (
        target.strip()
        .lower()
        .replace("https://", "")
        .replace("http://", "")
        .split("/")[0]
    )

    resolved_ip = clean_target
    dns_error = None
    try:
        resolved_ip = socket.gethostbyname(clean_target)
    except Exception as e:
        dns_error = str(e)

    geo_data: Dict[str, Any] = {}
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(
                f"http://ip-api.com/json/{resolved_ip}?fields=status,message,country,countryCode,regionName,city,zip,lat,lon,timezone,isp,org,as,reverse,mobile,proxy,hosting,query"
            )
            if resp.status_code == 200:
                geo_data = resp.json()
        except Exception as e:
            geo_data = {"status": "error", "message": str(e)}

    # Check local DB for domain or IP
    conn = get_db_connection()
    local_rows = conn.execute(
        "SELECT * FROM osint_records WHERE LOWER(indicator) IN (?, ?) ORDER BY id DESC",
        (clean_target, resolved_ip),
    ).fetchall()
    conn.close()
    local_matches = [dict(r) for r in local_rows]

    log_query(
        "target_osint",
        clean_target,
        f"IP={resolved_ip}, Country={geo_data.get('country', 'N/A')}",
    )

    return {
        "target": clean_target,
        "resolved_ip": resolved_ip,
        "dns_error": dns_error,
        "network_intelligence": geo_data,
        "local_database_matches": local_matches,
    }


# =====================================================================
# CUSTOM OSINT DATABASE CRUD ENDPOINTS
# =====================================================================


@app.get("/api/v1/db/search", tags=["Custom OSINT Database"])
def search_database(
    q: str = Query("", description="Search query (email, username, domain, source, or keyword)"),
    indicator_type: str = Query("", description="Filter by type: email, username, domain, ip"),
    limit: int = Query(50, ge=1, le=500),
    _: bool = Depends(verify_api_key),
):
    """
    Search your custom OSINT SQLite database via API.
    Leave `q` empty to list the latest records.
    """
    conn = get_db_connection()
    sql = "SELECT * FROM osint_records WHERE 1=1"
    params: List[Any] = []

    if q.strip():
        like_q = f"%{q.strip().lower()}%"
        sql += " AND (LOWER(indicator) LIKE ? OR LOWER(source_name) LIKE ? OR LOWER(exposed_data) LIKE ? OR LOWER(notes) LIKE ?)"
        params.extend([like_q, like_q, like_q, like_q])

    if indicator_type.strip():
        sql += " AND LOWER(indicator_type) = ?"
        params.append(indicator_type.strip().lower())

    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    conn.close()

    results = [dict(r) for r in rows]
    if q.strip():
        log_query("db_search", q.strip(), f"Found {len(results)} records")

    return {
        "query": q,
        "filter_type": indicator_type or "all",
        "count": len(results),
        "results": results,
    }


@app.post("/api/v1/db/records", tags=["Custom OSINT Database"])
def add_database_record(
    record: RecordCreate,
    _: bool = Depends(verify_api_key),
):
    """Add a new OSINT / Breach Metadata record to the SQLite database."""
    conn = get_db_connection()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO osint_records
        (indicator, indicator_type, source_name, breach_date, exposed_data, severity, notes, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record.indicator.strip(),
            record.indicator_type.strip().lower(),
            record.source_name.strip(),
            record.breach_date.strip(),
            record.exposed_data.strip(),
            record.severity.strip(),
            record.notes.strip(),
            now,
        ),
    )
    new_id = cursor.lastrowid
    conn.commit()
    conn.close()

    return {
        "status": "created",
        "id": new_id,
        "indicator": record.indicator,
        "created_at": now,
    }


@app.post("/api/v1/db/bulk", tags=["Custom OSINT Database"])
def bulk_import_records(
    payload: BulkRecordImport,
    _: bool = Depends(verify_api_key),
):
    """Bulk import multiple OSINT records at once into the database."""
    conn = get_db_connection()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    rows_to_insert = [
        (
            r.indicator.strip(),
            r.indicator_type.strip().lower(),
            r.source_name.strip(),
            r.breach_date.strip(),
            r.exposed_data.strip(),
            r.severity.strip(),
            r.notes.strip(),
            now,
        )
        for r in payload.records
        if r.indicator.strip()
    ]
    conn.executemany(
        """
        INSERT INTO osint_records
        (indicator, indicator_type, source_name, breach_date, exposed_data, severity, notes, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows_to_insert,
    )
    conn.commit()
    conn.close()
    return {
        "status": "bulk_imported",
        "inserted_count": len(rows_to_insert),
    }


@app.delete("/api/v1/db/records/{record_id}", tags=["Custom OSINT Database"])
def delete_database_record(
    record_id: int,
    _: bool = Depends(verify_api_key),
):
    """Delete a record by its ID."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM osint_records WHERE id = ?", (record_id,))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    if deleted == 0:
        raise HTTPException(status_code=404, detail="Record ID not found")
    return {"status": "deleted", "id": record_id}


@app.get("/api/v1/db/logs", tags=["System"])
def get_recent_logs(limit: int = Query(25, ge=1, le=200)):
    """View recent API search logs."""
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT * FROM search_logs ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return {"count": len(rows), "logs": [dict(r) for r in rows]}


# =====================================================================
# TABLET-FRIENDLY WEB DASHBOARD (NO CODING NEEDED)
# =====================================================================
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>OSINT & Breach Database API — Control Panel</title>
  <style>
    :root {
      --bg: #0b0f19;
      --card: #131b2e;
      --card-border: #1e293b;
      --accent: #06b6d4;
      --accent-hover: #0891b2;
      --success: #10b981;
      --danger: #ef4444;
      --warning: #f59e0b;
      --text: #f1f5f9;
      --muted: #94a3b8;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.5;
      padding-bottom: 50px;
    }
    header {
      background: linear-gradient(135deg, #0f172a 0%, #1e1b4b 100%);
      border-bottom: 1px solid var(--card-border);
      padding: 20px 24px;
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }
    .brand h1 {
      font-size: 1.35rem;
      color: #38bdf8;
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .brand p {
      font-size: 0.88rem;
      color: var(--muted);
      margin-top: 2px;
    }
    .header-links {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }
    .btn {
      background: var(--accent);
      color: #000;
      font-weight: 600;
      padding: 10px 16px;
      border-radius: 8px;
      border: none;
      cursor: pointer;
      font-size: 0.92rem;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      transition: 0.15s ease;
    }
    .btn:hover { background: var(--accent-hover); color: #fff; }
    .btn-outline {
      background: rgba(56, 189, 248, 0.1);
      color: #38bdf8;
      border: 1px solid rgba(56, 189, 248, 0.3);
    }
    .btn-danger {
      background: rgba(239, 68, 68, 0.15);
      color: #f87171;
      border: 1px solid rgba(239, 68, 68, 0.3);
      padding: 6px 12px;
      font-size: 0.8rem;
    }
    .container {
      max-width: 1180px;
      margin: 24px auto;
      padding: 0 16px;
    }
    .stats-bar {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 14px;
      margin-bottom: 24px;
    }
    .stat-card {
      background: var(--card);
      border: 1px solid var(--card-border);
      border-radius: 12px;
      padding: 16px;
    }
    .stat-card .label { font-size: 0.8rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; }
    .stat-card .val { font-size: 1.5rem; font-weight: 700; color: #38bdf8; margin-top: 4px; }
    .tabs {
      display: flex;
      gap: 8px;
      overflow-x: auto;
      margin-bottom: 20px;
      padding-bottom: 4px;
    }
    .tab-btn {
      background: var(--card);
      color: var(--muted);
      border: 1px solid var(--card-border);
      padding: 12px 18px;
      border-radius: 10px;
      cursor: pointer;
      font-weight: 600;
      white-space: nowrap;
      font-size: 0.95rem;
    }
    .tab-btn.active {
      background: rgba(6, 182, 212, 0.15);
      color: #38bdf8;
      border-color: #38bdf8;
    }
    .panel { display: none; }
    .panel.active { display: block; }
    .card {
      background: var(--card);
      border: 1px solid var(--card-border);
      border-radius: 12px;
      padding: 20px;
      margin-bottom: 20px;
    }
    .card h2 {
      font-size: 1.15rem;
      margin-bottom: 6px;
      color: #e2e8f0;
    }
    .card p.sub {
      font-size: 0.88rem;
      color: var(--muted);
      margin-bottom: 16px;
    }
    .form-row {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-bottom: 14px;
    }
    input, select, textarea {
      background: #090d16;
      border: 1px solid #334155;
      color: var(--text);
      padding: 12px 14px;
      border-radius: 8px;
      font-size: 0.95rem;
      flex: 1;
      min-width: 210px;
    }
    input:focus, select:focus, textarea:focus {
      outline: none;
      border-color: #38bdf8;
    }
    pre.json-box {
      background: #060911;
      border: 1px solid #1e293b;
      padding: 14px;
      border-radius: 8px;
      overflow-x: auto;
      font-size: 0.85rem;
      color: #a5f3fc;
      max-height: 360px;
      margin-top: 12px;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      margin-top: 12px;
      font-size: 0.9rem;
    }
    th, td {
      text-align: left;
      padding: 12px 10px;
      border-bottom: 1px solid #1e293b;
    }
    th { color: var(--muted); font-weight: 600; font-size: 0.8rem; text-transform: uppercase; }
    .badge {
      padding: 3px 8px;
      border-radius: 6px;
      font-size: 0.75rem;
      font-weight: 600;
    }
    .badge-high { background: rgba(239,68,68,0.2); color: #f87171; }
    .badge-medium { background: rgba(245,158,11,0.2); color: #fbbf24; }
    .badge-low { background: rgba(16,185,129,0.2); color: #34d399; }
    .endpoint-item {
      background: #090d16;
      border: 1px solid #1e293b;
      padding: 12px 14px;
      border-radius: 8px;
      margin-bottom: 10px;
      font-family: monospace;
      font-size: 0.88rem;
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 8px;
    }
    .method-get { color: #34d399; font-weight: bold; }
    .method-post { color: #60a5fa; font-weight: bold; }
  </style>
</head>
<body>

  <header>
    <div class="brand">
      <h1>🛡️ OSINT & Breach Exposure API</h1>
      <p>Samsung Galaxy Tab A9+ 5G Friendly Dashboard + SQLite Cloud Database</p>
    </div>
    <div class="header-links">
      <a href="/docs" target="_blank" class="btn btn-outline">📄 Interactive API Docs (/docs)</a>
      <a href="/api/v1/health" target="_blank" class="btn btn-outline">⚡ Health JSON</a>
    </div>
  </header>

  <div class="container">
    <!-- Top Stats -->
    <div class="stats-bar">
      <div class="stat-card">
        <div class="label">API Status</div>
        <div class="val" id="stat-status" style="color:#10b981;">ONLINE</div>
      </div>
      <div class="stat-card">
        <div class="label">Custom DB Records</div>
        <div class="val" id="stat-records">-</div>
      </div>
      <div class="stat-card">
        <div class="label">Total Searches Logged</div>
        <div class="val" id="stat-logs">-</div>
      </div>
      <div class="stat-card">
        <div class="label">Database Engine</div>
        <div class="val" style="font-size:1.15rem; margin-top:8px;">SQLite3 + REST API</div>
      </div>
    </div>

    <!-- Navigation Tabs -->
    <div class="tabs">
      <button class="tab-btn active" onclick="switchTab('tab-db')">🗄️ 1. Database Search & Add</button>
      <button class="tab-btn" onclick="switchTab('tab-breach')">🔍 2. Email & Password Leak Check</button>
      <button class="tab-btn" onclick="switchTab('tab-recon')">🌐 3. IP & Domain OSINT</button>
      <button class="tab-btn" onclick="switchTab('tab-endpoints')">🔗 4. API Links (For Bots/Apps)</button>
    </div>

    <!-- TAB 1: CUSTOM DATABASE SEARCH & ADD -->
    <div id="tab-db" class="panel active">
      <div class="card">
        <h2>🔎 Search Your OSINT Database</h2>
        <p class="sub">Apne database mein email, username, domain ya breach source search karein.</p>
        <div class="form-row">
          <input type="text" id="db-search-input" placeholder="Search email, username, domain (e.g. test@example.com)..." />
          <select id="db-type-filter" style="max-width:180px;">
            <option value="">All Types</option>
            <option value="email">Email</option>
            <option value="username">Username</option>
            <option value="domain">Domain</option>
            <option value="ip">IP Address</option>
          </select>
          <button class="btn" onclick="searchDatabase()">Search DB</button>
        </div>
        <div style="overflow-x:auto;">
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>Indicator</th>
                <th>Type</th>
                <th>Source / Breach</th>
                <th>Exposed Data</th>
                <th>Severity</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody id="db-table-body">
              <tr><td colspan="7">Loading records...</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <div class="card">
        <h2>➕ Add New Record to Database</h2>
        <p class="sub">Yahan se aap bina coding ke seedha apne API Database mein naya record daal sakte hain.</p>
        <div class="form-row">
          <input type="text" id="add-indicator" placeholder="Indicator (e.g. user@domain.com or username)" />
          <select id="add-type" style="max-width:180px;">
            <option value="email">Email</option>
            <option value="username">Username</option>
            <option value="domain">Domain</option>
            <option value="ip">IP Address</option>
          </select>
          <input type="text" id="add-source" placeholder="Source / Breach Name (e.g. Audit-2026)" />
        </div>
        <div class="form-row">
          <input type="text" id="add-exposed" placeholder="Exposed Info (e.g. Email, Username, IP)" />
          <input type="text" id="add-date" placeholder="Date (e.g. 2026-03)" style="max-width:160px;" />
          <select id="add-severity" style="max-width:160px;">
            <option value="High">High</option>
            <option value="Critical">Critical</option>
            <option value="Medium" selected>Medium</option>
            <option value="Low">Low</option>
          </select>
        </div>
        <div class="form-row">
          <input type="text" id="add-notes" placeholder="Optional Notes..." />
          <button class="btn" onclick="addRecord()">💾 Save to Database</button>
        </div>
        <div id="add-msg" style="font-size:0.9rem; margin-top:8px;"></div>
      </div>
    </div>

    <!-- TAB 2: EMAIL & PASSWORD BREACH CHECK -->
    <div id="tab-breach" class="panel">
      <div class="card">
        <h2>📧 Email Breach Exposure Check (Local DB + Public OSINT)</h2>
        <p class="sub">Check karein ki koi email aapke local database ya public data breaches (XposedOrNot) mein aaya hai ya nahi.</p>
        <div class="form-row">
          <input type="email" id="breach-email-input" placeholder="Enter email (try test@example.com)..." value="test@example.com" />
          <button class="btn" onclick="checkEmailBreach()">Check Email Exposure</button>
        </div>
        <pre class="json-box" id="breach-email-output">Click "Check Email Exposure" to see live API JSON response...</pre>
      </div>

      <div class="card">
        <h2>🔐 Leaked Password Check (Safe k-Anonymity SHA-1 API)</h2>
        <p class="sub">Check karein ki koi password public leaks mein kitni baar expose hua hai (bina pura password bheje).</p>
        <div class="form-row">
          <input type="text" id="breach-pass-input" placeholder="Enter any password to test (e.g. password123)..." value="password123" />
          <button class="btn" onclick="checkPasswordLeak()">Check Password Leak</button>
        </div>
        <pre class="json-box" id="breach-pass-output">Click "Check Password Leak" to see live API JSON response...</pre>
      </div>
    </div>

    <!-- TAB 3: IP & DOMAIN OSINT -->
    <div id="tab-recon" class="panel">
      <div class="card">
        <h2>🌐 Domain / IP OSINT Reconnaissance</h2>
        <p class="sub">Kisi bhi domain ya IP ka location, ISP, ASN, hosting status aur local DB match check karein.</p>
        <div class="form-row">
          <input type="text" id="recon-target-input" placeholder="Enter domain or IP (e.g. example.com or 8.8.8.8)..." value="example.com" />
          <button class="btn" onclick="checkTargetRecon()">Run OSINT Lookup</button>
        </div>
        <pre class="json-box" id="recon-target-output">Click "Run OSINT Lookup" to see live API JSON response...</pre>
      </div>
    </div>

    <!-- TAB 4: READY API ENDPOINTS -->
    <div id="tab-endpoints" class="panel">
      <div class="card">
        <h2>🔗 Ready-to-Use API Endpoints</h2>
        <p class="sub">Jab aapka ye project Render/Cloud par live ho jayega, tab aap in URLs ko kisi bhi Telegram Bot, Website ya App mein use kar sakte hain:</p>

        <div class="endpoint-item">
          <span><span class="method-get">GET</span> /api/v1/breach/email?email=test@example.com</span>
          <a href="/api/v1/breach/email?email=test@example.com" target="_blank" class="btn btn-outline">Test Live</a>
        </div>

        <div class="endpoint-item">
          <span><span class="method-get">GET</span> /api/v1/breach/password?password=password123</span>
          <a href="/api/v1/breach/password?password=password123" target="_blank" class="btn btn-outline">Test Live</a>
        </div>

        <div class="endpoint-item">
          <span><span class="method-get">GET</span> /api/v1/osint/target?target=example.com</span>
          <a href="/api/v1/osint/target?target=example.com" target="_blank" class="btn btn-outline">Test Live</a>
        </div>

        <div class="endpoint-item">
          <span><span class="method-get">GET</span> /api/v1/db/search?q=example</span>
          <a href="/api/v1/db/search?q=example" target="_blank" class="btn btn-outline">Test Live</a>
        </div>

        <div class="endpoint-item">
          <span><span class="method-post">POST</span> /api/v1/db/records (Add JSON data to DB)</span>
          <a href="/docs#/Custom%20OSINT%20Database/add_database_record_api_v1_db_records_post" target="_blank" class="btn btn-outline">Open in /docs</a>
        </div>
      </div>
    </div>

  </div>

  <script>
    function switchTab(tabId) {
      document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.getElementById(tabId).classList.add('active');
      event.target.classList.add('active');
    }

    async function loadStats() {
      try {
        const res = await fetch('/api/v1/health');
        const data = await res.json();
        document.getElementById('stat-records').textContent = data.total_db_records;
        document.getElementById('stat-logs').textContent = data.total_searches_logged;
      } catch (e) {
        document.getElementById('stat-status').textContent = 'OFFLINE';
      }
    }

    async function searchDatabase() {
      const q = document.getElementById('db-search-input').value;
      const t = document.getElementById('db-type-filter').value;
      const tbody = document.getElementById('db-table-body');
      tbody.innerHTML = '<tr><td colspan="7">Searching...</td></tr>';
      try {
        const res = await fetch(`/api/v1/db/search?q=${encodeURIComponent(q)}&indicator_type=${encodeURIComponent(t)}`);
        const data = await res.json();
        if (!data.results || data.results.length === 0) {
          tbody.innerHTML = '<tr><td colspan="7" style="color:#94a3b8;">No matching records found.</td></tr>';
          return;
        }
        tbody.innerHTML = data.results.map(r => {
          const sevClass = (r.severity || '').toLowerCase().includes('high') || (r.severity || '').toLowerCase().includes('crit')
            ? 'badge-high'
            : ((r.severity || '').toLowerCase().includes('med') ? 'badge-medium' : 'badge-low');
          return `
            <tr>
              <td>#${r.id}</td>
              <td style="font-weight:600; color:#38bdf8;">${escapeHtml(r.indicator)}</td>
              <td>${escapeHtml(r.indicator_type)}</td>
              <td>${escapeHtml(r.source_name)} <span style="color:#64748b; font-size:0.8rem;">(${escapeHtml(r.breach_date)})</span></td>
              <td>${escapeHtml(r.exposed_data)}</td>
              <td><span class="badge ${sevClass}">${escapeHtml(r.severity)}</span></td>
              <td><button class="btn btn-danger" onclick="deleteRecord(${r.id})">Delete</button></td>
            </tr>
          `;
        }).join('');
        loadStats();
      } catch (e) {
        tbody.innerHTML = '<tr><td colspan="7" style="color:#ef4444;">Error loading database.</td></tr>';
      }
    }

    async function addRecord() {
      const indicator = document.getElementById('add-indicator').value.trim();
      const indicator_type = document.getElementById('add-type').value;
      const source_name = document.getElementById('add-source').value.trim() || 'Manual-Entry';
      const exposed_data = document.getElementById('add-exposed').value.trim() || 'Metadata';
      const breach_date = document.getElementById('add-date').value.trim() || '2026';
      const severity = document.getElementById('add-severity').value;
      const notes = document.getElementById('add-notes').value.trim();
      const msg = document.getElementById('add-msg');

      if (!indicator) {
        msg.style.color = '#f87171';
        msg.textContent = '⚠️ Kripya Indicator (Email/Username/Domain) bharein!';
        return;
      }

      const res = await fetch('/api/v1/db/records', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ indicator, indicator_type, source_name, breach_date, exposed_data, severity, notes })
      });
      if (res.ok) {
        msg.style.color = '#34d399';
        msg.textContent = '✅ Record successfully database mein save ho gaya!';
        document.getElementById('add-indicator').value = '';
        searchDatabase();
      } else {
        msg.style.color = '#f87171';
        msg.textContent = '❌ Save karne mein error aaya.';
      }
    }

    async function deleteRecord(id) {
      await fetch(`/api/v1/db/records/${id}`, { method: 'DELETE' });
      searchDatabase();
    }

    async function checkEmailBreach() {
      const email = document.getElementById('breach-email-input').value.trim();
      const out = document.getElementById('breach-email-output');
      out.textContent = 'Checking local database & public breach intelligence...';
      const res = await fetch(`/api/v1/breach/email?email=${encodeURIComponent(email)}`);
      const data = await res.json();
      out.textContent = JSON.stringify(data, null, 2);
      loadStats();
    }

    async function checkPasswordLeak() {
      const pass = document.getElementById('breach-pass-input').value;
      const out = document.getElementById('breach-pass-output');
      out.textContent = 'Checking k-Anonymity SHA-1 range API...';
      const res = await fetch(`/api/v1/breach/password?password=${encodeURIComponent(pass)}`);
      const data = await res.json();
      out.textContent = JSON.stringify(data, null, 2);
      loadStats();
    }

    async function checkTargetRecon() {
      const target = document.getElementById('recon-target-input').value.trim();
      const out = document.getElementById('recon-target-output');
      out.textContent = 'Running DNS + IP Geolocation + Local DB OSINT...';
      const res = await fetch(`/api/v1/osint/target?target=${encodeURIComponent(target)}`);
      const data = await res.json();
      out.textContent = JSON.stringify(data, null, 2);
      loadStats();
    }

    function escapeHtml(str) {
      if (!str) return '';
      return String(str).replace(/[&<>"']/g, m => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
      }[m]));
    }

    // Initial load
    loadStats();
    searchDatabase();
  </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def dashboard_home():
    return HTMLResponse(content=DASHBOARD_HTML)
