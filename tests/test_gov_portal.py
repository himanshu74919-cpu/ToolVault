"""v2.8.4 — gov portal module ke safety guards (network ke bina)."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gov_portal as gp  # noqa: E402


def test_services_complete():
    assert set(gp.GOV_SERVICES) == {"echallan", "vahan", "sarathi", "iib"}
    for s in gp.GOV_SERVICES.values():
        assert s["url"].startswith("https://")


def test_no_ssrf_other_hosts():
    assert gp._url_ok("https://vahan.parivahan.gov.in/nrservices/x.xhtml")
    assert gp._url_ok("https://www.iib.gov.in/web/IIB/KnowYourPolicy")
    # khatarnak hosts reject hone chahiye
    assert not gp._url_ok("http://localhost:8000/admin")
    assert not gp._url_ok("https://evil.com/steal")
    assert not gp._url_ok("https://127.0.0.1/x")
    assert not gp._url_ok("")


def test_fetch_rejects_bad_session_and_host():
    r = asyncio.run(gp.session_fetch("nope", "x"))
    assert r["ok"] is False
    r2 = asyncio.run(gp.session_fetch("nope", "https://evil.com/a"))
    assert r2["ok"] is False
