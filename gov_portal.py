"""v2.8.4 — Government portal reachability + whitelisted session fetch.

Kyun: VAHAN / e-Challan / Sarathi / IIB jaise official portals server IP (datacenter /
foreign) ko block karte hain. Ye module batata hai ki hub (Render se chal raha hai)
un tak pahunch pa raha hai ya nahi — aur pahunch raha ho to unke saath SAFE session
banata hai, taaki aage captcha-relay flow ban sake.

SECURITY: sirf GOV_ALLOWED_HOSTS wale domains hi hit ho sakte hain (koi SSRF nahi).
"""
from __future__ import annotations

import base64
import time
import uuid
from typing import Any, Dict, Optional

import httpx

UA = ("Mozilla/5.0 (Linux; Android 13; SM-M135FU) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Mobile Safari/537.36")

# Ye 4 official portals — inke alawa koi host allowed nahi
GOV_SERVICES: Dict[str, Dict[str, str]] = {
    "echallan": {
        "name": "e-Challan Status",
        "host": "echallan.parivahan.gov.in",
        "url": "https://echallan.parivahan.gov.in/index/accused-challan",
        "use": "vehicle number -> pending challans",
    },
    "vahan": {
        "name": "VAHAN RC Details",
        "host": "vahan.parivahan.gov.in",
        "url": "https://vahan.parivahan.gov.in/nrservices/faces/user/searchstatus.xhtml",
        "use": "vehicle number -> RC / owner / insurance details",
    },
    "sarathi": {
        "name": "Sarathi DL Status",
        "host": "sarathi.parivahan.gov.in",
        "url": "https://sarathi.parivahan.gov.in/sarathiservice/stateSelection.do",
        "use": "DL number + DOB -> licence details",
    },
    "iib": {
        "name": "IIB Insurance Policy",
        "host": "www.iib.gov.in",
        "url": "https://www.iib.gov.in/web/IIB/KnowYourPolicy",
        "use": "vehicle number -> insurance company / policy expiry",
    },
}
GOV_ALLOWED_HOSTS = {s["host"] for s in GOV_SERVICES.values()} | {
    "vahanx.parivahan.gov.in", "sarathi.parivahan.gov.in", "iib.gov.in",
    "echallan.parivahan.gov.in", "vahan.parivahan.gov.in",
}

# ---------------------------------------------------------------- sessions
_SESSIONS: Dict[str, Dict[str, Any]] = {}
_SESSION_TTL = 600.0          # 10 min me captcha bharo
MAX_TEXT = 400_000            # itna hi text wapas (safety)


def _cleanup() -> None:
    now = time.time()
    dead = [k for k, v in _SESSIONS.items() if now - v["born"] > _SESSION_TTL]
    for k in dead:
        sess = _SESSIONS.pop(k, None)
        if sess:
            try:
                sess["client"].close()
            except Exception:
                pass


def _url_ok(url: str) -> bool:
    if not url:
        return False
    return url.split("/")[2].split(":")[0].lower() in GOV_ALLOWED_HOSTS


async def open_session(service: str) -> Dict[str, Any]:
    """Portal ka landing page kholta hai; cookies session me save rehti hain."""
    svc = GOV_SERVICES.get(service)
    if not svc:
        return {"ok": False, "error": f"unknown service: {service}",
                "services": list(GOV_SERVICES)}
    _cleanup()
    client = httpx.AsyncClient(follow_redirects=True, timeout=30.0,
                               headers={"User-Agent": UA,
                                        "Accept-Language": "en-IN,en;q=0.9"})
    sid = uuid.uuid4().hex[:12]
    t0 = time.time()
    try:
        r = await client.get(svc["url"])
        _SESSIONS[sid] = {"client": client, "service": service, "born": time.time(),
                          "url": str(r.url)}
        return {"ok": True, "session": sid, "service": service, "name": svc["name"],
                "status": r.status_code, "final_url": str(r.url),
                "chars": len(r.text), "ms": int((time.time() - t0) * 1000),
                "blocked": r.status_code in (403, 451)}
    except Exception as e:
        try:
            await client.aclose()
        except Exception:
            pass
        return {"ok": False, "session": None, "service": service,
                "error": f"{type(e).__name__}: {e}",
                "ms": int((time.time() - t0) * 1000)}


async def session_fetch(session: str, path: str = "", method: str = "GET",
                        data: Optional[Dict[str, Any]] = None,
                        want: str = "text") -> Dict[str, Any]:
    """Usi session (cookies) me koi bhi path hit karo — sirf allowed host par, aur
    relative path us host ke hisaab se resolve hota hai."""
    _cleanup()
    sess = _SESSIONS.get(session)
    if not sess:
        return {"ok": False, "error": "session nahi mila / expire ho gaya (10 min)"}
    svc = GOV_SERVICES[sess["service"]]
    url = path if path.startswith("http") else svc["url"].rsplit("/", 1)[0] + "/" + path.lstrip("/")
    if not _url_ok(url):
        return {"ok": False, "error": f"host allowed nahi hai (sirf {sorted(GOV_ALLOWED_HOSTS)})"}
    try:
        if method.upper() == "POST":
            r = await sess["client"].post(url, data=data or {})
        else:
            r = await sess["client"].get(url, params=data or None)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    out: Dict[str, Any] = {"ok": True, "status": r.status_code, "final_url": str(r.url),
                           "content_type": r.headers.get("content-type", ""),
                           "len": len(r.content)}
    ctype = (r.headers.get("content-type") or "").lower()
    if want == "b64" or not ctype.startswith("text"):
        out["b64"] = base64.b64encode(r.content).decode()
    else:
        out["text"] = r.text[:MAX_TEXT]
    # session me naye cookies ban jayein to wahi client use karta rahega
    out["cookies"] = {k: v for k, v in sess["client"].cookies.items()}
    return out


async def close_session(session: str) -> Dict[str, Any]:
    sess = _SESSIONS.pop(session, None)
    if sess:
        try:
            await sess["client"].aclose()
        except Exception:
            pass
    return {"ok": bool(sess)}


def session_info() -> Dict[str, Any]:
    _cleanup()
    return {"open_sessions": len(_SESSIONS),
            "sessions": {k: {"service": v["service"],
                             "age_s": int(time.time() - v["born"])} for k, v in _SESSIONS.items()}}


# ---------------------------------------------------------------- probe
async def probe_service(service: str, timeout: float = 15.0) -> Dict[str, Any]:
    svc = GOV_SERVICES.get(service)
    if not svc:
        return {"ok": False, "error": "unknown service"}
    t0 = time.time()
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout,
                                     headers={"User-Agent": UA}) as c:
            r = await c.get(svc["url"])
        return {"service": service, "name": svc["name"], "reachable": True,
                "status": r.status_code, "chars": len(r.text),
                "blocked": r.status_code in (403, 451),
                "ms": int((time.time() - t0) * 1000)}
    except Exception as e:
        return {"service": service, "name": svc["name"], "reachable": False,
                "error": f"{type(e).__name__}: {e}",
                "ms": int((time.time() - t0) * 1000)}


async def probe_all() -> Dict[str, Any]:
    import asyncio
    results = await asyncio.gather(*[probe_service(s) for s in GOV_SERVICES])
    return {"ok": True, "checked_at": time.strftime("%d-%m-%Y %H:%M:%S"),
            "results": list(results),
            "reachable": [r["service"] for r in results if r.get("reachable")],
            "note": ("reachable=true ka matlab hub ke server se portal khul raha hai; "
                     "uske baad captcha-relay flow ban sakta hai.")}
