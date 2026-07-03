"""File-to-Link streaming server.

Yaha same Pyrogram bot client use hota hai jo bot.py mein hai — koi extra
session string ya Telethon client nahi chahiye. `/dl/<file_unique_id>` par
GET/HEAD request aane par, DB se file ka reference (chat_id + message_id)
nikaal ke Telegram se seedha stream kar dete hain, Range header ka bhi
proper support ke saath (taaki Bunny jaisi services partial/HEAD requests
bhi kar sake).
"""

import logging
import math
import mimetypes

from aiohttp import web

from config import STREAM_CHUNK_SIZE
from db import get_file

log = logging.getLogger("streamer")

routes = web.RouteTableDef()


def _parse_range(range_header: str, file_size: int):
    # Format: "bytes=START-END" (END optional)
    range_val = range_header.replace("bytes=", "").strip()
    start_str, _, end_str = range_val.partition("-")
    start = int(start_str) if start_str else 0
    end = int(end_str) if end_str else file_size - 1
    end = min(end, file_size - 1)
    if start < 0 or start > end:
        raise ValueError("invalid range")
    return start, end


async def _yield_bytes(bot, chat_id: int, message_id: int, start: int, end: int):
    """start-end (inclusive, byte offsets) ke beech ka data Telegram se
    chunk-aligned tareeke se nikaal kar yield karta hai."""
    message = await bot.get_messages(chat_id, message_id)
    media = message.video or message.document or message.audio or message.animation
    if media is None:
        return

    chunk_size = STREAM_CHUNK_SIZE
    offset_chunk = start // chunk_size          # kis chunk se shuru karein
    first_cut = start - (offset_chunk * chunk_size)  # us chunk ke andar kitna skip karein
    part_count = math.ceil((end + 1) / chunk_size) - offset_chunk

    current = 0
    async for chunk in bot.stream_media(message, offset=offset_chunk, limit=part_count):
        if not chunk:
            break
        current += 1
        if part_count == 1:
            yield chunk[first_cut : first_cut + (end - start + 1)]
        elif current == 1:
            yield chunk[first_cut:]
        elif current == part_count:
            last_cut = (end + 1) - ((offset_chunk + part_count - 1) * chunk_size)
            yield chunk[:last_cut]
        else:
            yield chunk
        if current >= part_count:
            break


@routes.get("/")
async def health(request: web.Request):
    return web.Response(text="qa4bo file-to-link server up hai ✅")


@routes.get("/dl/{file_uid}")
@routes.head("/dl/{file_uid}")
async def stream_handler(request: web.Request):
    file_uid = request.match_info["file_uid"]
    rec = await get_file(file_uid)
    if not rec:
        raise web.HTTPNotFound(text="Ye link invalid hai ya expire ho gaya.")

    bot = request.app["bot"]
    file_size = int(rec["file_size"])
    file_name = rec.get("file_name") or "video.mp4"
    mime_type = (
        rec.get("mime_type")
        or mimetypes.guess_type(file_name)[0]
        or "application/octet-stream"
    )

    range_header = request.headers.get("Range")
    status = 200
    start, end = 0, file_size - 1
    if range_header:
        try:
            start, end = _parse_range(range_header, file_size)
            status = 206
        except ValueError:
            raise web.HTTPRequestRangeNotSatisfiable()

    headers = {
        "Content-Type": mime_type,
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
        "Content-Disposition": f'inline; filename="{file_name}"',
    }
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"

    resp = web.StreamResponse(status=status, headers=headers)
    await resp.prepare(request)

    if request.method == "HEAD":
        return resp

    try:
        async for chunk in _yield_bytes(bot, rec["chat_id"], rec["message_id"], start, end):
            await resp.write(chunk)
    except (ConnectionResetError, ConnectionAbortedError):
        pass
    except Exception:
        log.exception("streaming failed for %s", file_uid)
    await resp.write_eof()
    return resp


async def start_web_server(bot, port: int):
    application = web.Application()
    application["bot"] = bot
    application.add_routes(routes)

    runner = web.AppRunner(application)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    log.info("Streaming server started on 0.0.0.0:%s", port)
    return runner
