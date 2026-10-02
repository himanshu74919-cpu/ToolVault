# 🛰️ OSINT & Multi-Utility API Hub (59 Endpoints + SQLite Database + Dashboard)

Ye ek **ready-to-use API Hub** hai jo `https://osint-apis-hub.onrender.com` jaisi hi endpoint
structure follow karta hai, lekin isme extra milta hai:

* ✅ **59 endpoints** – `?key=Demo` ya aapki khud ki API key se
* ✅ **SQLite database** – apna khud ka data (leak records, phone, GST, PAN, vehicle…) dashboard se add karein
* ✅ **Web Dashboard** (`/dashboard`) – tablet/mobile friendly, bina coding ke sab kuch control
* ✅ **Smart fallback** – har endpoint pehle free native source try karta hai, agar poori data
  nahi milti to upstream (osint-apis-hub) se merge kar leta hai. Ek source down hone par doosra
  version automatically try hota hai.
* ✅ **Response caching** – same query dobara aane par turant result (SQLite cache)
* ✅ **API key management** – naye keys banao, disable karo, usage dekho
* ✅ **Free cloud hosting** – Render / Railway / Koyeb / Docker, kahin bhi

---

# ⭐ 2 KHAAS APIs (aapke liye banayi gayi)

## 1️⃣ `/api/num-info` — Full Number Report

**Request:**
```
GET /api/num-info?key=Demo&q=9058390341
GET /api/num-info?key=Demo&q=919058390341&format=text   ← Telegram/WhatsApp ke liye card
```

Number 10-digit ho ya `91` ke saath — dono chalega (API khud dono variant try karta hai).
Extra params: `&deep=1` (aur number variants try kare), `&raw=1` (raw upstream data bhi).

**`&format=text` output (bilkul waise hi):**
```
🔍 NUMBER REPORT — 9058390341
━━━━━━━━━━━━━━━━━━━━━━
👤 Name: Brajesh Kumar
👨 Father: Rabendra Singh
📱 Phones/Alt: 9058390341, 916395131687
🌐 Region: JIO UPE UPW; AIRTEL UPW; JIO UPW
🆔 Govt ID: 861313813129
🏠 Address(es):
   └ S/O Rabendra Singh,puraiya,JOGAAMainpuri,Uttar Pradesh,206301
────────────────────────
📶 Live data · num-info, leak-v1 · 02-10-2026 22:03
```

**JSON output:**
```json
{
  "success": true,
  "query": "9058390341",
  "record_count": 1,
  "people": [
    {
      "name": "Brajesh Kumar",
      "father_name": "Rabendra Singh",
      "phones": ["9058390341", "916395131687"],
      "alt_phones": ["916395131687"],
      "region": "JIO UPE UPW; AIRTEL UPW; JIO UPW",
      "govt_ids": ["861313813129"],
      "emails": [],
      "addresses": ["S/O Rabendra Singh,puraiya,JOGAAMainpuri,Uttar Pradesh,206301"],
      "sources": ["num-info"]
    }
  ],
  "sources_used": ["num-info", "leak-v1"],
  "formatted": "🔍 NUMBER REPORT — ...",
  "timestamp_ist": "02-10-2026 22:03:41"
}
```

**Data kahan se aata hai (priority order):**
1. 🗄️ **Aapka apna database** (Dashboard → Database → category `phone` / `leak`)
2. 🌐 `num-info` source
3. 🌐 `leak-v1` / `leak-v2` sources

Sab sources ke records **ek saath merge** hote hain — ek hi insaan ke multiple phone numbers,
govt IDs aur addresses ek card me aa jate hain.

---

## 2️⃣ `/api/vehicle-report` — RC + Challan Full Report

**Request:**
```
GET /api/vehicle-report?key=Demo&number=BR30AR0802
GET /api/vehicle-report?key=Demo&number=BR30AR0802&format=text   ← ready to share card
```
Aliases: `/api/vehicle-full?number=...`, `/api/rc-info?rc=...`

**`&format=text` output:**
```
🚘 VEHICLE REPORT — BR30AR0802
━━━━━━━━━━━━━━━━━━━━━━
🚗 VEHICLE
• Maker / Model: HONDA SHINE
• Class: M-CYCLE/SCOOTER
• Fuel: PETROL • 124.6 cc
• Seating: 2
• Emission: BHARAT STAGE VI
━━━━━━━━━━━━━━━━━━━━━━
👤 OWNER & RTO
• Owner: S*****U S*H
• RTO: SITAMARHI, BIHAR · Sitamarhi
• RTO Phone: NA
• RTO Site: https://state.bihar.gov.in/transport/CitizenHome.html
━━━━━━━━━━━━━━━━━━━━━━
📅 RC / PAPERS
• Registration: 29-Aug-2025
• Fitness upto: 28-Aug-2040
• Tax upto: LTT
• Vehicle Age: 1 years , 1 months & 3 days
• Finance: NA (no hypothecation)
━━━━━━━━━━━━━━━━━━━━━━
🛡️ INSURANCE & PUC
• Insurance: GO DIGIT GENERAL INSURANCE LTD
• Valid upto: 27-Jul-2030 (Insurance Valid Upto 3 years , 9 months & 25 days)
• Status: You Are Insured
• PUC: 28-Aug-2026 (PUC Already Expired)
━━━━━━━━━━━━━━━━━━━━━━
🚨 CHALLANS — 1 found
• ⏳ Pending: 1 — ₹1,000
• 💰 Total amount (all challans): ₹1,000

🔹 #BR250023260716183506
   👤 Accused: R****T K***R
   💰 Amount: ₹1,000
   📅 Date: 16-07-2026
   ❌ Status: ⏳ PENDING
   🛑 Offence: Driving without helmet
   📍 Place: HFXW+W8F Narayan sah chowk, Vidya Pati Nagar, Bhabdepur, Sitamarhi, Bihar 843302, India
━━━━━━━━━━━━━━━━━━━━━━
📶 Live data · vehicle-rc, vehicle-challan, vehicle-challan-v4 · 02-10-2026 22:03
Confirm once on the official e-Challan / Parivahan site before paying anything.
```

**JSON sections:** `vehicle`, `owner`, `rto`, `rc`, `insurance`, `puc`,
`challans{count, pending_count, pending_amount, total_amount, list[]}`,
`local_analysis` (offline RTO decode), `custom_database_records` (aapka data).

**Data sources (auto fallback):** `vehicle-rc` → `vehicle-info` / `vehicle-details` / `vehicle-v`
+ challan list `vehicle-challan` + challan summary `vehicle-challan-v4`.
Koi source down ho to report bina us section ke bhi banti hai — kabhi pura fail nahi hota.

---

## 🚀 Local me kaise chalayein (testing ke liye)

```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

Phir browser me kholein: `http://localhost:8000/dashboard`

* Dashboard login password (default): **`admin123`**
  (Settings tab ya environment variable `ADMIN_PASSWORD` se badal sakte hain)

---

## ☁️ Free hosting (Render.com) – step by step (Hinglish)

1. GitHub par naya **repository** banao (public ya private, dono chalega).
2. Is project ki saari files us repo me upload kar do:
   `main.py`, `requirements.txt`, `render.yaml`, `Procfile`, `Dockerfile`, `README.md`, `.gitignore`
3. [render.com](https://render.com) kholo → **Sign in with GitHub**.
4. **New +** → **Blueprint** → apna repo select karo → **Apply**.
   Render khud `render.yaml` padh kar service bana dega.
5. Deploy complete hone par URL milega: `https://osint-api-hub-xxxx.onrender.com`
6. **Environment** section me `ADMIN_PASSWORD` apna strong password set kar do.
7. Dashboard kholne ke liye: `https://aapka-url.onrender.com/dashboard`

> **Note:** Render free tier 15 minute inactive rehne par sleep kar deta hai.
> Pehli request me 30–60 second lag sakte hain (cold start). Baad me fast.
> Free tier ki disk temporary hoti hai — isliye important data ko Database tab se
> **Export CSV** kar ke apne paas save kar lete raho.

**Railway / Koyeb / Heroku** par bhi chalega – `Procfile` aur `Dockerfile` pehle se maujood hain.
Bus env variable `PORT` automatically mil jata hai.

---

## 🔑 API Key

| Key | Kahan use karein |
|-----|------------------|
| `Demo` | By default sabke liye enabled. |
| `osint-xxxx...` | Dashboard → **API Keys** tab se naya key banao. |

Example: `https://aapka-url.onrender.com/api/ifsc?key=Demo&ifsc=SBIN0000001`

Agar aapko `Demo` key band karni ho to: **Settings → Demo key enabled → No → Save**.

---

## 📋 Pure 59 Endpoints (Base URL ke saath)

Base URL example: `https://aapka-url.onrender.com` (ya local me `http://localhost:8000`)

### 🌐 IP & Network
| # | Endpoint | Example |
|---|----------|---------|
| 1 | `/api/ip-v1` | `?key=Demo&query=8.8.8.8` |
| 2 | `/api/ip-v2` | `?key=Demo&ip=157.35.26.44` |
| 3 | `/api/ip-v3` | `?key=Demo&ip=157.35.26.44` |

### 📱 Device / 🌍 World / 🤖 AI / 🐙 Code
| # | Endpoint | Example |
|---|----------|---------|
| 4 | `/api/imei` | `?key=Demo&imei=353010111111110` |
| 5 | `/api/country` | `?key=Demo&name=india` |
| 6 | `/api/ai-gf` | `?key=Demo&prompt=hi` |
| 7 | `/api/github` | `?key=Demo&q=@Rohit` |

### 🇮🇳 India Utilities
| # | Endpoint | Example |
|---|----------|---------|
| 8 | `/api/ifsc` | `?key=Demo&ifsc=SBIN0000001` |
| 9 | `/api/pincode` | `?key=Demo&pincode=110001` |

### 🚗 Vehicle
| # | Endpoint | Example |
|---|----------|---------|
| 10 | `/api/vehicle-challan` | `?key=Demo&number=HR26EV0001` |
| 11 | `/api/vehicle-challan-v2` | `?key=Demo&number=HR26EV0001` |
| 12 | `/api/vehicle-challan-v4` | `?key=Demo&number=HR26EV0001` |
| 13 | `/api/vehicle-info` | `?key=Demo&vehicle_number=HR26EV0001` |
| 14 | `/api/vehicle-info-v2` | `?key=Demo&vehicle_number=HR26EV0001` |
| 15 | `/api/vehicle-rc` | `?key=Demo&number=HR26EV0001` |
| 16 | `/api/vehicle-details` | `?key=Demo&number=MH12DE1433` |
| 17 | `/api/vehicle-v` | `?key=Demo&rc=MH12DE1433` |

### 🎵 Media / 👻 Social / 📦 Files / 🎮 Gaming
| # | Endpoint | Example |
|---|----------|---------|
| 18 | `/api/song` | `?key=Demo&song=chandani` |
| 19 | `/api/snap-stories` | `?key=Demo&username=priyapanchal272` |
| 20 | `/api/snap-highlights` | `?key=Demo&username=priyapanchal272` |
| 21 | `/api/instagram-profile` | `?key=Demo&username=sumit_sharma2` |
| 22 | `/api/instagram-posts` | `?key=Demo&username=sumit_sharma2` |
| 23 | `/api/terabox-file` | `?key=Demo&url=https://1024terabox.com/s/1ahJz-qdH7h_9One0lXxDoA` |
| 24 | `/api/terabox-stream` | `?key=Demo&url=https://1024terabox.com/s/1EqwgqQWgmeOvQxc33258UA` |
| 25 | `/api/terabox-stream-v2` | `?key=Demo&url=https://1024terabox.com/s/1EqwgqQWgmeOvQxc33258UA` |
| 26 | `/api/terabox-stream-v3` | `?key=Demo&url=https://1024terabox.com/s/1EqwgqQWgmeOvQxc33258UA` |
| 27 | `/api/bgmi` | `?key=Demo&user=55622571339` |
| 28 | `/api/image-to-prompt` | `?key=Demo&url=https://i.ytimg.com/vi/X8X-XyK4CYE/mqdefault.jpg` |

### ▶️ YouTube
| # | Endpoint | Example |
|---|----------|---------|
| 29 | `/api/youtube-all` | `?key=Demo&url=https://youtube.com/watch?v=X8X-XyK4CYE` |
| 30 | `/api/youtube-info` | `?key=Demo&url=https://youtube.com/watch?v=X8X-XyK4CYE` |
| 31 | `/api/youtube-info-id` | `?key=Demo&id=X8X-XyK4CYE` |

### 🔥 Leak OSINT
| # | Endpoint | Example |
|---|----------|---------|
| 32 | `/api/leak-v1` | `?key=Demo&q=919973700987` |
| 33 | `/api/leak-v2` | `?key=Demo&q=919973700987` |
| 34 | `/api/num-info` | `?key=Demo&q=919973700984` |

### 🧮 GST / PAN
| # | Endpoint | Example |
|---|----------|---------|
| 35 | `/api/gst-search` | `?key=Demo&gstin=19BOKPS7056D1ZI` |
| 36 | `/api/gst-direct` | `?key=Demo&gstin=19BOKPS7056D1ZI` |
| 37 | `/api/gst-info` | `?key=Demo&gst=19BOKPS7056D1ZI` |
| 38 | `/api/gst-info-v2` | `?key=Demo&gst=19BOKPS7056D1ZI` |
| 39 | `/api/pan-to-gst` | `?key=Demo&pan=BOKPS7056D` |
| 40 | `/api/pan-to-gst-v2` | `?key=Demo&pan=BOKPS7056D` |
| 41 | `/api/pan-to-gst-v3` | `?key=Demo&pan=AACCL5754F` |
| 42 | `/api/pan-to-gst-v4` | `?key=Demo&pan=AAYFK4129N` |
| 43 | `/api/pan-info` | `?key=Demo&pan=AAYFK4129N` |

### ⭐ Aapke khaas report APIs
| # | Endpoint | Example |
|---|----------|---------|
| 44 | `/api/vehicle-report` | `?key=Demo&number=BR30AR0802` (RC + RTO + insurance + PUC + challans) |
| 45 | `/api/vehicle-full` | `?key=Demo&number=MH12DE1433` (alias) |
| 46 | `/api/rc-info` | `?key=Demo&rc=BR30AR0802` (alias) |
| 47 | `/api/number-info` | `?key=Demo&q=9058390341` (alias of num-info) |
| 48 | `/api/num` | `?key=Demo&q=9058390341` (short alias) |

(`/api/num-info` upar #34 me hai — wo ab full merged report deta hai.)


### 🆕 Naye (Family + Email)
| # | Endpoint | Example |
|---|----------|---------|
| 49 | `/api/family` | `?key=Demo&q=919973700984` — linked/family numbers (same address / same father) |
| 50 | `/api/num-family` | `?key=Demo&q=Pramila Hembram` — alias, naam se bhi search |
| 51 | `/api/email-info` | `?key=Demo&email=ranjitkumarlalgonv@gamil.com` |
| 52 | `/api/email` | alias of email-info |
| 53 | `/api/pass-check` | `?key=Demo&password=Katihar@123` — breach count (k-anonymity) |

---

## 👨‍👩‍👧 `/api/family` — Family / Linked Numbers (NEW)

Number ya naam daalo → **sirf wahi log** dikhte hain jinke
**address ke words match** karte hain ya **father ka naam same** hai.
(Bakwas results hatane ke liye smart scoring use hota hai.)

```
GET /api/family?key=Demo&q=919973700984&format=text
```

```
👨‍👩‍👧 FAMILY / LINKED NUMBERS — 919973700984
━━━━━━━━━━━━━━━━━━━━━━
👤 Primary: Anurag Ranjan (S/O Akhilesh Kumar Ray)
📱 Numbers: 919973700984, 917352858502
🏠 Address: khajauli,Khajaui Urf Aurangabad,...,Vaishali,Bihar,844121
────────────────────────
👥 Linked members (14):
 1. Abhijeet Kumar — 919973700984, 917070341551
    🔗 possible sibling / brother-sister (same father)  (match 6)
    👨 Father: Akhilesh Kumar Ray
    🏠 Paharpur,Vaishali Prataptand Lalganj,Bihar,844123
 2. Chandan Kumar — ..., 917367945181
    🔗 possible sibling (same father)
...
📶 Live data · num-info, leak-v1 · 02-10-2026 22:52
Relations guessed from address/father matching - verify before trusting.
```

Params: `q` (number / naam), `&limit=25` (max members), `&deep=0` (fast, bina extra search),
`&format=text` (card).

---

## 📧 `/api/email-info` — Email OSINT (NEW)

```
GET /api/email-info?key=Demo&email=ranjitkumarlalgonv@gamil.com&format=text
```

```
📧 EMAIL REPORT — ranjitkumarlalgonv@gamil.com
━━━━━━━━━━━━━━━━━━━━━━
✅ Format: Valid
🏢 Provider: Custom / private domain
⚠️ Disposable: No
🌐 Mail server (MX): 0 mail.gamil.com
🔐 SPF: v=spf1 a mx include:... -all
🖼️ Gravatar: Not found
────────────────────────
👤 IDENTITY RECORDS (25):
 • Pramila Hembram | 👨 Ratan Tudu
   📱 9973700987   🌐 JIO BHR&JHR
   🏠 w/o ratan tudu,Jhawa,sohasa...,katihar,Bihar,855113
   🆔 300664932743
────────────────────────
📶 Live data · leak-v1 · 02-10-2026 22:48
```

Milta hai: format validity, provider, disposable check, MX + SPF (Google DNS), Gravatar,
**leak records** (naam/phone/address/ID) aur leaked credentials (masked password).
Optional: Settings me apni **HaveIBeenPwned API key** daalo to HIBP breach list bhi aayegi.

## 🔑 `/api/pass-check` — Password Breach Check (BONUS)

```
GET /api/pass-check?key=Demo&password=Katihar@123&format=text
```
```
🔑 PASSWORD CHECK — K*********3
🚨 Status: LEAKED ❌
📊 Kitni baar mila: 4,097 breaches/combo lists me
🔒 SHA1 prefix: 3BF2D… (password kabhi server se bahar nahi gaya)
```
(Pwned Passwords **k-anonymity** — sirf hash ka 5-letter prefix bheja jata hai.)

---

# 💰 API BECHNA KAISE HAIN (₹100 / month wala pura system)

Ye sab **built-in** hai, koi extra coding nahi chahiye:

## Step 1 — Customer ka key banao (Dashboard → API Keys)
| Field | Kya bharein |
|-------|-------------|
| Customer naam | jise bech rahe ho |
| Validity | `30 din` (= 1 month ₹100), `90 din`, `365 din`, ya `Lifetime` |
| Plan | `SAB endpoints` / `Popular pack` / `Sirf Number pack` / `Custom` (comma separated) |
| Device lock | `ON` → key sirf 1 device (phone/PC) par chalegi |
| Price note | sirf record ke liye (₹100) |

**Create** dabaate hi key ban jati hai → customer ko bhej do.

## Step 2 — Month khatam hone par kya hoga?
* Expiry ke baad har request par **403** milega:
```json
{"success":false,"status":"expired","error":"Your API key expired on 2026-11-01 22:50:57 (IST)."}
```
* Dashboard me expiry **red** dikhegi (`days_left` negative).
* Payment milte hi **+30d** button dabao → `POST /admin/keys/{id}/extend` →
  naya expiry = (purana expiry ya aaj, jo bhi baad ho) + 30 din. **Key turant wapas chalu.**

## Step 3 — Device lock kaise kaam karta hai
* `device_lock=1` + `max_devices=1` → pehli request jis IP / `?device=ID` se aayi, wahi bind ho gaya.
* Doosre device se call → `403 device_locked`.
* Customer ko naye phone par chalana ho to dashboard me **♻️ Reset device** dabao
  (`POST /admin/keys/{id}/unbind`) → naya device bind ho jayega.
* Customer apne bot me `&device=myname` lagaye to IP change hone par bhi lock safe rahega.

## Step 4 — Customer khud status check kare
```
GET https://aapka-url.onrender.com/api/key-info?key=<uski_key>
```
```json
{"plan":["num-info","vehicle-report"],"expires_at_ist":"2026-11-01 22:50:57",
 "days_left":30,"status":"active","device_lock":true,"requests_used":1}
```

## Step 5 — Plan ke bahar wali API
```
GET /api/family?key=<sirf num-info wala key>&q=...
```
```json
{"success":false,"status":"not_in_plan","error":"Aapka plan is endpoint ko allow nahi karta",
 "your_plan":["num-info","vehicle-report"]}
```

## Pricing ideas (aap marzi se)
| Plan | Validity | Endpoints | Price |
|------|----------|-----------|-------|
| Trial | 7 din | num-info + family | free / ₹20 |
| Number pack | 30 din | num-info, family, num | ₹100 |
| Vehicle pack | 30 din | vehicle-report, vehicle-rc, vehicle-challan* | ₹100 |
| Email pack | 30 din | email-info, pass-check | ₹100 |
| Full access | 30 din | `*` (sab) | ₹200–300 |
| Reseller | 365 din | `*` | ₹1000+ |

Payment lene ke liye: UPI QR / PhonePe / Paytm / Google Pay — aur key WhatsApp pe bhej do.
Dashboard → Keys → **📤 Bhejo** button customer ko bhejne layak poora message de deta hai.

---

# 🤖 Telegram Bot me use kaise karein

`examples/telegram_bot.py` ready hai. Bas 3 line me apna data bharein
(`BOT_TOKEN`, `API_BASE`, `API_KEY`) aur `python telegram_bot.py` chalao.

Commands: `/num 919973700984`, `/vehicle BR30AR0802`, `/family 919973700984`,
`/email test@gmail.com`, `/pass mypassword`, `/key`.

Har command `&format=text` use karta hai, isliye response seedha card ki tarah dikhta hai.

> Bot host karne ke liye bhi Render use kar sakte ho (Background Worker) ya apne PC/tablet
> (Termux) par chala sakte ho.

# 🌐 Website me use kaise karein

`examples/website_demo.html` kholo → base URL + key daalo → Run.
CORS hub me pehle se **ON** hai, isliye koi bhi website (ya GitHub Pages) direct call kar sakti hai.

JavaScript example:
```js
const r = await fetch("https://aapka-url.onrender.com/api/num-info?key=Demo&q=919973700984&format=text");
document.getElementById("out").textContent = await r.text();
```

PHP example:
```php
$txt = file_get_contents("https://aapka-url.onrender.com/api/num-info?key=Demo&q=919973700984&format=text");
echo $txt;
```

Python example:
```python
import requests
print(requests.get("https://aapka-url.onrender.com/api/vehicle-report",
                   params={"key":"Demo","number":"BR30AR0802","format":"text"}, timeout=90).text)
```

---

# 📤 GITHUB PE NAYA REPOSITORY KAISE BANAYEIN (step by step)

**A. GitHub account + naya Gmail**
1. [github.com](https://github.com) kholo → **Sign up** → apna naya Gmail daalo → password banao → verify karo.
2. Login ke baad upar right me **+ → New repository**.
3. **Repository name**: `osint-api-hub`
4. **Public** select karo (Render free tier public repo ke saath best kaam karta hai; private bhi chalega par GitHub se connect karte waqt permission deni padti hai).
5. **Add a README file** ko tick kar do (baaki sab unchecked).
6. **Create repository** dabao.

**B. Files upload kaise karein (bina coding ke, browser se)**
1. Repo page par **Add file → Upload files**.
2. In files ko drag & drop karo (sab ek saath):
   `main.py`, `requirements.txt`, `render.yaml`, `Procfile`, `Dockerfile`, `README.md`, `.gitignore`
   aur `examples/` folder ki files (folder ke liye: `examples` naam se naya file →
   `examples/telegram_bot.py` type kar ke slash daba do, GitHub folder bana deta hai).
3. Niche **Commit changes** (green button) dabao. Ho gaya — code GitHub par hai ✅

> (Agar aap chahein to mujhe GitHub username + token de do, main khud push kar dunga.)

**C. Personal Access Token (agar main push karun to)**
1. GitHub → right-top profile photo → **Settings** → **Developer settings** (sabse niche).
2. **Personal access tokens → Tokens (classic) → Generate new token (classic)**.
3. Note: `osint-hub`, expiration: `30 days`, **repo** ka checkbox tick karo.
4. **Generate token** → token copy kar lo (ek hi baar dikhta hai).
5. Kaam ho jane ke baad token ko **Delete** kar dena (safety).

---

# ☁️ RENDER PE DEPLOY — step by step (Tablet/Phone se bhi ho jayega)

1. [render.com](https://render.com) kholo → **Get Started** → **Sign in with GitHub** → authorize karo.
2. Dashboard par **New +** button (upar right) → **Blueprint**.
3. Apna repo (`osint-api-hub`) select karo → **Connect**.
   - Render `render.yaml` khud padh lega: plan `free`, runtime `python`,
     build `pip install -r requirements.txt`, start `uvicorn main:app --host 0.0.0.0 --port $PORT`.
4. **Apply** dabao → 2–4 minute me deploy ho jayega.
5. Upar mila URL (jaise `https://osint-api-hub-xxxx.onrender.com`) copy karo.
6. **Environment** tab me jaakar set karo:
   - `ADMIN_PASSWORD` = apna strong password (zaroor badlo!)
   - `DEMO_KEY` = `Demo` (ya kuch aur)
7. Test: browser me `https://...onrender.com/dashboard` kholo → wahi dashboard dikhega.
8. Pehla API test:
   `https://...onrender.com/api/num-info?key=Demo&q=919973700984&format=text`

### Render free tier ke rules
* 15 minute tak koi request na aaye to service **sleep** ho jati hai; agle request me 30–60s lagte hain (cold start).
* Sleep se bachne ke liye: [cron-job.org](https://cron-job.org) ya [uptimerobot.com](https://uptimerobot.com)
  par free account banao aur har 10 minute me apne `/health` URL ko ping karwao.
* Free tier ki disk temporary hai → **Database tab se CSV export karte raho** (backup).

### Deploy ke baad kya karein (5 minute ka checklist)
- [ ] Dashboard kholo, `ADMIN_PASSWORD` se login karo
- [ ] Settings me `Demo` key ko ON/OFF decide karo (selling ke liye OFF rakhna better)
- [ ] API Keys tab se pehla customer key banao (30 din, plan select karke)
- [ ] `examples/telegram_bot.py` me apna Render URL + token + key daal kar bot chalao
- [ ] Har hafte Database → Export CSV se backup le lo

---

## 🆘 Koi problem aaye to
| Problem | Solution |
|---------|----------|
| `502 / no data` | Upstream (source) down hai — thodi der baad try karo; purana cached result bhi mil jata hai |
| `401 invalid key` | `?key=Demo` lagao ya dashboard se naya key banao |
| `403 expired` | Dashboard → Keys → **+30d** (renew) |
| `403 device_locked` | Dashboard → Keys → **♻️ Reset device** |
| `429 rate limit` | Settings me `rate_limit_per_min` badhao (default 120) |
| Dashboard blank | URL ke aage `/dashboard` lagao; phir bhi na chale to Render logs dekho |
| Deploy fail | Render → **Logs** tab me error padho; aksar `requirements.txt` ya `PORT` ki wajah se hota hai |


### 🆕 Aadhaar Family + YouTube Downloader
| # | Endpoint | Example |
|---|----------|---------|
| 54 | `/api/aadhaar-family` | `?key=Demo&aadhaar=861313813129` — parivar ke members + district/state |
| 55 | `/api/aadhaar` | alias |
| 56 | `/api/ration` | alias (ration card number se bhi try) |
| 57 | `/api/youtube-download` | `?key=Demo&url=https://youtube.com/watch?v=X8X-XyK4CYE` |
| 58 | `/api/ytdl` | alias |
| 59 | `/api/youtube-mp3` | `?key=Demo&url=...` — sirf audio link |

---

# 📜 `/api/aadhaar-family` — AADHAAR FAMILY INTEL (NEW)

12-digit Aadhaar/UID daalo → us record se **parivar ke members** (same father / same address)
aur **district + state**.

```
GET /api/aadhaar-family?key=Demo&aadhaar=861313813129&format=text
```

```
╔══════════════════════════════════════╗
║       📜 AADHAAR FAMILY INTEL        ║
╚══════════════════════════════════════╝

💳 Aadhaar Number (searched)
┗ 🎫 XXXXXXXX3129

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

👨‍👩‍👧‍👦 FAMILY MEMBERS (13)
👤 1. Brajesh Kumar
 ┗ 💳 Aadhaar: XXXXXXXX3129
 ┗ 🔗 searched Aadhaar holder

 👤 2. Shailendra Singh
 ┗ 💳 Aadhaar: XXXXXXXX3264
 ┗ 🔗 possible sibling (same father)
...
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📍 LOCATION DETAILS
🗺️ District / State
┗ 🏙️ JOGAAMAINPURI / UTTAR PRADESH
┗ 📮 PIN: 206301

⚡ Powered by @Supermannn_x  |  API Developer / Telegram: @Supermannn_x
```

* **Aadhaar hamesha MASKED** hota hai (sirf last 4 digit) — privacy ke liye.
* `is_head` wale member ke saath 👑 (Head) lagta hai — jo naam sabse zyada logon ke
  father field me aata hai.
* `ration_card_number` / `fps_id` tabhi aate hain jab source me hon (apna data
  Database tab se add kar sakte ho — category `aadhaar` ya `ration`).
* Verhoeff checksum se number ki validity bhi check hoti hai (`aadhaar_valid_checksum`).

---

# ⬇️ `/api/youtube-download` — YouTube Downloader (NEW, Telegram bot ke liye perfect)

```
GET /api/youtube-download?key=Demo&url=https://youtube.com/watch?v=X8X-XyK4CYE&quality=720&format=text
GET /api/youtube-mp3?key=Demo&url=https://youtube.com/watch?v=X8X-XyK4CYE      # sirf audio
GET /api/youtube-download?key=Demo&url=...&type=audio                          # sirf audio
```

```
╔══════════════════════════════════════╗
║       ▶️ YOUTUBE DOWNLOAD LINKS      ║
╚══════════════════════════════════════╝

🎬 #video | सईयाँ सेवा करे | #Pawan Singh ...
📺 Mahi Movies Presents
⏱️ 3:01

🎥 VIDEO 1: 720p · mp4
┗ 🔗 https://rr2---sn-2onx5c-5x.googlevideo.com/videoplayback?...
🎵 AUDIO 2: 129.509kbps · m4a
┗ 🔗 https://rr2---sn-2onx5c-5x.googlevideo.com/videoplayback?...

⚠️ Ye direct links 2–6 ghante me expire ho jate hain.
```

**JSON fields:** `download_url` (best video/audio), `audio_url`, `links[]` (type/quality/ext/url),
`title`, `channel`, `duration`, `thumbnail`.

**Kaise kaam karta hai:** server par **`yt-dlp`** chal kar direct links nikalta hai
(`requirements.txt` me already add hai). Agar yt-dlp fail ho to upstream `youtube-all`
ke download links try karta hai.

> ⚠️ Render free tier ke IP ko YouTube kabhi-kabhi block kar deta hai — tab error aayega,
> kuch der baad phir try karein. Links ~2–6 ghante me expire ho jate hain, isliye bot me
> turant use karein.

**Telegram bot me bhejne ka tariqa:** `download_url` ko bot se `sendVideo`/`sendAudio`
me `video=url` / `audio=url` de dein — user ko file direct mil jayegi.

---

# ⚡ BRANDING — "Powered by @Supermannn_x" har jagah

Har response me aapka credit hai:

| Jagah | Kya dikhta hai |
|-------|----------------|
| Har JSON response | `"powered_by": "@Supermannn_x"` + `_meta.powered_by` |
| Har `format=text` card | last line: `⚡ Powered by @Supermannn_x \| API Developer / Telegram: @Supermannn_x` |
| Har error message | `"powered_by": "@Supermannn_x"` |
| `/api/endpoints`, `/health`, `/` | `developer`, `powered_by`, `telegram` fields |
| Dashboard | header me "API Developer: @Supermannn_x (Telegram)" + footer credit |

Badalna ho to: **Dashboard → Settings → Brand tag** (ya env `BRAND_TAG`).


### ➕ Extra helper endpoints (bonus)
| Endpoint | Kaam |
|----------|------|
| `GET /` | JSON list of all endpoints (browser me dashboard) |
| `GET /api/endpoints` | Endpoints ki list JSON me (examples ke saath) |
| `GET /dashboard` | Web dashboard |
| `GET /docs` | Swagger auto docs |
| `GET /health` | Server health |
| `GET /api/v1/search?q=` | Apne database me search (legacy) |

---

## 🗄️ Apna database kaise banayein (Dashboard se)

1. Dashboard → **Database** tab
2. **Category** chuno: `leak`, `phone`, `gst`, `pan`, `vehicle`, `imei`, `ip`, `general`
3. **Key** me wo value daalo jisse search karna hai (jaise `919973700984`)
4. **Data (JSON)** me record: `{"full_name":"Ram Kumar","address":"Bihar"}`
5. Save karo.

Ab jab koi `/api/num-info?key=Demo&q=919973700984` chalega, to response me
`custom_database_records` ke andar aapka data bhi aayega.

**Bulk import:** niche CSV box me ek line per record daalo:
```
919973700984,{"full_name":"Ram Kumar"},telegram se mila
919973700985,{"full_name":"Shyam"},own research
```

**Export:** Database tab → **Export CSV** (backup ke liye).

---

## 📊 Dashboard ke aur features

| Tab | Kya milta hai |
|-----|---------------|
| 🔌 Endpoints | 59 endpoints ki list, search, category filter, live test + response + Copy URL |
| 🗄️ Database | Records add / search / delete / CSV import / export |
| 🔑 API Keys | Naya key banao, enable-disable karo, request count dekho |
| 📊 Logs | Kaunsi API kab chali, source (native/upstream/cache), time, status |
| ⚙️ Settings | Upstream URL, cache TTL, rate limit, Demo key on/off, admin password |
| 📖 Help | Hinglish guide + hosting steps |

---

## 🔧 Environment variables (optional)

| Variable | Default | Meaning |
|----------|---------|---------|
| `PORT` | `8000` | Server port (Render khud set karta hai) |
| `DB_PATH` | `osint_database.db` | SQLite file ka path |
| `ADMIN_PASSWORD` | `admin123` | Dashboard login password |
| `UPSTREAM_BASE` | `https://osint-apis-hub.onrender.com` | Fallback upstream hub |
| `UPSTREAM_KEY` | `Demo` | Upstream key |
| `DEMO_KEY` | `Demo` | Apni public demo key |
| `GITHUB_TOKEN` | empty | GitHub search rate limit badhane ke liye |

---

## ⚠️ Important notes

* **Snapchat endpoints** (`snap-stories`, `snap-highlights`) upstream par bhi kabhi-kabhi
  fail hote hain – tab hub clean error message deta hai (`"error": "Status 0"`).
* **Vehicle / GST / PAN** endpoints me offline parsing hamesha kaam karti hai
  (state, RTO, GSTIN checksum, PAN holder type), aur jab upstream data milta hai to wo bhi
  `local_analysis` ke saath merge ho jata hai.
* **Leak OSINT** data upstream breach databases se aata hai – is hub me khud ka koi
  illegal data store nahi hai. Aap apna data Database tab se add kar sakte hain.
* Rate limit default: **120 requests/minute/key** (Settings me badal sakte hain).

---

## 📁 Files

```
main.py              # Poora app: API + SQLite + dashboard
requirements.txt     # Dependencies
render.yaml          # Render.com Blueprint config
Procfile             # Railway / Heroku / Koyeb
Dockerfile           # Docker image
README.md            # Ye file
osint_database.db    # SQLite database (runtime me banta hai)
```

Happy OSINT-ing! 🚀

---

## 🛡️ Reliability (upstream slow / down hone par bhi API chalu rahe)

| Feature | Kya karta hai |
|---------|---------------|
| **Smart cache** | Har successful response SQLite me cache hota hai. Number/leak reports **12 ghante**, vehicle report **6 ghante** cache rehte hain (Settings me `cache_ttl` se global change). |
| **Stale fallback** | Agar upstream abhi down / rate-limited hai, to purana cached result `X-Source: stale-cache` header ke saath wapas mil jata hai — API kabhi blank nahi jati. |
| **Auto version fallback** | `vehicle-rc` fail → `vehicle-info` → `vehicle-details` → `vehicle-v`. `terabox-stream-v3` fail → `v2` → `v1` → `file`. GST/PAN versions bhi aapas me fallback hote hain. |
| **Deadline guard** | `max_request_seconds` (default 50s) — ek request kabhi hang nahi hoti. Settings me badal sakte hain. |
| **Number variants** | `/api/num-info` 10-digit aur `91...` dono format khud try karta hai. `&deep=1` aur variants try karta hai. |

### Telegram bot me kaise use karein
Bas `&format=text` lagao — response seedha message me paste karne layak text aa jata hai:
```
https://aapka-url.onrender.com/api/num-info?key=Demo&q=919973700984&format=text
https://aapka-url.onrender.com/api/vehicle-report?key=Demo&number=BR30AR0802&format=text
```

---

## ⚖️ Responsible use

* Ye API **public / aggregated sources** se data laati hai — isme aapka apna koi government
  database nahi hai, aur na hi kisi paid service ka paywall todti hai.
* Jo personal data aata hai (naam, pata, ID) uska istemal sirf **apni verification / safety**
  ke liye karein. Har report me likha hota hai: *"Verify from a second source"* /
  *"Confirm once on the official e-Challan / Parivahan site before paying anything."*
* Challan payment hamesha **sarkari site** (Parivahan / echallan) par confirm kar ke hi karein.

---

# 🤝 RESELLER PANEL — dealers se API key bechwao (NEW)

Aapke dealers/resellers khud API key bana sakte hain — aap control karte ho
**kitna credit, kitne din tak, kaunse endpoints**.

## Flow
1. Dashboard → **🤝 Resellers** tab → naya reseller banao (username, password, credit, max days, endpoints).
2. Dealer ko username/password do (ya wo khud login kare reseller box se).
3. Dealer login karke **1 credit = 1 key** banata hai.
4. Wo **apne credit se zyada**, **apne endpoints ke bahar** ya **max days se zyada** key nahi bana sakta.

## Reseller API

| Method | Endpoint | Kya karta hai |
|---|---|---|
| POST | `/reseller/login` | `{username,password}` → `{token}` |
| GET | `/reseller/me` | apna credit + banaye gaye keys |
| GET | `/reseller/keys` | sirf apne keys |
| POST | `/reseller/keys` | naya key (`days`, `allowed_endpoints`, `customer`, `device_lock`) — 1 credit katega |
| POST | `/reseller/keys/{id}/extend` | `{"days":10}` |
| POST | `/reseller/keys/{id}/toggle` | enable / disable |

Header: `X-Reseller-Token: <token>` ya `?token=<token>`

**Example (dealer key banata hai):**
```bash
curl -X POST https://YOUR-URL.onrender.com/reseller/login \
  -H 'Content-Type: application/json' -d '{"username":"dealer1","password":"dealer123"}'
# {"success":true,"token":"..."}

curl -X POST https://YOUR-URL.onrender.com/reseller/keys \
  -H 'X-Reseller-Token: TOKEN' -H 'Content-Type: application/json' \
  -d '{"days":30,"allowed_endpoints":"num-info,family","customer":"End Customer"}'
# {"success":true,"api_key":"osint-XXXX","expires_at":"2026-11-01 23:32:07"}
```

Guard rails (live tested):
- Plan se bahar endpoint → `{"success":false,"error":"Ye endpoints aapke plan me nahi hain"}`
- Max days se upar → `{"success":false,"error":"Aap max 30 din ka key bana sakte ho"}`
- Credit 0 → `402`
- Galat password → `{"success":false,"error":"Galat username/password"}`

## Admin reseller API (dashboard wahi use karta hai)
`GET/POST /admin/resellers` · `POST /admin/resellers/{id}/credit {"amount":10}` ·
`POST /admin/resellers/{id}/plan` · `POST /admin/resellers/{id}/toggle` · `DELETE /admin/resellers/{id}`
(sab me header `x-admin-token: <admin password>`)

---

# 💸 UPI PAYMENT + AUTO KEY ACTIVATION (NEW)

Customer aapki **landing page** se plan chunta hai → UPI payment →
**payment aate hi key auto-activate** (ya aap manually approve kar do).

## Public API

| Method | Endpoint | Kya karta hai |
|---|---|---|
| GET | `/api/plans` | plans + price + UPI ID |
| POST | `/api/create-order` | `{plan, name, phone}` → `order_code`, `upi_link`, `qr_url` |
| GET | `/api/order-status?code=OSXXXX` | pending / **paid + api_key** |
| POST | `/webhook/payment` | payment aane par order match → key activate |
| POST | `/webhook/upi-sms` | UPI SMS text bhejein, amount/UTR parse hoga |

**Order banao:**
```bash
curl -X POST https://YOUR-URL.onrender.com/api/create-order \
  -H 'Content-Type: application/json' -d '{"plan":"num","name":"Rahul","phone":"9058390341"}'
# {"order_code":"OSY5CNS6K3","amount":100.0,"upi_link":"upi://pay?pa=...&am=100&tn=OSY5CNS6K3",
#  "qr_url":"https://api.qrserver.com/v1/create-qr-code/?...","status":"pending"}
```

**Payment aane par (webhook):**
```bash
curl -X POST https://YOUR-URL.onrender.com/webhook/payment \
  -H 'Content-Type: application/json' \
  -d '{"amount":100,"remark":"UPI/OSY5CNS6K3/payment","utr":"4321987654321","payer":"RAHUL"}'
# {"success":true,"matched":true,"status":"paid","api_key":"osint-XXXX","expires_at":"..."}
```
- Order **remark/UTR me code** se match hota hai, warna **amount** se (jo bhi pending ho).
- `x-webhook-secret` header set kar ke security on kar sakte ho (Settings → Webhook secret).
- Manual approval: `POST /admin/orders/{id}/mark-paid` (Dashboard → **💸 Payments** → *Mark Paid*).

## Default plans (Settings → Plans JSON se change kar sakte ho)
| Plan | Price | Days | Endpoints |
|---|---|---|---|
| Trial | ₹29 | 3 | num-info, family |
| Number Pack | ₹100 | 30 | num-info, family, num |
| Vehicle Pack | ₹100 | 30 | vehicle-report, rc-info, challan |
| Aadhaar Pack | ₹150 | 30 | aadhaar-family, aadhaar, ration |
| Full Access | ₹299 | 30 | sab 59 |
| Reseller | ₹999 | 365 | reseller panel credit |

---

# 🌐 LANDING PAGE — API bechne ki website (NEW, bina coding ke)

Deploy hote hi **2 pages ready** milte hain:

- **`https://YOUR-URL.onrender.com/site`** (ya `/store`) — poora selling website
- **`https://YOUR-URL.onrender.com/dashboard`** — admin panel

Site me kya hai:
- Hero + plans grid (price, days, endpoints)
- Order form → UPI QR + amount + **order code** (remark me dalna hai)
- **Auto key delivery** — payment aate hi page khud key dikhata hai (8 second polling)
- Live demo box (Demo key se try karo)
- `/api/key-info` se "Check My Key"
- Reseller CTA + Telegram support button
- Footer: `⚡ Powered by @Supermannn_x`

Settings tab me badal sakte ho: **UPI ID, UPI name, Telegram support, Website title, Tagline, Plans JSON**.

---

# 🤖 ADVANCED TELEGRAM BOT (NEW)

File: `examples/telegram_bot_advanced.py`

Features:
- **Inline buttons menu** — /start dabao, sab kuch buttons se
- **`/status`** — subscription check (plan, expiry, days left, usage, device)
- **`/buy`** — plans inline → UPI QR → *"Paid — Check"* → **key mil jata hai bot me hi**
- Force subscribe (`FORCE_CHANNEL = "@yourchannel"`)
- Free text input (button dabane ke baad number/vehicle/etc. bhej do)

Chalane ka tarika:
```bash
pip install requests
python examples/telegram_bot_advanced.py
```
Config me sirf 3 cheezein: `BOT_TOKEN`, `API_BASE` (apna Render URL), `API_KEY`.

---

# 💾 BACKUP & RESTORE (Render free plan ke liye BAHUT zaroori)

⚠️ Render ke **free plan** me server ki disk **temporary** hoti hai — server restart / sleep /
redeploy hone par `osint_database.db` reset ho sakta hai, yani **keys, resellers, orders sab udd sakte hain**.

Isliye dashboard me naya **💾 Backup** tab hai:

| Kaam | Kaise |
|---|---|
| Backup lena | Dashboard → **💾 Backup** → **⬇️ Backup Download (JSON)** |
| Restore karna | Wahi tab → JSON paste karein (ya file choose karein) → **♻️ Restore** |

API se bhi:
```bash
curl -H 'x-admin-token: admin123' https://YOUR-URL.onrender.com/admin/backup -o backup.json
curl -X POST -H 'x-admin-token: admin123' -H 'Content-Type: application/json' \
     -d @backup.json https://YOUR-URL.onrender.com/admin/restore
```

**Permanent solution (optional):** Render **Starter $7/month** → 1GB persistent disk
→ mount path `/var/data` → Environment me `DB_PATH=/var/data/osint.db`. Phir data kabhi reset nahi hoga.

---

# ☁️ RENDER PE DEPLOY (naye GitHub account `himanshu74919-cpu` ke saath)

Repo: **https://github.com/himanshu74919-cpu/ToolVault-**

1. **render.com** kholo → **Sign in with GitHub**
2. GitHub login aaye to **`himanshu74919-cpu`** se login karo → **Authorize Render**
3. Dashboard → **New +** → **Blueprint**
4. Repo list me **`ToolVault-`** select karo → **Apply**
5. 2–4 minute me deploy ho jayega → URL: `https://osint-api-hub-XXXX.onrender.com`
6. **Environment** tab → `ADMIN_PASSWORD` = `admin123` (chahe to badal do), `UPI_ID` = apna UPI → **Save**
7. **UptimeRobot.com** pe free monitor: `https://YOUR-URL.onrender.com/health` (har 5 min)

Baad me:
- `/site` → aapki API-selling website
- `/dashboard` → admin panel (password `admin123`)
- `/dashboard → 💾 Backup` → roz ek backup download kar lein

---

# ✅ LIVE DEPLOYMENT (verified)

**URL: https://osint-api-hub.onrender.com**

| Check | Status |
|---|---|
| `/health` → 59 endpoints, `developer: @Supermannn_x` | ✅ 200 |
| `/` `/site` `/store` (landing page) | ✅ 200 |
| `/dashboard` (admin panel) | ✅ 200 |
| `/api/plans` (6 plans, UPI ID set) | ✅ 200 |
| `/api/num-info?key=Demo&q=9058390341` | ✅ 200 (~19s) |
| `/api/vehicle-report?key=Demo&q=BR30AR0802` | ✅ 200 (~5s) |
| `/api/aadhaar-family?key=Demo&aadhaar=861313813129` | ✅ 200 (~30s cold) |
| `/api/email-info`, `/api/pass-check` | ✅ 200 |
| `/api/youtube-download` (video 1080p mp4 + audio m4a) | ✅ 200 (~4s) |
| `/api/youtube-mp3` | ✅ 200 (~0.6s) |
| `/api/create-order` → UPI link + QR | ✅ 200 |

## ⬇️ YouTube download kaise kaam karta hai (cloud par)

Cloud/datacenter IP (Render free) se YouTube `yt-dlp` ko block kar deta hai
(`Failed to extract any player response`). Isliye endpoint ye order try karta hai:

1. **Invidious** (public instance, proxied links — kisi bhi device se chalte hain) ← cloud par yahi kaam karta hai
2. **Piped** (backup)
3. **yt-dlp** (agar server IP block na ho — best quality)
4. Upstream metadata (aakhri koshish)

Agar yt-dlp ek baar fail ho jaye to wo **15 minute ke liye skip** ho jata hai, jisse
response time ~1-4 second rehta hai. `?debug=1` lagane par poora diagnostic dikhta hai:
```
/api/youtube-download?key=Demo&url=...&debug=1
```
⚠️ Ye third-party public instances par depend karta hai — kabhi-kabhi down ho sakte hain.
Isliye YouTube ko paid plan ka core feature banane ki jagah **bonus** rakhein.

---

# 🩺 UPTIMEROBOT "405 Method Not Allowed" — FIXED

**Problem:** UptimeRobot (aur kai uptime monitors) `HEAD` request bhejte hain, jabki FastAPI ke
`@app.get(...)` routes sirf `GET` allow karte the → server **405 Method Not Allowed** deta tha →
monitor me site hamesha **DOWN** dikh rahi thi (server asal me chal raha tha).

**Permanent fix (code me):** `HeadSupportMiddleware` — koi bhi `HEAD` request ko andar se `GET`
ki tarah handle karta hai aur body-khali jawab deta hai (`content-length: 0`), status code wahi
rehta hai (200 / 404 / 401 sab sahi). Isliye ab koi bhi monitor (HEAD ya GET) sahi status payega.

**UptimeRobot setting (recommended):**
- Monitor type: **HTTP(s)**
- URL: `https://YOUR-URL.onrender.com/health`   ← halka endpoint, tez reply
- Interval: 5 minute
