# Quality Changer Bot (Bunny Stream + Telegram)

Bot ko video bhejo → ye Bunny Stream pe upload karta hai → encoding complete hone
ka wait karta hai → phir har available quality (240p, 360p, 480p, 720p...) ki
MP4 file download karke wapas Telegram pe bhej deta hai.

## Files

- `bot.py` — Pyrogram bot logic (message handlers)
- `bunny_client.py` — Bunny Stream API wrapper (upload / poll / download)
- `config.py` — environment variables loader
- `requirements.txt` — dependencies
- `.env.example` — copy this to `.env` aur apni values daalo
- `Procfile` — Railway ke liye worker process

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

3. **Bunny Library settings check karo:**
   Stream → Library → **Encoding** tab mein:
   - Jo resolutions chahiye woh enable karo
   - **MP4 Fallback** zaroor ON karo — iske bina download URLs kaam nahi karenge
   - Yaad rakho: MP4 fallback normally 720p tak hi milta hai jab tak sirf ek hi
     high resolution enable na ho

4. **Run karo:**
   ```
   python bot.py
   ```

## Railway pe deploy

- Repo/files push karo
- Environment variables Railway dashboard mein set karo (same as `.env`)
- `Procfile` already `worker: python bot.py` bata deta hai kya run karna hai

## Kaise kaam karta hai

1. User bot ko koi video/document bhejta hai
2. Bot Telegram se file download karta hai
3. Bunny Stream pe upload karta hai (`create_video` + `upload_video`)
4. Local file delete kar deta hai (disk space bachane ke liye)
5. Har `POLL_INTERVAL_SECONDS` par Bunny se status check karta hai jab tak
   encoding `Finished` na ho jaaye
6. `availableResolutions` se pata chalta hai kaunsi qualities ready hain
7. Har quality ki MP4 URL banata hai: `https://{pull_zone}/{video_id}/play_{res}.mp4`
8. Ek-ek karke download karke Telegram pe bhejta hai, phir local copy delete

## Config options (`.env` mein)

| Variable | Matlab |
|---|---|
| `WANTED_RESOLUTIONS` | Sirf specific qualities bhejni hain toh comma-separated list (e.g. `360p,480p,720p`). Khali chhodo to sab bhej dega. |
| `DELETE_FROM_BUNNY_AFTER_SEND` | `true` karne se video Bunny se delete ho jayega saari qualities bhejne ke baad (storage credit bachega) |
| `POLL_INTERVAL_SECONDS` / `ENCODE_TIMEOUT_SECONDS` | Encoding status kitni der mein check kare, aur kitni der tak wait kare pehle timeout |

## Notes

- Pyrogram MTProto use karta hai, isliye 50MB Bot-API HTTP limit yaha apply
  nahi hoti — bade video files (jaise anime episodes) bhi bhej sakte ho.
- Agar `.env` mein galat Bunny credentials hue, upload step pe hi error aayega
  — pehle dashboard se manually ek video upload/download test kar chuke ho,
  toh values sahi hi honi chahiye.
