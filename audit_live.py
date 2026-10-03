#!/usr/bin/env python3
"""Deep live-audit script for this hub.

Usage:  python3 audit_live.py [ADMIN_PASSWORD]
Admin password optional hai (khali chhodo to sirf public check hoga).
Koi secret file me save nahi hota.
"""
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import requests

BASE = os.environ.get("HUB_BASE", "https://osint-api-hub.onrender.com")
KEY = "Demo"
ADMIN_PASS = sys.argv[1] if len(sys.argv) > 1 else ""
S = requests.Session()
S.headers["User-Agent"] = "hub-audit/1.0"


def get(path, **params):
    t = time.time()
    try:
        r = S.get(BASE + path, params=params, timeout=60)
        ms = int((time.time() - t) * 1000)
        try:
            j = r.json()
        except Exception:
            j = r.text[:200]
        return r.status_code, ms, j
    except Exception as e:
        return -1, int((time.time() - t) * 1000), {"error": str(e)[:120]}


print("=== 1) hub zinda hai? (cold start ho sakta hai)")
sc, ms, j = get("/health")
print(f"  /health → {sc} in {ms}ms | {json.dumps(j)[:160] if isinstance(j, dict) else j}")

print("\n=== 2) master endpoint list")
sc, ms, eps = get("/api/endpoints")
rows = []
if isinstance(eps, dict):
    eps = eps.get("endpoints") or []
print(f"  /api/endpoints → {sc}, {len(eps)} endpoints")

print("\n=== 3) har endpoint live test (key=Demo)")


SAMPLES = {}  # optional: {path: [[param, sample], ...]}; khali ho to /api/endpoints ke example URL se le lete hain


def probe(ep):
    path = str(ep.get("path", "")).replace("/api/", "")
    params = {"key": KEY}
    if SAMPLES.get(path):
        for nm, smp in SAMPLES[path]:
            if nm and smp:
                params[nm] = smp
    else:
        # endpoint ke "example" URL se params nikaal lo
        ex = str(ep.get("example") or "")
        if "?" in ex:
            import urllib.parse as _up
            for k, v in _up.parse_qsl(ex.split("?", 1)[1]):
                if k != "key":
                    params[k] = v
    # disabled/privacy endpoints ka sample safe rakho
    if path in ("num-info", "number-info", "num", "leak-v1", "leak-v2", "family",
                "num-family", "email-info", "email", "aadhaar-family", "aadhaar", "ration"):
        params = {"key": KEY, "q": "9000000000"}
    full = path if str(path).startswith("/") else f"/api/{path}"
    sc, ms, j = get(full, **params)
    err = ""
    src = ""
    if isinstance(j, dict):
        err = str(j.get("error") or j.get("detail") or j.get("hint") or "")[:100]
        src = str(j.get("source") or (j.get("_meta") or {}).get("source") or "")
    return path, sc, ms, src, err


with ThreadPoolExecutor(max_workers=8) as ex:
    results = list(ex.map(probe, eps))

okc = badc = 0
for path, sc, ms, src, err in sorted(results, key=lambda x: (x[1] < 0, x[1])):
    flag = "✅" if sc == 200 else ("⛔" if sc == 410 else ("🔑" if sc == 401 else "❌"))
    if sc == 200:
        okc += 1
    else:
        badc += 1
    print(f"  {flag} {path:24s} {sc:>4} {ms:>6}ms  {src:16s} {err}")
print(f"\n  TOTAL {len(results)} | 200: {okc} | problem: {badc}")

print("\n=== 4) admin panel (password diya gaya)")
if ADMIN_PASS:
    t = time.time()
    r = S.post(BASE + "/admin/login", json={"password": ADMIN_PASS}, timeout=60)
    print(f"  /admin/login → {r.status_code} {r.text[:80]} in {int((time.time()-t)*1000)}ms")
    if r.status_code == 200 and r.json().get("success"):
        H = {"x-admin-token": ADMIN_PASS}
        for path in ("/admin/overview", "/admin/settings", "/admin/keys", "/admin/records",
                     "/admin/orders", "/admin/resellers", "/admin/logs"):
            try:
                rr = S.get(BASE + path, headers=H, timeout=60)
                body = rr.json()
                if isinstance(body, dict):
                    keys = list(body.keys())[:8]
                    note = ""
                    if path == "/admin/settings":
                        st = body.get("settings", {})
                        note = json.dumps({k: st.get(k) for k in
                                           ("upstream_base", "upstream_key", "upstream_enabled",
                                            "demo_key_enabled", "cache_enabled", "brand_tag")})[:200]
                    if path == "/admin/records":
                        note = f"records={body.get('count') or len(body.get('records') or [])}"
                    if path == "/admin/keys":
                        ks = body.get("keys") or []
                        note = f"{len(ks)} keys: " + ", ".join(
                            f"{k.get('key','')[:10]}…plan={k.get('allowed_endpoints')}" for k in ks[:5])
                    print(f"  {path} → {rr.status_code} keys={keys} {note}")
                else:
                    print(f"  {path} → {rr.status_code} {str(body)[:120]}")
            except Exception as e:
                print(f"  {path} → ERR {str(e)[:100]}")
else:
    print("  (password nahi diya)")

print("\n=== 5) baaki public routes")
for path in ("/", "/site", "/store", "/dashboard", "/api/plans", "/api/v1/health",
             "/api/v1/search", "/api/key-info", "/openapi.json"):
    sc, ms, j = get(path, key=KEY)
    kind = ""
    if isinstance(j, dict):
        kind = str({k: j[k] for k in list(j)[:4]})[:110]
    else:
        kind = str(j)[:110].replace("\n", " ")
    print(f"  {path:18s} {sc:>4} {ms:>6}ms  {kind}")
