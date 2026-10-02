# 🛰️ OSINT & Multi-Utility API Hub (53 Endpoints + SQLite Database + Dashboard)

Ye ek **ready-to-use API Hub** hai jo `https://osint-apis-hub.onrender.com` jaisi hi endpoint
structure follow karta hai, lekin isme extra milta hai:

* ✅ **53 endpoints** – `?key=Demo` ya aapki khud ki API key se
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

## 📋 Pure 53 Endpoints (Base URL ke saath)

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
| 🔌 Endpoints | 53 endpoints ki list, search, category filter, live test + response + Copy URL |
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
