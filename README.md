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
