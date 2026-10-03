#!/usr/bin/env python3
"""
PRE-PUSH GUARD — ye script har baar push se pehle chalana.

Pichli baar (commit 40d219e) ek function galti se delete ho gaya tha aur
Render par deploy FAILED ho gaya (NameError: native_aadhaar_family).
Isliye ab ye check karta hai ki:

  1. main.py bina error ke import ho jaye
  2. endpoint catalog me likha har "native=..." function sach me maujood ho
  3. har endpoint ka path duplicate to nahi
  4. har native function async ho aur 2 argument le (params, request)

Chalane ka tarika:
    python3 precheck.py
Exit code 0 = sab theek (push kar do), 1 = matlab koi problem hai.
"""
from __future__ import annotations

import inspect
import sys

FAILS: list[str] = []
OKS: list[str] = []


def ok(msg: str) -> None:
    OKS.append(msg)
    print(f"✅ {msg}")


def bad(msg: str) -> None:
    FAILS.append(msg)
    print(f"❌ {msg}")


def main() -> int:
    print("=" * 78)
    print("OSINT HUB — PRE-PUSH GUARD")
    print("=" * 78)

    # ---------- 1) import ----------
    try:
        import main                                              # noqa: E402
    except Exception as e:                                       # noqa: BLE001
        bad(f"main.py import FAIL: {type(e).__name__}: {e}")
        return 1
    ok("main.py import ho gaya (koi NameError / SyntaxError nahi)")

    # ---------- 2) catalog ----------
    catalog = (getattr(main, "ENDPOINT_CATALOG", None) or getattr(main, "ENDPOINTS", None)
               or getattr(main, "CATALOG", None))
    if catalog is None:
        bad("ENDPOINT_CATALOG / ENDPOINTS nahi mila — kya naam badal gaya?")
        return 1
    ok(f"endpoint catalog mila — {len(catalog)} endpoints")

    # ---------- 3) har native function maujood + sahi signature ----------
    seen_paths: dict[str, int] = {}
    native_checked = 0
    registry = getattr(main, "NATIVE_FUNCS", None) or {}
    if not registry:
        bad("NATIVE_FUNCS registry nahi mila")
    else:
        ok(f"NATIVE_FUNCS registry mila — {len(registry)} handlers")
        broken = [k for k, v in registry.items() if not callable(v)]
        if broken:
            bad(f"NATIVE_FUNCS me toote hue references: {broken}")
        else:
            ok("NATIVE_FUNCS ke saare references ka function maujood hai")

    for idx, ep in enumerate(catalog):
        path = ep.get("path") or f"(no path #{idx})"
        seen_paths[path] = seen_paths.get(path, 0) + 1

        name = ep.get("native")
        if not name:
            continue
        # catalog ka "native" field NATIVE_FUNCS ki key hai
        fn = registry.get(name)
        if fn is None:
            bad(f"{path}: NATIVE_FUNCS['{name}'] MAUJOOD NAHI (delete ho gaya?)")
            continue
        if not callable(fn):
            bad(f"{path}: NATIVE_FUNCS['{name}'] callable nahi hai")
            continue
        if not inspect.iscoroutinefunction(fn):
            bad(f"{path}: '{name}' async (await wala) nahi hai")
            continue
        sig = inspect.signature(fn)
        if len(sig.parameters) < 2:
            bad(f"{path}: '{name}' ke sirf {len(sig.parameters)} param hain, 2 hone chahiye")
            continue
        native_checked += 1
    if native_checked:
        ok(f"{native_checked} endpoints ka native handler sab maujood hai")

    # ---------- 4) duplicate paths ----------
    dups = {p: c for p, c in seen_paths.items() if c > 1}
    if dups:
        bad(f"duplicate endpoint paths: {dups}")
    else:
        ok("koi duplicate endpoint path nahi")

    # ---------- 6) zaroori helpers ----------
    for helper in ("format_aadhaar_card", "_looks_like_person_name", "_no_name_label",
                   "native_aadhaar_family", "native_num_info_full", "native_vehicle_report"):
        if not hasattr(main, helper):
            bad(f"zaroori helper/function '{helper}' MAUJOOD NAHI")
    ok("saare zaroori helpers maujood hain")

    # ---------- 7) aadhaar card render test ----------
    try:
        card = main.format_aadhaar_card({
            "aadhaar_masked": "XXXXXXXX8408", "aadhaar_valid_checksum": True,
            "ration_card_number": "NA", "fps_id": "NA",
            "members": [{"name": "Test User", "aadhaar_masked": "XXXXXXXX8408", "is_head": True}],
            "location": {"city": "SITAMARHI", "state": "BIHAR", "pincode": "843302"},
            "response_time": "1.0s",
        })
        must = ["FAMILY MEMBERS (1)", "Ration Card Number", "FPS ID", "LOCATION DETAILS",
                "SITAMARHI (BIHAR)", "@Supermannn_x", "👑 (Head)"]
        missing = [m for m in must if m not in card]
        if missing:
            bad(f"aadhaar card me ye nahi mila: {missing}")
        else:
            ok("aadhaar card format sahi hai (members / ration / FPS / location / brand)")
    except Exception as e:                                       # noqa: BLE001
        bad(f"format_aadhaar_card FAIL: {type(e).__name__}: {e}")

    # ---------- result ----------
    print("=" * 78)
    if FAILS:
        print(f"RESULT — PASS: {len(OKS)} | FAIL: {len(FAILS)}  ⛔ PUSH MAT KARO")
        for f in FAILS:
            print("   ❌", f)
        print("=" * 78)
        return 1
    print(f"RESULT — PASS: {len(OKS)} | FAIL: 0  ✅ PUSH KAR SAKTE HO")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
