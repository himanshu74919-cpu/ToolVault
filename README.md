# 🛡️ OSINT & Breach Exposure Database API

Ye ek ready-to-use **OSINT & Breach Exposure API + SQLite Database + Web Control Panel** hai, jise aap bina coding knowledge ke seedha apne **Samsung Galaxy Tab A9+ 5G** ke browser se chala sakte hain aur Cloud (Render / Railway / Koyeb) par **Free mein 24/7 Live** host kar sakte hain.

---

## ✨ Features (Isme Kya-Kya Hai?)

1. **🗄️ Custom OSINT & Breach Database (`/api/v1/db/search` & `/api/v1/db/records`)**
   - Aap Web Dashboard se bina kisi code ke naye records (Email, Username, Domain, IP, Source, Exposed Data) add, search aur delete kar sakte hain.
   - Bulk JSON import endpoint (`/api/v1/db/bulk`) bhi maujood hai.
2. **📧 Email Breach Exposure Check (`/api/v1/breach/email?email=...`)**
   - Ye endpoint kisi bhi email ko aapke **Local SQLite Database** + **Public Breach Intelligence (XposedOrNot API)** dono mein check karta hai aur batata hai ki email kin-kin breaches mein expose hua hai.
3. **🔐 Leaked Password Check (`/api/v1/breach/password?password=...`)**
   - **HaveIBeenPwned k-Anonymity SHA-1 Range API** ka use karke safely check karta hai ki koi password public leaks mein kitni baar expose ho chuka hai (bina pura password internet par bheje).
4. **🌐 Domain & IP Reconnaissance (`/api/v1/osint/target?target=...`)**
   - Domain DNS resolution, IP Geolocation, ISP, ASN, Hosting detection aur Local Database match ek saath deta hai.
5. **📱 Tablet-Friendly Dashboard (`/`) & Auto Docs (`/docs`)**
   - Browser mein kholte hi Dark-Mode Dashboard milta hai jahan se aap sab kuch button click karke chala sakte hain.

---

## 🚀 Samsung Galaxy Tab A9+ 5G se Free Cloud Hosting Kaise Karein?

Jab ye code aapke **GitHub Repository** par push ho jaye:

1. Apne Tablet ke Chrome browser mein **[render.com](https://render.com)** kholein aur **Sign in with GitHub** karein.
2. **New +** button dabayein aur **Web Service** (ya **Blueprint**) select karein.
3. Apna GitHub repository (`osint-breach-database-api`) connect karein.
4. **Free Plan** select karein aur **Deploy Web Service** par click kar dein.
5. 2 minute mein aapko ek live URL mil jayega (jaise `https://your-osint-api.onrender.com`)!
