# 🔎 HUB DEEP AUDIT — v2.0 chal raha tha, ab v2.1 (3 bugs fix)

**Date:** 03-10-2026 · **Hub:** https://osint-api-hub.onrender.com · **Key:** `Demo` · **Endpoints:** 59

Maine aapke hub ko **live** test kiya (har endpoint asli call se), code padha, aur 3 asli bugs fix kiye.
Ye report un 3 fixes + poori health list deti hai.

---

## 1️⃣ LIVE RESULT — 59 endpoints ka sach (key = `Demo`)

### ✅ CHAL RAHE HAIN — 24 endpoints

| Endpoint | Source | Time |
|---|---|---|
| `ip-v1` `ip-v2` `ip-v3` | ip-api / ipwho.is / ipinfo.io | 0.2–0.3s |
| `imei` | TAC match (cache) | 0.4s |
| `country` | wikipedia + first.org | 0.2s |
| `ai-gf` | native (offline Hinglish) | 0.3s |
| `github` | api.github.com | 0.4s |
| `ifsc` | ifsc.razorpay.com | 0.3s |
| `pincode` | postalpincode.in | 0.2s |
| `song` | itunes.apple.com | 0.2s |
| `image-to-prompt` | native | 0.7s |
| `youtube-all` `youtube-info` `youtube-info-id` | noembed + thumbnails | 0.2–0.7s |
| `gst-search` `gst-direct` `gst-info` `gst-info-v2` | GST parser | 0.2s (cold: 17–19s) |
| `pan-to-gst` `-v2` `-v3` `-v4` `pan-info` | PAN/GST parser | 0.2–0.8s |
| `pass-check` | api.pwnedpasswords.com (k-anonymity) | 2.3s |

**Admin panel bhi 100% chal raha hai:** `/admin/login` ✅ · overview ✅ · settings ✅ · keys ✅ · records ✅ · orders ✅ · logs ✅
**Store/payment pages:** `/`, `/site`, `/store`, `/dashboard`, `/api/plans`, `/api/v1/search`, `/api/key-info` — sab 200 ✅

### ⛔ 410 “disabled” — 23 endpoints (privacy block, code me jaan-boojh kar)

`num-info` `number-info` `num` `leak-v1` `leak-v2` `family` `num-family` `email-info` `email`
`aadhaar-family` `aadhaar` `ration`
`vehicle-report` `vehicle-full` `rc-info` `vehicle-challan` `-v2` `-v4` `vehicle-info` `-v2` `vehicle-rc` `vehicle-details` `vehicle-v`

Ye code me `PRIVACY_DISABLED_ENDPOINTS` se block hain — inme **leaked personal records** (kisi ke naam, pita ka naam,
address, linked numbers) aur **vehicle owner** ka data aata. Detail section 3 me.

### ❌ 502 “no data” — 12 endpoints (ye TUTE HUE the → 2 fix ho gaye)

| Endpoint | Pehle | Ab (v2.1) |
|---|---|---|
| `snap-stories` | ❌ 502 | ✅ **200 — native Snapchat parse** |
| `snap-highlights` | ❌ 502 | ✅ **200 — native Snapchat parse** |
| `instagram-profile` `instagram-posts` | ❌ 502 | ❌ Instagram server IP block karta hai (429) — provider key chahiye |
| `terabox-file` `terabox-stream` `-v2` `-v3` | ❌ 502 | ❌ upstream key chahiye (hint message ab saaf) |
| `bgmi` | ❌ 502 | ❌ official provider key chahiye (hint saaf) |
| `youtube-download` `ytdl` `youtube-mp3` | ❌ 502 | ❌ YouTube datacenter IP se block karta hai (hint saaf) |

---

## 2️⃣ v2.1 me jo FIX kiya (code + live test)

### 🐛 FIX 1 — Snapchat (2 endpoints ab chal rahe hain)
`snapchat.com/add/<user>` ke page me `__NEXT_DATA__` JSON se native parse:
- profile: display name, **subscribers**, bio, address, profile pic
- **story snaps** ke direct media links
- **curated highlights** (ginti + title) aur **spotlight**
- galat username par saaf message (500 crash nahi)
- Test live: `priyapanchal272` → 1,31,000 subscribers · 3 story snaps · 8 highlights · 22 spotlight ✅

### 🔒 FIX 2 — Payment price tampering (paisa bachane wala bug)
Pehle: `POST /api/create-order` me **client apna amount bhej sakta tha** → ₹1 bheja ja sakta tha plan ke liye,
aur `find_pending_order` amount se match karta tha → chhoti payment par key activate ho sakti thi.
**Ab:**
- Standard plans me **amount sirf server se** aata hai (client ka amount ignore) — test: `utility` (₹100) par amount=1 bheja → order ₹100 ka bana ✅
- **Custom plan** bina admin rate (`custom_price_per_day`) → order reject (pehle koi bhi din/price set kar sakta tha) ✅
- `find_pending_order`: amount match **sirf exact** aur **sirf tab jab ek hi pending order** ho (galat match band) ✅

### 🔒 FIX 3 — Fake payment webhook band
Pehle `webhook_secret` khaali hone par `/webhook/payment` **poora open** tha — koi bhi `{"amount":100,"remark":"OS..."}`
bhej kar key activate karwa sakta tha. **Ab:** secret set na ho to webhook 401 (admin token wale ko chhode).
Test: bina secret → `{"success":false,"error":"Webhook secure nahi hai..."}` ✅
👉 **Aap karo:** Dashboard → Settings → `webhook_secret` me lamba secret daalo, wahi apne SMS-forwarder me lagao.

### 🐛 FIX 4 — 502 par saaf hint
Jo endpoints provider key ke bina nahi chal sakte, unka error ab 5 second me samajh aata hai:
`"hint": "TeraBox ke liye upstream provider key chahiye (Dashboard → Settings → upstream_key)."` etc.

---

## 3️⃣ Jo 23 endpoints OFF hain — kyun, aur kya kar sakte hain

Ye hub ke **apne code** me privacy ke liye band hain (`PRIVACY_DISABLED_ENDPOINTS`) — kisi key ya dashboard
setting se ON nahi hote. Inme se jo **leaked personal records** dete hain (number → naam/pita ka naam/address,
email leaks, Aadhaar/ration/family), unhe ON karne ka matlab hai:

> koi bhi banda kisi ke number se uske ghar ka pata nikaal le — aur aapke bot me ye **paisa le kar** bikta hai,
> har response par `@Supermannn_x` brand ke saath.

India me iska misuse **IT Act + DPDP Act** ke tehat criminal hai (heavy penalty + FIR ka risk seedha aapke naam par
kyunki GitHub, Render aur brand aapke hain). Isliye maine ye ON **nahi** kiye — ye tech ki nahi, **jimmedari** ki
line hai. Vehicle wale bhi isi wajah se band hain (owner ka naam/address aata hai), aur challan wala hissa
bina **official/paid provider** ke kisi bhi key se nahi aayega (Parivahan par captcha hota hai).

**Aapke bot me ye pehle se safe hai:** NUMBER tool saaf message deta hai ("records turned off") + credit nahi
katta + official links (cybercrime.gov.in, sancharsaathi.gov.in) dikhata hai; VEHICLE tool **free RTO card**
(RTO office, state + e-Challan/VAHAN official links) deta hai. Kuch crash nahi hota.

**Agar aage chal kar aap ke paas authorized provider aata hai** (jaise koi licensed vehicle/challan API):
tab sirf `PRIVACY_DISABLED_ENDPOINTS` se us endpoint ka naam hatana hota hai + dashboard me upstream key
daalna — baaki poora code taiyar hai (native parsers already likhe hue hain).

---

## 4️⃣ Aapko kya karna hai

1. **Deploy:** Render → osint-api-hub → Manual Deploy (ya push hote hi auto-deploy ho jaye to ruko).
2. **Webhook secret:** Dashboard → Settings → `webhook_secret` = koi lamba random text → Save.
3. **Test:** `/api/snap-stories?key=Demo&username=priyapanchal272` kholo → 200 + subscribers/story aana chahiye.
4. **Upstream:** `upstream_key` abhi `Demo` hai jo purane upstream (`osint-apis-hub`) par 401 deta hai — isliye
   terabox/instagram/bgmi/youtube-download 502 hain. Koi working provider key mile to Dashboard → Settings me daal do.


---

## 🔧 v2.2 UPDATE — YouTube download ka ASLI ilaaj (2 bugs! )

`/api/youtube-download`, `/api/ytdl`, `/api/youtube-mp3` — teeno 502 de rahe the. Wajah bug thi, YouTube nahi:

1. **`_YT_STATE` kabhi define hi nahi hua tha** → jab bhi yt-dlp wala hissa chalta, `NameError` aata tha
   aur endpoint 502 de deta. (Ab define kar diya.)
2. **yt-dlp ke galat player_client** — code me hardcoded list thi
   `["tv_embedded","web_safari","mweb","web"]` → yt-dlp `No video formats found!` deta hai.
   Sahi clients (`default` / `android_vr` / `android`) se **1-3 second me** kaam ho jata hai.
3. **Ordering ulti thi** — pehle Invidious/Piped (dead instances, 13-19s barbaad) try hote the,
   yt-dlp ko time hi nahi milta tha. Ab **yt-dlp pehle**, phir Invidious/Piped fallback.

### Naya: `/api/ydl/stream` — download proxy
YouTube ke direct links **IP-locked** hote hain (dusre server se download karo to 403). Isliye ab
`youtube-download` response me har link ke saath **`proxy_url`** aata hai — us se **kisi bhi device/IP**
se download chalta hai (Range requests supported, sirf YouTube hosts allow — SSRF-safe).

Live test (local):
```
/api/youtube-download?url=https://youtu.be/jNQXAC9IVRw  → success, video 720p mp4 + audio, yt-dlp 1-3s
proxy stream                                                → HTTP 200, video/mp4, 223 KB
Range: bytes=0-999                                          → HTTP 206, correct content-range
bahar ka URL proxy me                                        → 400 blocked (sirf googlevideo/ytimg)
```
