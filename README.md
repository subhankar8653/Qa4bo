# Quality Changer Bot (Bunny Stream + Telegram)

Bot ko video/file ya http(s) link bhejo → ye pehle usse ek link mein convert
karta hai (agar file/video bheja ho) → phir Bunny Stream us link se video
fetch karta hai → encoding complete hone ka wait karta hai → phir har
available quality (240p, 360p, 480p, 720p...) ki MP4 file download karke
wapas Telegram pe bhej deta hai.

## Files

- `bot.py` — Pyrogram bot logic (message handlers) + bot/web server ka combined entrypoint
- `bunny_client.py` — Bunny Stream API wrapper (upload / poll / download)
- `streamer.py` — File-to-Link streaming server (aiohttp), Telegram files ko HTTP link se serve karta hai
- `db.py` — MongoDB (Motor) layer, file→link mapping store karta hai
- `config.py` — environment variables loader
- `requirements.txt` — dependencies
- `.env.example` — copy this to `.env` aur apni values daalo
- `Procfile` — Railway ke liye **web** process (streaming server ke liye public port chahiye)

## Kaise kaam karta hai (naya flow)

1. User bot ko video/document bhejta hai
2. Bot Telegram se file download **nahi** karta — bas uska `file_id` +
   metadata MongoDB mein save kar deta hai aur ek link banata hai:
   `{BASE_URL}/dl/{file_unique_id}`
3. Ye link exactly waise hi process hota hai jaise koi seedha http(s) link
   bhejne par hota — quality selection UI aati hai
4. ▶️ Start dabane par Bunny Stream (`create_video_from_url`) seedha is link
   se video fetch kar leta hai (Bunny hamare server ko GET request karta hai,
   jo Telegram se real-time stream karke serve karta hai — Range/seek support
   ke saath)
5. Encoding poori hone tak poll karta hai (`POLL_INTERVAL_SECONDS`)
6. `availableResolutions` se pata chalta hai kaunsi qualities ready hain
7. Har selected quality download karke Telegram pe bhejta hai, phir local
   copy delete

Agar koi seedha http(s) link bhejta hai, wo pipeline same rehta hai (step 3
se seedha shuru).

## Setup

1. **Dependencies install karo:**
   ```
   pip install -r requirements.txt
   ```

2. **`.env` banao:**
   ```
   cp .env.example .env
   ```
   Aur ye values fill karo:
   - `API_ID`, `API_HASH` — https://my.telegram.org se
   - `BOT_TOKEN` — @BotFather se
   - `BUNNY_LIBRARY_ID`, `BUNNY_API_KEY`, `BUNNY_PULL_ZONE` — Bunny dashboard →
     Stream → apni Library → **API** tab se copy karo
   - `MONGO_URI` — MongoDB connection string (Atlas ya koi bhi Mongo instance)
   - `BASE_URL` — apna public domain (niche dekho)

3. **Bunny Library settings check karo:**
   Stream → Library → **Encoding** tab mein:
   - Jo resolutions chahiye woh enable karo
   - **MP4 Fallback** zaroor ON karo — iske bina download URLs kaam nahi karenge

4. **Run karo:**
   ```
   python bot.py
   ```
   Ye ek saath bot polling aur streaming HTTP server (`PORT` par) dono chalu
   kar dega.

## Railway pe deploy

- Repo/files push karo
- Is service ko **web** process banao (`Procfile` mein already `web: python bot.py` hai)
- Settings → Networking → **Generate Domain** karo, taaki public URL mile
  (kuch jaisa `xxxx.up.railway.app`)
- Us domain ko `https://` ke saath `BASE_URL` env var mein daal do
  (agar khali chhodoge, bot Railway ke `RAILWAY_PUBLIC_DOMAIN` env var se
  khud bana lene ki koshish karega, lekin explicit set karna safe hai)
- Baaki environment variables (same as `.env`) Railway dashboard mein set karo,
  including `MONGO_URI`

## Config options (`.env` mein)

| Variable | Matlab |
|---|---|
| `MONGO_URI` | MongoDB connection string — file→link feature ke liye zaroori |
| `DB_NAME` | Mongo database ka naam (default `qa4bo`) |
| `PORT` | Streaming server ka port (Railway khud deta hai, chhedo mat) |
| `BASE_URL` | Public URL jispe `/dl/...` links khulenge |
| `WANTED_RESOLUTIONS` | Sirf specific qualities bhejni hain toh comma-separated list (e.g. `360p,480p,720p`). Khali chhodo to sab bhej dega. |
| `DELETE_FROM_BUNNY_AFTER_SEND` | `true` karne se video Bunny se delete ho jayega saari qualities bhejne ke baad (storage credit bachega) |
| `POLL_INTERVAL_SECONDS` / `ENCODE_TIMEOUT_SECONDS` | Encoding status kitni der mein check kare, aur kitni der tak wait kare pehle timeout |

## Notes

- Pyrogram MTProto use karta hai, isliye 50MB Bot-API HTTP limit yaha apply
  nahi hoti — bade video files (jaise anime episodes) bhi bhej sakte ho.
- Streaming server bhi wahi bot client use karta hai (`app`) — koi extra
  session string ya userbot ki zaroorat nahi.
- Agar `.env` mein galat Bunny credentials hue, upload step pe hi error aayega
  — pehle dashboard se manually ek video upload/download test kar chuke ho,
  toh values sahi hi honi chahiye.
- Agar `MONGO_URI` ya `BASE_URL` missing hai, seedha http(s) link wala flow
  phir bhi kaam karega — sirf video/file → link conversion disable rahega
  (bot warn kar dega).

