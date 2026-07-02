import os
import asyncio
import logging

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


@app.on_message(filters.command("start"))
async def start_cmd(client: Client, message: Message):
    await message.reply_text(
        "Video ya file bhejo — main use Bunny Stream pe upload karunga, "
        "encoding complete hote hi saari available qualities (240p-1080p) "
        "wapas isi chat mein bhej dunga."
    )


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
            try:
                await loop.run_in_executor(
                    None, lambda r=res, d=dest: bunny.download_resolution(video_id, r, d)
                )
                await message.reply_video(dest, caption=f"Quality: {res}")
            finally:
                if os.path.exists(dest):
                    os.remove(dest)

        await status_msg.edit_text("Sab qualities bhej di gayi \u2705")

        if DELETE_FROM_BUNNY_AFTER_SEND:
            bunny.delete_video(video_id)

    except Exception as e:
        log.exception("Processing failed")
        await status_msg.edit_text(f"Error aaya: {e}")
    finally:
        if local_path and os.path.exists(local_path):
            os.remove(local_path)


if __name__ == "__main__":
    log.info("Bot starting...")
    app.run()
