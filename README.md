# 🛰️ OSINT & Multi-Utility API Hub (43 Endpoints + SQLite Database + Dashboard)

Ye ek **ready-to-use API Hub** hai jo `https://osint-apis-hub.onrender.com` jaisi hi endpoint
structure follow karta hai, lekin isme extra milta hai:

* ✅ **43 endpoints** – `?key=Demo` ya aapki khud ki API key se
* ✅ **SQLite database** – apna khud ka data (leak records, phone, GST, PAN, vehicle…) dashboard se add karein
* ✅ **Web Dashboard** (`/dashboard`) – tablet/mobile friendly, bina coding ke sab kuch control
* ✅ **Smart fallback** – har endpoint pehle free native source try karta hai, agar poori data
  nahi milti to upstream (osint-apis-hub) se merge kar leta hai. Ek source down hone par doosra
  version automatically try hota hai.
* ✅ **Response caching** – same query dobara aane par turant result (SQLite cache)
* ✅ **API key management** – naye keys banao, disable karo, usage dekho
* ✅ **Free cloud hosting** – Render / Railway / Koyeb / Docker, kahin bhi

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

## 📋 Pure 43 Endpoints (Base URL ke saath)

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
| 🔌 Endpoints | 43 endpoints ki list, search, category filter, live test + response + Copy URL |
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
