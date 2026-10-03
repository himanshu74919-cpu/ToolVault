# OSINT API Hub — poora endpoint check (live)

**Hub:** https://osint-api-hub.onrender.com  ·  **Version:** 2.6.8  ·  **Time:** 03-10-2026 23:10  ·  **Total:** 58 endpoints

| Result | Kitne | Matlab |
|---|---|---|
| ✅ OK | 35 | Poora data aa raha hai |
| ⚠️ PARTIAL | 0 | Native data chal raha hai, live/upstream nahi (key chahiye) |
| 🔑 PROVIDER | 14 | Aapki licensed API lagane par live ho jayenge |
| 🔒 POLICY | 9 | Jaan-boojh kar band (leaked personal data — illegal) |
| ❌ BROKEN | 0 | Kuch toota hua (agar 0 hai to sab theek) |

---

| Endpoint | Result | HTTP | Time | Note |
|---|---|---|---|---|
| `ai-gf` | ✅ OK | 200 | 1.3s |  |
| `bgmi` | ✅ OK | 200 | 0.3s |  |
| `country` | ✅ OK | 200 | 2.8s |  |
| `device-specs` | ✅ OK | 200 | 1.3s |  |
| `github` | ✅ OK | 200 | 3.4s |  |
| `gst-direct` | ✅ OK | 200 | 0.3s |  |
| `gst-info` | ✅ OK | 200 | 0.3s |  |
| `gst-info-v2` | ✅ OK | 200 | 0.3s |  |
| `gst-search` | ✅ OK | 200 | 0.3s |  |
| `ifsc` | ✅ OK | 200 | 1.5s |  |
| `image-to-prompt` | ✅ OK | 200 | 1.0s |  |
| `imei` | ✅ OK | 200 | 1.7s |  |
| `instagram-posts` | ✅ OK | 200 | 1.8s |  |
| `instagram-profile` | ✅ OK | 200 | 4.3s |  |
| `ip-v1` | ✅ OK | 200 | 2.8s |  |
| `ip-v2` | ✅ OK | 200 | 1.8s |  |
| `ip-v3` | ✅ OK | 200 | 2.7s |  |
| `pan-info` | ✅ OK | 200 | 0.3s |  |
| `pan-to-gst` | ✅ OK | 200 | 0.2s |  |
| `pan-to-gst-v2` | ✅ OK | 200 | 0.3s |  |
| `pan-to-gst-v3` | ✅ OK | 200 | 0.3s |  |
| `pan-to-gst-v4` | ✅ OK | 200 | 0.6s |  |
| `pass-check` | ✅ OK | 200 | 1.5s |  |
| `pincode` | ✅ OK | 200 | 2.0s |  |
| `snap-highlights` | ✅ OK | 200 | 3.3s |  |
| `snap-stories` | ✅ OK | 200 | 4.5s |  |
| `song` | ✅ OK | 200 | 0.5s |  |
| `terabox-file` | ✅ OK | 200 | 1.7s |  |
| `terabox-stream` | ✅ OK | 200 | 1.0s |  |
| `terabox-stream-v2` | ✅ OK | 200 | 0.9s |  |
| `terabox-stream-v3` | ✅ OK | 200 | 0.3s |  |
| `youtube-all` | ✅ OK | 200 | 0.9s |  |
| `youtube-info` | ✅ OK | 200 | 0.8s |  |
| `youtube-info-id` | ✅ OK | 200 | 0.5s |  |
| `youtube-mp3` | ✅ OK | 200 | 12.9s |  |
| `num` | 🔑 PROVIDER-CHAHIYE | 410 | 0.2s | Number info: LEGAL carrier lookup (operator/circle/type/MNP) ke liye apni API lagao — NUMI |
| `num-info` | 🔑 PROVIDER-CHAHIYE | 410 | 0.4s | Number info: LEGAL carrier lookup (operator/circle/type/MNP) ke liye apni API lagao — NUMI |
| `number-info` | 🔑 PROVIDER-CHAHIYE | 410 | 0.3s | Number info: LEGAL carrier lookup (operator/circle/type/MNP) ke liye apni API lagao — NUMI |
| `rc-info` | 🔑 PROVIDER-CHAHIYE | 410 | 0.2s | Live vehicle/owner/challan lookup disabled hai jab tak authorized provider configure na ho |
| `vehicle-challan` | 🔑 PROVIDER-CHAHIYE | 410 | 0.3s | Live vehicle/owner/challan lookup disabled hai jab tak authorized provider configure na ho |
| `vehicle-challan-v2` | 🔑 PROVIDER-CHAHIYE | 410 | 0.4s | Live vehicle/owner/challan lookup disabled hai jab tak authorized provider configure na ho |
| `vehicle-challan-v4` | 🔑 PROVIDER-CHAHIYE | 410 | 0.3s | Live vehicle/owner/challan lookup disabled hai jab tak authorized provider configure na ho |
| `vehicle-details` | 🔑 PROVIDER-CHAHIYE | 410 | 0.2s | Live vehicle/owner/challan lookup disabled hai jab tak authorized provider configure na ho |
| `vehicle-full` | 🔑 PROVIDER-CHAHIYE | 410 | 0.6s | Live vehicle/owner/challan lookup disabled hai jab tak authorized provider configure na ho |
| `vehicle-info` | 🔑 PROVIDER-CHAHIYE | 410 | 0.2s | Live vehicle/owner/challan lookup disabled hai jab tak authorized provider configure na ho |
| `vehicle-info-v2` | 🔑 PROVIDER-CHAHIYE | 410 | 0.6s | Live vehicle/owner/challan lookup disabled hai jab tak authorized provider configure na ho |
| `vehicle-rc` | 🔑 PROVIDER-CHAHIYE | 410 | 0.3s | Live vehicle/owner/challan lookup disabled hai jab tak authorized provider configure na ho |
| `vehicle-report` | 🔑 PROVIDER-CHAHIYE | 410 | 0.6s | Live vehicle/owner/challan lookup disabled hai jab tak authorized provider configure na ho |
| `vehicle-v` | 🔑 PROVIDER-CHAHIYE | 410 | 0.3s | Live vehicle/owner/challan lookup disabled hai jab tak authorized provider configure na ho |
| `aadhaar` | 🔒 POLICY-SE-BAND | 410 | 0.3s | Aadhaar/family lookup yahan available nahi. UIDAI/NFSA ke official consent-based portal ka |
| `aadhaar-family` | 🔒 POLICY-SE-BAND | 410 | 0.4s | Aadhaar/family lookup yahan available nahi. UIDAI/NFSA ke official consent-based portal ka |
| `email` | 🔒 POLICY-SE-BAND | 410 | 0.2s | Leaked personal-record lookup yahan supported nahi. Sirf non-sensitive phone metadata aur  |
| `email-info` | 🔒 POLICY-SE-BAND | 410 | 0.2s | Leaked personal-record lookup yahan supported nahi. Sirf non-sensitive phone metadata aur  |
| `family` | 🔒 POLICY-SE-BAND | 410 | 0.2s | Leaked personal-record lookup yahan supported nahi. Sirf non-sensitive phone metadata aur  |
| `leak-v1` | 🔒 POLICY-SE-BAND | 410 | 0.4s | Leaked personal-record lookup yahan supported nahi. Sirf non-sensitive phone metadata aur  |
| `leak-v2` | 🔒 POLICY-SE-BAND | 410 | 0.4s | Leaked personal-record lookup yahan supported nahi. Sirf non-sensitive phone metadata aur  |
| `num-family` | 🔒 POLICY-SE-BAND | 410 | 0.2s | Leaked personal-record lookup yahan supported nahi. Sirf non-sensitive phone metadata aur  |
| `ration` | 🔒 POLICY-SE-BAND | 410 | 0.3s | Aadhaar/family lookup yahan available nahi. UIDAI/NFSA ke official consent-based portal ka |

---

## Sabse dheele (jinke liye provider/key chahiye)
Youtube-download / ytdl: pehli call 19-23s (free public providers), cached hone ke baad 1-3s.
Baaki sab 0-7s me jawab de rahe hain.