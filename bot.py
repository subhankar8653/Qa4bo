import os
import asyncio
import logging
import time

from pyrogram import Client, filters
from pyrogram.types import Message

from config import (
    API_ID, API_HASH, BOT_TOKEN,
    BUNNY_LIBRARY_ID, BUNNY_API_KEY, BUNNY_PULL_ZONE,
    WANTED_RESOLUTIONS, DOWNLOAD_DIR,
    DELETE_FROM_BUNNY_AFTER_SEND,
    POLL_INTERVAL_SECONDS, ENCODE_TIMEOUT_SECONDS,
)
from bunny_client import BunnyStreamClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("quality_changer_bot")

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

app = Client(
    "quality_changer_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
)

bunny = BunnyStreamClient(BUNNY_LIBRARY_ID, BUNNY_API_KEY, BUNNY_PULL_ZONE)


async def _wait_and_send_qualities(message: Message, status_msg: Message, video_id: str):
    """Bunny ka poora encoding (status Finished) complete hone tak wait karta hai,
    phir har resolution ek-ek karke download karke isi chat mein bhej deta hai —
    isse CDN ko har mp4 file propagate hone ka poora time mil jaata hai."""
    loop = asyncio.get_event_loop()

    def progress_cb(status, progress):
        log.info("video %s status=%s progress=%s%%", video_id, status, progress)

    video_data = await loop.run_in_executor(
        None,
        lambda: bunny.wait_until_ready(
            video_id,
            poll_interval=POLL_INTERVAL_SECONDS,
            timeout=ENCODE_TIMEOUT_SECONDS,
            on_progress=progress_cb,
        ),
    )

    resolutions = bunny.available_resolutions(video_data)
    if WANTED_RESOLUTIONS:
        resolutions = [r for r in resolutions if r in WANTED_RESOLUTIONS]

    if not resolutions:
        await status_msg.edit_text(
            "Koi resolution nahi mila. Check karo ki library ki Encoding "
            "settings mein 'MP4 Fallback' ON hai ya nahi."
        )
        if DELETE_FROM_BUNNY_AFTER_SEND:
            bunny.delete_video(video_id)
        return

    await status_msg.edit_text(
        f"Ready! {len(resolutions)} quality mil gayi ({', '.join(resolutions)}). "
        f"Bhej raha hoon ek-ek karke..."
    )

    for res in resolutions:
        dest = os.path.join(DOWNLOAD_DIR, f"{video_id}_{res}.mp4")
        last_edit = {"t": 0.0}

        def make_dl_progress(r=res):
            def cb(downloaded, total):
                now = time.time()
                if now - last_edit["t"] < 3:
                    return
                last_edit["t"] = now
                pct = int(downloaded * 100 / total) if total else 0
                text = f"{r} download ho raha hai bunny se... {pct}%"
                asyncio.run_coroutine_threadsafe(status_msg.edit_text(text), loop)
            return cb

        async def upload_progress(current, total, r=res):
            now = time.time()
            if now - last_edit["t"] < 3:
                return
            last_edit["t"] = now
            pct = int(current * 100 / total) if total else 0
            try:
                await status_msg.edit_text(f"{r} Telegram pe upload ho raha hai... {pct}%")
            except Exception:
                pass

        try:
            await loop.run_in_executor(
                None,
                lambda r=res, d=dest, cb=make_dl_progress(): bunny.download_resolution(
                    video_id, r, d, on_progress=cb
                ),
            )
            last_edit["t"] = 0.0
            await message.reply_video(dest, caption=f"Quality: {res}", progress=upload_progress)
        finally:
            if os.path.exists(dest):
                os.remove(dest)

    await status_msg.edit_text("Sab qualities bhej di gayi \u2705")

    if DELETE_FROM_BUNNY_AFTER_SEND:
        bunny.delete_video(video_id)


@app.on_message(filters.command("start"))
async def start_cmd(client: Client, message: Message):
    await message.reply_text(
        "Do tarike se use kar sakte ho:\n\n"
        "1️⃣ Direct video/file bhejo — main download+upload karke Bunny pe daal dunga.\n\n"
        "2️⃣ (Fast) Pehle apna FileToLink bot use karke us video ka direct link banao, "
        "phir wahi link (http/https se shuru) yahan bhejo — main seedha Bunny ko "
        "bolunga wahan se fetch kar le, koi local download nahi hoga.\n\n"
        "Dono case mein encoding complete hote hi saari qualities (240p-1080p) "
        "wapas isi chat mein bhej dunga."
    )


@app.on_message(filters.text & filters.regex(r"^https?://"))
async def handle_url(client: Client, message: Message):
    """FileToLink (ya kisi bhi public) link se seedha Bunny fetch karega —
    local download/upload step yahan skip ho jaata hai, isliye ye fast route hai."""
    source_url = message.text.strip()
    # Kabhi kabhi FileToLink link mein double slash aa jaata hai (base_url//dl/..),
    # jisse Bunny 422 de deta hai — path ka double-slash normalize kar dete hain.
    if "://" in source_url:
        scheme, rest = source_url.split("://", 1)
        rest = rest.replace("//", "/")
        source_url = f"{scheme}://{rest}"
    status_msg = await message.reply_text("Bunny ko bol raha hoon URL se fetch kare...")

    try:
        title = os.path.basename(source_url.split("?")[0]) or "video"
        video_id = bunny.create_video_from_url(source_url, title)

        await status_msg.edit_text(
            "Bunny ne fetch shuru kar diya (URL se seedha download ho raha hai bunny "
            "ke server pe)... encoding ke baad qualities bhej dunga."
        )

        await _wait_and_send_qualities(message, status_msg, video_id)

    except Exception as e:
        log.exception("URL fetch failed")
        await status_msg.edit_text(f"Error aaya: {e}")


@app.on_message(filters.video | filters.document)
async def handle_video(client: Client, message: Message):
    status_msg = await message.reply_text("Downloading video from Telegram...")
    local_path = None

    try:
        local_path = await message.download(file_name=f"{DOWNLOAD_DIR}/")

        await status_msg.edit_text("Uploading to Bunny Stream...")
        title = os.path.basename(local_path)
        video_id = bunny.create_video(title)
        bunny.upload_video(video_id, local_path)

        # Local copy no longer needed once Bunny has it.
        os.remove(local_path)
        local_path = None

        await status_msg.edit_text(
            "Encoding shuru ho gayi Bunny Stream pe... "
            "isme video ki length ke hisaab se kuch minute lag sakte hain."
        )

        await _wait_and_send_qualities(message, status_msg, video_id)

    except Exception as e:
        log.exception("Processing failed")
        await status_msg.edit_text(f"Error aaya: {e}")
    finally:
        if local_path and os.path.exists(local_path):
            os.remove(local_path)


if __name__ == "__main__":
    log.info("Bot starting...")
    app.run()
