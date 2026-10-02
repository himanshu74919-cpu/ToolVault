"""
🤖 READY-MADE TELEGRAM BOT — apne OSINT API Hub ke liye
========================================================
Kaise chalayein:
1. Python 3 install ho, fir:  pip install requests
2. Is file me niche 3 cheezein bharein:
      BOT_TOKEN   = Telegram @BotFather se mila token
      API_BASE    = apna Render URL  (https://osint-api-hub-xxxx.onrender.com)
      API_KEY     = apni API key (Demo ya customer wali)
3. Chalu karein:  python telegram_bot.py
4. Telegram me apne bot ko /start bhejein.

Commands:
  /num 919973700984        -> number report card
  /vehicle BR30AR0802      -> RC + challan report
  /family 919973700984     -> linked / family numbers
  /email test@gmail.com    -> email OSINT
  /pass Katihar@123        -> password breach check
  /key                     -> apni key ki expiry/plan
"""

import time

import requests

# ============ YAHAN APNA DATA BHAREIN ============
BOT_TOKEN = "1234567890:AAFxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
API_BASE = "https://osint-api-hub-xxxx.onrender.com"   # bina last slash ke
API_KEY = "Demo"
# =================================================

HELP_TEXT = """🤖 *OSINT API Bot*

Commands:
/num <number> — Number report (naam, father, address, ID)
/vehicle <plate> — RC + insurance + challan report
/family <number> — Family / linked numbers
/email <email> — Email OSINT + leak records
/pass <password> — Password breach check
/key — Apni API key ki expiry/plan

Example: `/num 919973700984`"""


def api_get(path: str, **params) -> str:
    """Call the hub with format=text so we can paste the card straight into Telegram."""
    params["key"] = API_KEY
    params["format"] = "text"
    url = f"{API_BASE}/api/{path}"
    try:
        r = requests.get(url, params=params, timeout=90)
        if r.status_code == 403:
            try:
                data = r.json()
                return (f"🔒 {data.get('status', 'blocked').upper()}\n{data.get('error', '')}\n"
                        f"{data.get('hint', '')}")
            except Exception:
                return f"🔒 Blocked (HTTP 403). Key expired ya plan me nahi hai."
        if r.status_code != 200:
            return f"⚠️ API error: HTTP {r.status_code}\n{r.text[:300]}"
        return r.text.strip() or "❌ Empty response"
    except Exception as exc:
        return f"⚠️ Request failed: {exc}"


def tg(method: str, **payload):
    return requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
                         json=payload, timeout=60).json()


def send(chat_id: int, text: str):
    if len(text) > 4000:
        text = text[:4000] + "\n…(truncated)"
    tg("sendMessage", chat_id=chat_id, text=text, parse_mode="Markdown",
       disable_web_page_preview=True)


def handle(text: str) -> str:
    parts = text.strip().split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd in ("/start", "/help"):
        return HELP_TEXT
    if cmd == "/num":
        if not arg:
            return "Usage: `/num 919973700984`"
        return api_get("num-info", q=arg)
    if cmd == "/vehicle":
        if not arg:
            return "Usage: `/vehicle BR30AR0802`"
        return api_get("vehicle-report", number=arg)
    if cmd == "/family":
        if not arg:
            return "Usage: `/family 919973700984`"
        return api_get("family", q=arg)
    if cmd == "/email":
        if not arg:
            return "Usage: `/email test@gmail.com`"
        return api_get("email-info", email=arg)
    if cmd == "/pass":
        if not arg:
            return "Usage: `/pass mypassword123`"
        return api_get("pass-check", password=arg)
    if cmd == "/key":
        try:
            r = requests.get(f"{API_BASE}/api/key-info", params={"key": API_KEY}, timeout=30)
            d = r.json()
            return ("🔑 *Key status*\n"
                    f"Plan: `{d.get('plan')}`\n"
                    f"Expiry: `{d.get('expires_at_ist')}`\n"
                    f"Days left: `{d.get('days_left')}`\n"
                    f"Requests used: `{d.get('requests_used')}`\n"
                    f"Device lock: `{d.get('device_lock')}`")
        except Exception as exc:
            return f"⚠️ {exc}"
    return "❓ Unknown command. /help dekho."


def main():
    print("Bot started. Telegram me /start bhejein...")
    offset = None
    while True:
        try:
            updates = tg("getUpdates", offset=offset, timeout=30).get("result", [])
            for upd in updates:
                offset = upd["update_id"] + 1
                msg = upd.get("message") or upd.get("edited_message") or {}
                chat_id = (msg.get("chat") or {}).get("id")
                text = msg.get("text") or ""
                if not chat_id or not text:
                    continue
                print(f"<- {text}")
                send(chat_id, handle(text))
        except Exception as exc:
            print("error:", exc)
            time.sleep(3)


if __name__ == "__main__":
    main()
