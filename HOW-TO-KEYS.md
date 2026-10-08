# OSINT HUB — keys kaise lagte hain (v2.8.6)

Yeh file sirf aapke liye likhi gayi hai — hub ke 3 "aadhe-khali" tools (📲 Carrier,
🧾 GST real data, 🚗 Vehicle) ko live karne ka poora tarika, aur ye ki maine kya theek kiya.

Koi bhi cheez Render dashboard me badalne ki zaroorat **nahi** hai — sab kuch
hub ke apne dashboard (Settings) se ho jayega.

---

## 1) Maine kya theek kiya

| Problem (pehle) | Ab (v2.8.5 / v2.8.6) |
|---|---|
| Code me **doosre aadmi ka demo hub** (`osint-apis-hub.onrender.com`) default `UPSTREAM_BASE` tha. Matlab aapke users ke GSTIN/phone/vehicle numbers bina aapse poochhe us anjaan server par jaate the. | Default ab **khaali**. Base set na ho to hub kisi bahar **kuch bhi nahi bhejta** — sirf apna native data use karta hai. (Privacy by default.) |
| Aapki saved key 7 character ki thi (`GST/xxxxx` jaisa kachra). Purana checker sirf 3 exact words ("", "Demo", "KEY") jaanta tha, isliye wo kachra "asli key" lagti thi → har request par 45-second wait ya `Invalid API key`, aur `/health` me **558 skipped calls**. | `upstream_key_is_placeholder()` ab **lambai + format** dekhta hai: 12 se chhoti key, ya `/` `:` khaali-jagah wala kachra = placeholder → call hoti hi nahi, isliye wait bhi nahi, ghanta bhi nahi. |
| Ek baar key invalid ho to har 15 minute baad dobara try hota tha (cold Render instance jaagta rehta tha). | 3 baar lagataar invalid = us process me upstream **auto-off**. `/admin/upstream/status` ab `auto_off` + `consecutive_fails` saaf-saaf batata hai. |
| Provider keys sirf **Render environment variables** se padhi jaati thi — aapke paas us service ke Render settings tak pahunch nahi thi, isliye "on nahi kar pa raha" wali halat thi. | Ab **env ke saath-saath dashboard → Settings bhi chalta hai** (`numinfo_provider_key`, `gst_provider_key`, `vehicle_provider_url` …). `/admin/env/status` bhi ab settings-gira hua value "set ✅" dikhata hai. |
| `/health` ka note adhoora sach likhta tha. | Note ab 4 haalaat alag-alag likhta hai: *manually OFF* / *auto-off (3 fails)* / *base set nahi* / *key invalid* / *ok*. |

Aur hub ke tests: `tests/test_upstream_privacy.py` — 13 naye offline checks
(poora suite: **20 passed**, koi network call nahi).

---

## 2) Dashboard kholna (bas 2 step)

1. Browser me kholo: `https://osint-api-hub.onrender.com/admin`
2. Password daalo (wahi jo aapne mujhe diya tha).

> ⚠️ **Turant ek kaam karo:** wo password chat me aa gaya tha, isliye use badal do.
> Settings me `admin_password` ka field hai — naya password daalo → Save.
> (Naya password mujhe mat bhejo, khud rakh lo.)

---

## 3) 📲 Carrier / Number Info live karna (FREE — 5 minute)

1. `https://www.numverify.com/` kholo → **Sign Up** (email se, card maangta nahi)
2. Login ke baad dashboard par **`access key`** dikhega (32 character jaisa) → copy karo
3. Hub ke dashboard → **Settings** me ye 4 fields daalo:

| Field | Value |
|---|---|
| `numinfo_provider_url` | `https://apilayer.net/api/validate` |
| `numinfo_provider_key` | <span>vahi 32-character access key</span> |
| `numinfo_provider_auth` | `query` |
| `numinfo_provider_key_param` | `access_key` |

4. **Save** dabao → 1 minute me `/health` ke `providers.carrier` me `numverify.com ✅` dikhne chahiye.
5. Free tier: **100 query/mahina**. Khatam ho jaye to hub apne aap native (free) data par wapas aa jayega — bot nahi tuta.

---

## 4) 🧾 GST real data live karna (FREE — 5 minute)

1. `https://gstinapi.in/` → signup (free me **100 GSTIN check/mahina**)
2. Wahan se mila hua key copy karo
3. Hub dashboard → Settings:

| Field | Value |
|---|---|
| `gst_provider_key` | <span>gstinapi.in ka key</span> |
| (agar wo URL alag pooche) `gst_provider_url` | `https://gstinapi.in/gst` |

4. Save → `/health` me `providers.gst: on ✅`.

> Hub me GST ka **native** check (public data se format + status) ab bhi chalta rehta hai;
> ye key lagne par full firm-name / address / return-date milne lagegi.

---

## 5) 🚗 Vehicle / VAHAN — yahan main jaan-boojh kar "official links" rakhta hoon

Sarkar ke portals (VAHAN, e-Challan, Sarathi, IIB) par **captcha + login** hai aur
unke apne paid API plans hain (`parivahan` paid API, `digiLocker` jaise). Bina paid
contract ke wahan se data nikalna = TOS todna + aapka IP band. Isliye hub:

- number daalne par **sahi official link** + step-by-step tarika deta hai (ye already live hai),
- agar aap paid VAHAN/RC provider ka key le lo, to hub ke dashboard me bas
  `vehicle_provider_url` + `vehicle_provider_key` (+ `vehicle_provider_auth`,
  `vehicle_provider_key_param`, `vehicle_provider_param`) daal do — tool khud chal padega.

---

## 6) "Upstream" ka matlab (aur kyun abhi OFF rakha hai)

`UPSTREAM_BASE` + `UPSTREAM_KEY` = kisi **doosre API server** se data mangwana.
Aapke hub me `upstream_enabled=1` chala raha tha, key kachri thi, aur base ek
**teesre aadmi ka demo hub** tha → 558 failed/skipped calls. Maine upstream **OFF**
kar diya hai (`upstream_enabled=0`, `upstream_key=""`).

Chalu karna ho to (dashboard → Settings):
1. `upstream_base` = `https://` se shuru poora address (jiska key aapke paas asli ho)
2. `upstream_key` = uski asli key (12+ character)
3. `upstream_enabled` = `1`
4. Test: `https://osint-api-hub.onrender.com/admin/upstream/test` (browser me kholo,
   `?token=<aapka admin password>`) — answer me `key_ok: true` aana chahiye.

Khali chhodna = sabse safe: hub sirf apna data use karega, koi number/GSTIN bahar nahi jayega.

---

## 7) `/health` kaise padhein

`https://osint-api-hub.onrender.com/health`

- `upstream.key_ok` → `null` ka matlab "key lagi hi nahi" (bura nahi), `false` = galat lagi hai
- `upstream.skipped_calls` → ab **0** rahega (skip = bachat, error nahi)
- `upstream.auto_off` → `true` matlab 3 baar fail hokar band hua (key theek karo, phir restart/auto)
- `providers.*` → `off (paid/free-tier API chahiye)` matlab aapko key nahi mili, code theek hai
- `persistence.github_backup` → `on → …/hub-db-backup` = aapka DB roz backup ho raha hai ✅

---

## 8) Ek optional suggestion

Aapke hub me `demo_key_enabled=1` + `DEMO_KEY` set hai — yaani **koi bhi** aapke hub
ko free me use kar sakta hai (aapki 120/min rate limit ke andar). Agar aap keys bech
nahi rahe, to ye on rakhte hue bhi `rate_limit_per_min` kam kar sakte ho; aur agar
bilkul nahi chahiye to Settings me `demo_key_enabled = 0` kar do. (Aapki marzi —
maine chheda nahi hai.)
