"""
🤖 ADVANCED TELEGRAM BOT — inline buttons + subscription check + UPI payment
===========================================================================
Ye bot aapke OSINT API Hub ko ek "readymade product" ki tarah bechta hai.

Features:
  ✅ Inline keyboard menu (buttons se sab kuch)
  ✅ /status  → subscription check (plan, expiry, kitne din bache, usage)
  ✅ /buy     → plans inline buttons → UPI order → QR → "I have paid" → auto key
  ✅ /num /vehicle /aadhaar /family /email /pass /yt /ytmp3  (button + command dono)
  🔒 Force subscribe (optional) — channel join ke bina bot kaam na kare
  🤝 Reseller mode (optional) — dealer apna token dal kar customer ko key de sake

Chalane ka tarika:
  1) pip install requests
  2) niche CONFIG me BOT_TOKEN, API_BASE, API_KEY bharein
  3) python telegram_bot_advanced.py

Optional settings:
  FORCE_CHANNEL = "@yourchannel"   # sab users ko is channel join karna padega
  UPI_ID        = "aapka@okhdfcbank"
  SUPPORT       = "@Supermannn_x"
"""

import time

import requests

# ================= CONFIG (yahin bharna hai) =================
BOT_TOKEN = "1234567890:AAFxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"      # @BotFather se
API_BASE = "https://osint-api-hub-xxxx.onrender.com"            # bina last slash ke
API_KEY = "Demo"                                                # apni key

UPI_ID = "aapka@okhdfcbank"        # payment yahan aayega
SUPPORT = "@Supermannn_x"          # support / developer
FORCE_CHANNEL = ""                 # example: "@osintapihub"  (khali = force subscribe off)
ADMIN_ID = 0                       # apna numeric telegram id (optional, sirf broadcast ke liye)
# ==============================================================

BRAND = "@Supermannn_x"
API = f"https://api.telegram.org/bot{BOT_TOKEN}"


# ---------------------------------------------------------------- telegram helpers
def tg(method, **payload):
    try:
        r = requests.post(f"{API}/{method}", json=payload, timeout=60)
        return r.json()
    except Exception as e:                                   # noqa: BLE001
        print("telegram error:", e)
        return {"ok": False}


def send(chat, text, reply_markup=None, parse_mode="Markdown"):
    payload = {"chat_id": chat, "text": text[:4000], "parse_mode": parse_mode,
               "disable_web_page_preview": True}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return tg("sendMessage", **payload)


def edit(chat, msg_id, text, reply_markup=None):
    payload = {"chat_id": chat, "message_id": msg_id, "text": text[:4000],
               "parse_mode": "Markdown", "disable_web_page_preview": True}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return tg("editMessageText", **payload)


def answer(cb_id, text=None, alert=False):
    tg("answerCallbackQuery", callback_query_id=cb_id, text=text or "", show_alert=alert)


def kb(rows):
    """rows = [[("label","callback_data"), ...], ...]"""
    return {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in rows]}


# ---------------------------------------------------------------- keyboards
MENU = kb([
    [("🔍 Number Report", "m:num"), ("🚗 Vehicle Report", "m:vehicle")],
    [("🆔 Aadhaar Family", "m:aadhaar"), ("👨‍👩‍👧 Linked Numbers", "m:family")],
    [("📧 Email OSINT", "m:email"), ("🔐 Password Check", "m:pass")],
    [("▶️ YouTube Download", "m:yt"), ("🎵 YouTube MP3", "m:ytmp3")],
    [("💎 Buy API Key", "m:buy"), ("📊 My Subscription", "m:status")],
    [("💬 Support", "m:support")],
])

PLAN_KB_CACHE = None


def plan_keyboard():
    global PLAN_KB_CACHE
    if PLAN_KB_CACHE:
        return PLAN_KB_CACHE
    rows = []
    try:
        plans = requests.get(f"{API_BASE}/api/plans", timeout=30).json().get("plans", [])
        for p in plans:
            rows.append([(f"{p['name']} — ₹{int(p['price'])} ({p['days']} din)", f"plan:{p['id']}")])
    except Exception:                                        # noqa: BLE001
        rows = [[("Number Pack — ₹100 (30 din)", "plan:num")],
                [("Vehicle Pack — ₹100 (30 din)", "plan:vehicle")],
                [("Full Pack — ₹299 (30 din)", "plan:full")]]
    rows.append([("🔄 Check Payment", "paid:"), ("❌ Cancel", "cancel")])
    PLAN_KB_CACHE = kb(rows)
    return PLAN_KB_CACHE


# ---------------------------------------------------------------- subscription check
def subscription_text(key=None):
    key = key or API_KEY
    try:
        r = requests.get(f"{API_BASE}/api/key-info", params={"key": key}, timeout=30)
        d = r.json()
    except Exception as e:                                   # noqa: BLE001
        return f"⚠️ Hub se connect nahi ho pa raha: {e}"
    if r.status_code != 200 or not d.get("success", True):
        return "❌ Key invalid ya expired. /buy se naya key le lo."

    exp = d.get("expires_at_ist") or d.get("expires_at") or "Never (lifetime)"
    days = d.get("days_left")
    dl = "" if days is None else f" ({days} din bache)"
    eps = d.get("allowed_endpoints") or []
    if isinstance(eps, list):
        plan = ", ".join(str(e) for e in eps) or "*"
    else:
        plan = str(eps)
    plan = plan.replace("ALL", "ALL 59 endpoints")
    plan_name = d.get("plan") or "Custom"
    if isinstance(plan_name, list):
        plan_name = ", ".join(str(x) for x in plan_name)
    status = "✅ Active" if d.get("status") == "active" else f"🔒 {d.get('status')}"
    return (
        "📊 *Subscription Status*\n"
        f"🔑 Key: `{key[:12]}...`\n"
        f"👤 Customer: {d.get('customer') or '-'}\n"
        f"📦 Plan: {plan_name}\n"
        f"📅 Expiry: {exp}{dl}\n"
        f"🔓 Endpoints: {plan}\n"
        f"📈 Usage: {d.get('requests_used', 0)} requests\n"
        f"📱 Device: {d.get('devices_bound', 0)}/{d.get('max_devices', 1)}\n"
        f"🚦 Status: {status}\n\n"
        f"_Powered by {BRAND}_"
    )


def is_subscribed(user_id):
    """Force-subscribe check. FORCE_CHANNEL khali hai to hamesha True."""
    if not FORCE_CHANNEL:
        return True
    try:
        r = requests.get(f"{API}/getChatMember",
                         params={"chat_id": FORCE_CHANNEL, "user_id": user_id}, timeout=30).json()
        return r.get("result", {}).get("status") in ("member", "administrator", "creator")
    except Exception:                                        # noqa: BLE001
        return True


def join_gate(chat):
    send(chat,
         "🔔 *Pehle channel join karein*\n\n"
         f"Bot use karne ke liye {FORCE_CHANNEL} join karna zaroori hai.\n"
         "Join karke niche wale button se verify karein.",
         kb([[("📢 Join Channel", "url")], [("✅ Verify", "verify")]]))


# ---------------------------------------------------------------- hub calls
def api_get(path, **params):
    params["key"] = API_KEY
    params["format"] = "text"
    try:
        r = requests.get(f"{API_BASE}/api/{path}", params=params, timeout=90)
        if r.status_code == 403:
            d = r.json()
            return (f"🔒 {str(d.get('status', 'blocked')).upper()}\n{d.get('error','')}\n\n"
                    f"Naya key lene ke liye /buy — {BRAND}")
        if r.status_code == 429:
            return "⏳ Rate limit ho gaya, thodi der baad try karein."
        return r.text if r.text.strip() else "⚠️ Kuch nahi mila."
    except Exception as e:                                   # noqa: BLE001
        return f"⚠️ Error: {e}"


def create_order(plan_id, name, phone=""):
    try:
        r = requests.post(f"{API_BASE}/api/create-order", timeout=60,
                          json={"plan": plan_id, "name": name, "phone": phone})
        return r.json()
    except Exception as e:                                   # noqa: BLE001
        return {"success": False, "error": str(e)}


def order_status(code):
    try:
        return requests.get(f"{API_BASE}/api/order-status",
                            params={"code": code}, timeout=60).json()
    except Exception as e:                                   # noqa: BLE001
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------- pending input
# user_id -> action jiska jawab hum wait kar rahe hain
WAITING = {}
# user_id -> last order code (payment check ke liye)
LAST_ORDER = {}


# ---------------------------------------------------------------- handlers
def cmd_start(chat, user, name):
    send(chat,
         f"👋 Namaste *{name}*!\n\n"
         "Yeh bot aapko deta hai:\n"
         "• 📱 Number / Aadhaar / Vehicle reports\n"
         "• 👨‍👩‍👧 Family & linked numbers\n"
         "• ▶️ YouTube direct download\n"
         "• 💎 API key kharidna (UPI)\n\n"
         "Niche buttons use karein 👇\n\n"
         f"_Powered by {BRAND}_",
         MENU)


def cmd_status(chat):
    send(chat, subscription_text())


def cmd_buy(chat):
    send(chat, "💎 *Plans chunein*\n\nPayment UPI se hoga, payment milte hi "
               "**API key turant** mil jayegi (auto-activation).\n\n"
               f"_Powered by {BRAND}_", plan_keyboard())


def on_plan(chat, msg_id, user, plan_id, name):
    o = create_order(plan_id, name)
    if not o.get("success"):
        answer_cb = o.get("error", "Order nahi ban paaya")
        return edit(chat, msg_id, f"❌ {answer_cb}")
    LAST_ORDER[user] = o["order_code"]
    upi = o.get("upi_id") or UPI_ID
    amount = o.get("amount")
    code = o["order_code"]
    upi_link = o.get("upi_link") or (
        f"upi://pay?pa={upi}&pn=OSINT%20API%20Hub&am={amount}&cu=INR&tn={code}")
    qr = f"https://api.qrserver.com/v1/create-qr-code/?size=400x400&data={upi_link}"
    try:
        tg("sendPhoto", chat_id=chat, photo=qr,
           caption=("💳 *Payment details*\n\n"
                    f"💰 Amount: ₹{amount}\n"
                    f"🏦 UPI ID: `{upi}`\n"
                    f"📝 Remark: *{code}*\n\n"
                    "⚠️ Remark me ye code likhna zaroori hai — isi se aapka key "
                    "auto-activate hoga.\n\n"
                    "Pay karke ✅ *Paid — Check* dabayein."))
    except Exception:                                        # noqa: BLE001
        send(chat, f"💳 Pay ₹{amount} to `{upi}`\nRemark: *{code}*\n{upi_link}")
    edit(chat, msg_id, "👇 UPI QR bhej diya. Pay karke *Paid — Check* dabayein.",
         kb([[("✅ Paid — Check", "paid:")], [("❌ Cancel", "cancel")]]))


def on_paid(chat, msg_id, user):
    code = LAST_ORDER.get(user)
    if not code:
        return edit(chat, msg_id, "Koi pending order nahi mila. /buy se naya order banayein.")
    d = order_status(code)
    if d.get("status") == "paid":
        LAST_ORDER.pop(user, None)
        return edit(chat, msg_id,
                    "✅ *Payment confirmed!*\n\n"
                    f"🔑 *Aapki API Key:*\n`{d['api_key']}`\n\n"
                    f"📅 Valid: {d.get('days')} din\n"
                    "Kaise use karein:\n"
                    f"`{API_BASE}/api/num-info?key={d['api_key']}&q=919973700984`\n\n"
                    "Ya bot me /num bhejein.\n\n"
                    f"_Powered by {BRAND}_",
                    kb([[("📊 My Subscription", "m:status")], [("🏠 Menu", "m:start")]]))
    edit(chat, msg_id,
         f"⏳ Payment abhi receive nahi hua.\nOrder: `{code}`\n\n"
         "Payment karne ke 10-30 second baad dobara dabayein.\n"
         f"Problem ho to {SUPPORT} pe message karein.",
         kb([[("🔄 Dobara Check", "paid:")], [("❌ Cancel", "cancel")]]))


def handle_command(chat, user, name, text):
    parts = text.strip().split(maxsplit=1)
    cmd = parts[0].lower().split("@")[0]
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd == "/start":
        return cmd_start(chat, user, name)
    if cmd == "/menu":
        return send(chat, "🏠 *Main Menu*", MENU)
    if cmd in ("/status", "/myplan", "/subscription"):
        return cmd_status(chat)
    if cmd in ("/buy", "/plans", "/order"):
        return cmd_buy(chat)
    if cmd in ("/support", "/help"):
        return send(chat, f"💬 Support: {SUPPORT}\n\n⚡ Powered by {BRAND}", MENU)
    if cmd in ("/key", "/mykey"):
        return send(chat, f"🔑 Bot key: `{API_KEY}`\n\n" + subscription_text())

    if cmd == "/num":
        if not arg:
            WAITING[user] = "num"
            return send(chat, "📱 Number bhejein (10 digits), example: `919973700984`")
        return send(chat, api_get("num-info", q=arg))
    if cmd == "/vehicle":
        if not arg:
            WAITING[user] = "vehicle"
            return send(chat, "🚗 Vehicle number bhejein, example: `BR30AR0802`")
        return send(chat, api_get("vehicle-report", q=arg))
    if cmd == "/aadhaar":
        if not arg:
            WAITING[user] = "aadhaar"
            return send(chat, "🆔 12 digit Aadhaar number bhejein")
        return send(chat, api_get("aadhaar-family", aadhaar=arg))
    if cmd == "/family":
        if not arg:
            WAITING[user] = "family"
            return send(chat, "👨‍👩‍👧 Number bhejein")
        return send(chat, api_get("family", q=arg))
    if cmd == "/email":
        if not arg:
            WAITING[user] = "email"
            return send(chat, "📧 Email bhejein")
        return send(chat, api_get("email-info", q=arg))
    if cmd == "/pass":
        if not arg:
            WAITING[user] = "pass"
            return send(chat, "🔐 Password bhejein")
        return send(chat, api_get("pass-check", q=arg))
    if cmd in ("/yt", "/youtube"):
        if not arg:
            WAITING[user] = "yt"
            return send(chat, "▶️ YouTube link bhejein")
        return send(chat, api_get("youtube-download", url=arg))
    if cmd == "/ytmp3":
        if not arg:
            WAITING[user] = "ytmp3"
            return send(chat, "🎵 YouTube link bhejein")
        return send(chat, api_get("youtube-mp3", url=arg))

    # free text = pending input ka jawab
    act = WAITING.pop(user, None)
    if act:
        route = {"num": ("num-info", "q"), "vehicle": ("vehicle-report", "q"),
                 "aadhaar": ("aadhaar-family", "aadhaar"), "family": ("family", "q"),
                 "email": ("email-info", "q"), "pass": ("pass-check", "q"),
                 "yt": ("youtube-download", "url"), "ytmp3": ("youtube-mp3", "url")}
        if act in route:
            path, param = route[act]
            return send(chat, api_get(path, **{param: text.strip()}))

    return send(chat, "🤔 Samajh nahi aaya. /start dabayein ya niche menu use karein.", MENU)


# ---------------------------------------------------------------- callback router
def on_callback(cb):
    data = cb.get("data", "")
    chat = cb["message"]["chat"]["id"]
    msg_id = cb["message"]["message_id"]
    user = cb["from"]["id"]
    name = cb["from"].get("first_name", "User")
    answer(cb["id"])

    if data == "verify":
        return edit(chat, msg_id, "✅ Verified!" if is_subscribed(user)
                    else f"❌ Abhi join nahi kiya. {FORCE_CHANNEL} join karein.")
    if data == "cancel":
        LAST_ORDER.pop(user, None)
        return edit(chat, msg_id, "❌ Cancel kar diya. /buy se dobara try karein.")

    if data == "m:start":
        return cmd_start(chat, user, name)
    if data == "m:status":
        return edit(chat, msg_id, subscription_text(), kb([[("🏠 Menu", "m:start")]]))
    if data == "m:buy":
        return edit(chat, msg_id, "💎 Plans chunein 👇", plan_keyboard())
    if data == "m:support":
        return edit(chat, msg_id, f"💬 Support: {SUPPORT}\n\n⚡ Powered by {BRAND}",
                    kb([[("🏠 Menu", "m:start")]]))
    if data == "paid:":
        return on_paid(chat, msg_id, user)
    if data.startswith("plan:"):
        return on_plan(chat, msg_id, user, data.split(":", 1)[1], name)

    if data.startswith("m:"):
        act = data.split(":", 1)[1]
        labels = {"num": ("📱 Number bhejein (10 digits)", "num-info", "q"),
                  "vehicle": ("🚗 Vehicle number bhejein (e.g. BR30AR0802)", "vehicle-report", "q"),
                  "aadhaar": ("🆔 12 digit Aadhaar number bhejein", "aadhaar-family", "aadhaar"),
                  "family": ("👨‍👩‍👧 Number bhejein", "family", "q"),
                  "email": ("📧 Email bhejein", "email-info", "q"),
                  "pass": ("🔐 Password bhejein", "pass-check", "q"),
                  "yt": ("▶️ YouTube link bhejein", "youtube-download", "url"),
                  "ytmp3": ("🎵 YouTube link bhejein", "youtube-mp3", "url")}
        if act in labels:
            prompt, path, param = labels[act]
            WAITING[user] = act
            return edit(chat, msg_id, prompt, kb([[("🏠 Menu", "m:start")]]))

    return edit(chat, msg_id, "Menu 👇", MENU)


# ---------------------------------------------------------------- main loop
def main():
    print(f"🤖 Advanced bot chal gaya — {BRAND}")
    print("   API:", API_BASE)
    offset = None
    while True:
        try:
            r = requests.get(f"{API}/getUpdates",
                             params={"timeout": 30, "offset": (offset + 1) if offset else None},
                             timeout=60).json()
            for upd in r.get("result", []):
                offset = upd["update_id"]
                if "callback_query" in upd:
                    try:
                        on_callback(upd["callback_query"])
                    except Exception as e:                   # noqa: BLE001
                        print("callback error:", e)
                    continue
                msg = upd.get("message") or upd.get("edited_message")
                if not msg or "text" not in msg:
                    continue
                chat = msg["chat"]["id"]
                user = msg["from"]["id"]
                name = msg["from"].get("first_name", "User")
                text = msg["text"].strip()

                if not is_subscribed(user):
                    join_gate(chat)
                    continue
                try:
                    handle_command(chat, user, name, text)
                except Exception as e:                       # noqa: BLE001
                    print("handler error:", e)
                    send(chat, "⚠️ Kuch gadbad ho gayi, thodi der baad try karein.")
        except Exception as e:                               # noqa: BLE001
            print("loop error:", e)
            time.sleep(5)


if __name__ == "__main__":
    main()
