import os
from dotenv import load_dotenv

load_dotenv()

# --- Telegram ---
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# --- Bunny Stream ---
BUNNY_LIBRARY_ID = os.getenv("BUNNY_LIBRARY_ID", "")
BUNNY_API_KEY = os.getenv("BUNNY_API_KEY", "")
# CDN / Pull Zone hostname, e.g. vz-abc123-456.b-cdn.net (from Stream > Library > API)
BUNNY_PULL_ZONE = os.getenv("BUNNY_PULL_ZONE", "")

# Comma separated list e.g. "360p,480p,720p" — leave empty to send every
# resolution Bunny reports as available for that video.
WANTED_RESOLUTIONS = [r.strip() for r in os.getenv("WANTED_RESOLUTIONS", "").split(",") if r.strip()]

DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "downloads")

# If true, deletes the video from Bunny Stream after all qualities are sent
# (saves storage credit, but you lose the Bunny-hosted copy).
DELETE_FROM_BUNNY_AFTER_SEND = os.getenv("DELETE_FROM_BUNNY_AFTER_SEND", "false").lower() == "true"

# How often (seconds) to poll Bunny for encoding status, and max wait time.
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "10"))
ENCODE_TIMEOUT_SECONDS = int(os.getenv("ENCODE_TIMEOUT_SECONDS", "1800"))

# --- MongoDB (file-to-link streaming server ke liye) ---
MONGO_URI = os.getenv("MONGO_URI", "")
DB_NAME = os.getenv("DB_NAME", "qa4bo")

# --- Streaming server (video -> link) ---
# Railway pe "web" process ke liye Railway khud PORT env var deta hai.
PORT = int(os.getenv("PORT", "8080"))

# Public base URL jispe ye bot se milne wale links khulenge, e.g.
# https://your-app.up.railway.app  (trailing slash mat lagana).
# Agar Railway pe domain generate karke BASE_URL manually set nahi kiya,
# to Railway ka apna injected RAILWAY_PUBLIC_DOMAIN use kar lenge (agar available ho).
_railway_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
BASE_URL = os.getenv("BASE_URL", "").rstrip("/") or (
    f"https://{_railway_domain}" if _railway_domain else ""
)

# Telegram file streaming chunk size (bytes) — 1MB Telegram ka standard part size hai.
STREAM_CHUNK_SIZE = 1024 * 1024
