# 🛰️ OSINT & Multi-Utility API Hub — **v2.5.11**

**60 endpoints** · SQLite database · web dashboard · API key system — sab kuch ek hi FastAPI app me.

Live: **https://osint-api-hub.onrender.com** · Health: `/health` · Docs: `/docs` · Dashboard: `/dashboard`

---

## 🚀 Kaise chalayein

```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port $PORT
```

Render par deploy karte waqt:
- **Build command:** `pip install -r requirements.txt`
- **Start command:** `uvicorn main:app --host 0.0.0.0 --port $PORT`
- Health check path: `/health`

---

## 🔑 API key

Har request me `?key=<API_KEY>` bhejna hota hai.

```
https://osint-api-hub.onrender.com/api/imei?key=Demo&imei=353010111111110
```

- Default/demo key: **`Demo`** (lifetime, ALL-ENDPOINTS plan, 120 req/min)
- Nayi key banane/band karne ke liye dashboard: `/dashboard` → Keys
- Key check: `/api/key-info?key=<KEY>`

---

## 🧰 Endpoints (60) — kaam ke hisaab se

| Group | Kya milta hai |
|---|---|
| 📲 **IMEI / TAC** | `imei`, `device-specs`, `tac` — 248,364 TAC rows ki local DB + nanoreview specs (photo + 11 sections) |
| 📱 **Number** | `num-info`, `num-report` — operator, circle, number type (leaked personal records jaan-boojh kar OFF) |
| 🏦 **Banking** | `ifsc`, `pincode`, `pan-to-gst`, `gst-search`, `gst-info` |
| 🌐 **Network** | `ip-v1`, `ip-v2`, `ip-v3`, `domain`, `dns` |
| 📥 **Download** | `youtube-download` (1080p), `ytdl`, `youtube-all`, `terabox-file`, `terabox-stream-v2/v3`, `song` |
| 📸 **Social** | `instagram-profile`, `instagram-posts`, `snap-stories`, `snap-highlights` |
| 🚗 **Vehicle** | `vehicle-*` — **policy ke wajah se OFF** (bot graceful message + official portal link deta hai) |
| 🛠️ **Admin** | `/admin/*` — keys, orders, records, logs, cache, backup |

Poori list live dekhne ke liye: **`/docs`** (Swagger) ya **`/api`** catalog.

---

## 🔌 Authorized provider lagana (vehicle + carrier) — v2.6

Jin endpoints par policy rok hai, unhe **aap apni licensed API** se live kar sakte ho.
Render → Environment me bas ye 2-6 line daalo:

### 🚗 Vehicle / RC / Challan
```
VEHICLE_PROVIDER_URL    = https://provider.example/api/vehicle   (aapki API ka endpoint)
VEHICLE_PROVIDER_KEY    = aapki key
VEHICLE_PROVIDER_PARAM  = number          (query param ka naam — default "number")
VEHICLE_PROVIDER_HEADER = Authorization   (header ka naam — default "Authorization")
VEHICLE_PROVIDER_AUTH   = bearer | key | header | query
```
Iske baad `/api/vehicle-rc`, `/api/vehicle-challan`, `/api/vehicle-challan-v4`,
`/api/vehicle-report` sab **live data** denge — aur aapke bot me
**🚗 VEHICLE INFO + CHALLAN** tool turant poora report dikhayega (maker, model, fuel,
owner, RTO, insurance, PUC, fitness aur saare challans amount/offence ke saath).

Provider ka JSON shape koi bhi ho — hub khud map kar leta hai
(`reg_no` / `registration_number` / `rc_number` … sab chalta hai).

### 📱 Number Info (carrier — legal)
```
NUMINFO_PROVIDER_URL = https://provider.example/api/hlr
NUMINFO_PROVIDER_KEY = aapki key
```
Isse **operator / circle / number type / MNP ported** live aata hai.
⚠️ Leaked personal records (naam-pata wala data) **jaan-boojh kar support nahi** —
wo illegal hai aur bot ban ho jata hai.

### Status check
```
GET /health   →  "providers": {"vehicle": "on/off", "carrier": "on/off"}
```

---

## 🗄️ Database

- **SQLite** — apna data (records, keys, orders, logs) dashboard se add karo, coding ki zaroorat nahi.
- IMEI ke liye ready-made TAC database `data/tac_full.csv` me hai — startup par SQLite index ban jata hai.
- 132 popular phones ki specs `data/specs_seed.json` me — Render ka IP block ho to bhi full details aate hain.

---

## ✅ Push se pehle check

```bash
python3 precheck.py       # import + 60 endpoints + 40 native handlers + duplicate paths
python3 -m pytest tests/ -q
```

Dono green hone par hi push karo — `precheck.py` push guard hai.

---

## 📝 Notes

- IMEI privacy: response me IMEI ke sirf **pehle 8 digit** dikhte hain, poora number nahi.
- Jin endpoints par legal/policy rok hai (vehicle, leaked records, aadhaar, email) wo **jaan-boojh kar disabled** hain — 410 + saaf Hinglish message dete hain.
- Har response me `_meta` aata hai (endpoint, source, server time, version) — debugging aasan.

---

## 🔑 APNI API KEY KAISE LE (dashboard se)

1. Kholo **https://osint-api-hub.onrender.com/dashboard**
2. Password daalo (jo aapne Render me `ADMIN_PASSWORD` env me rakha hai; default `admin123`)
3. **API Keys** tab → **+ New Key** → naam likho → **Create**
4. Key copy karo (jaise `osint-xxxxxxxx`) aur use karo:
   `https://osint-api-hub.onrender.com/api/ip-v2?key=osint-xxxxxxxx&ip=8.8.8.8`

⚠️ **Ye key ab bhi restart par udd sakti hai** (Render free plan ka ephemeral disk) —
permanent karne ke liye `MASTER_API_KEY` env lagao (upar dekho).

### Kaun-kaun se endpoints live hain
`ENDPOINT-REPORT.md` file dekho — poore live test ka result:
35 OK · 11 par aapki licensed API lagane se live · 12 policy se band (leaked personal data) · **0 broken**.

---

## 💾 PERMANENCE (v2.6) — API keys kabhi na ude

⚠️ **Render free plan par filesystem ephemeral hai** — service restart / spin-down / redeploy
hote hi `osint_database.db` **ud jati hai**. Isliye banayi gayi keys, records aur settings
gayab ho jate hain (yehi wajah hai ki key baar-baar "Invalid API key" deti thi).

### Ilaaj — 3 env vars (Render → Environment)
```
MASTER_API_KEY        = aapki permanent key (comma se multiple: key1,key2)
GITHUB_BACKUP_REPO    = owner/repo           # PRIVATE repo, jaise himanshu74919-cpu/hub-db-backup
GITHUB_BACKUP_TOKEN   = ghp_xxx              # us repo ka token (repo scope)
GITHUB_BACKUP_MINUTES = 15                   # optional, default 15
```
* `MASTER_API_KEY` wali key **DB ke bina bhi** chalti hai — restart par bhi kabhi nahi marte.
* `GITHUB_BACKUP_REPO` lagane par hub **boot par DB wapas laata hai** aur har 15 min me
  chupke se backup karta hai → keys, records, settings **sab bach jate hain**.
* Manual control: `POST /admin/backup/github` (abhi backup) · `POST /admin/restore/github` (wapas lao)
* Status: `GET /health` → `persistence` block.


### ⚡ Env hamesha jeetegi (v2.6.4)
Render → Environment me jo value set hai **wahi final** hai. Boot par wo value database me
bhi likh di jati hai, isliye purani (galat) DB value kabhi env ko nahi dabaayegi.
Ye lagta hai: `UPSTREAM_BASE`, `UPSTREAM_KEY`, `UPSTREAM_ENABLED`, `ADMIN_PASSWORD`,
`BRAND_TAG`, `UPI_ID`, `UPI_NAME`, `WEBHOOK_SECRET`, `GITHUB_TOKEN`, `HIBP_API_KEY`,
`CACHE_TTL`, `RATE_LIMIT_PER_MIN`, `MAX_REQUEST_SECONDS`, aur `SETTING_<NAAM>` (koi bhi setting).

### Settings bhi permanent
`SETTING_<NAAM>` env se koi bhi dashboard setting lock kar sakte ho, jaise:
```
SETTING_UPSTREAM_KEY = aapki-asli-upstream-key
SETTING_DEMO_KEY_ENABLED = 0
```
