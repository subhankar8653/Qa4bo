"""MongoDB layer — Telegram file <-> streaming link ka mapping store karta hai.

Har video/document jo bot ko milta hai, uska Telegram file reference
(chat_id + message_id + file_id + metadata) yaha ek document ke roop mein
save hota hai, `file_unique_id` ko primary key (_id) bana kar. Isi id se
`/dl/<id>` link banta hai jo streamer.py serve karta hai.
"""

import logging
from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorClient

from config import MONGO_URI, DB_NAME

log = logging.getLogger("db")

_client = AsyncIOMotorClient(MONGO_URI) if MONGO_URI else None
_db = _client[DB_NAME] if _client is not None else None
files_col = _db["files"] if _db is not None else None


def _require_db():
    if files_col is None:
        raise RuntimeError(
            "MONGO_URI set nahi hai — .env / Railway env vars mein MONGO_URI daalo."
        )


async def ensure_indexes():
    """Startup pe ek baar call karo. Koi zaroori index nahi (unique_id already _id hai),
    bas connectivity confirm ho jaati hai aur agar future mein TTL/expiry chahiye ho
    to yaha add kar sakte ho."""
    if files_col is None:
        log.warning("MONGO_URI missing hai — file-to-link feature disabled rahega.")
        return
    await _client.admin.command("ping")
    log.info("MongoDB connected: db=%s", DB_NAME)


async def save_file(
    unique_id: str,
    file_id: str,
    file_name: str,
    file_size: int,
    mime_type: str,
    chat_id: int,
    message_id: int,
) -> str:
    """File record upsert karta hai aur uski _id (= unique_id) return karta hai."""
    _require_db()
    doc = {
        "_id": unique_id,
        "file_id": file_id,
        "file_name": file_name,
        "file_size": file_size,
        "mime_type": mime_type,
        "chat_id": chat_id,
        "message_id": message_id,
        "created_at": datetime.now(timezone.utc),
    }
    await files_col.update_one({"_id": unique_id}, {"$set": doc}, upsert=True)
    return unique_id


async def get_file(unique_id: str) -> dict | None:
    if files_col is None:
        return None
    return await files_col.find_one({"_id": unique_id})
