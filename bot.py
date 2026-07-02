import os
import asyncio
import logging
import time
from typing import Dict

from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from config import (
    API_ID, API_HASH, BOT_TOKEN,
    BUNNY_LIBRARY_ID, BUNNY_API_KEY, BUNNY_PULL_ZONE,
    DOWNLOAD_DIR,
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

engine = BunnyStreamClient(BUNNY_LIBRARY_ID, BUNNY_API_KEY, BUNNY_PULL_ZONE)

ALL_QUALITIES = ["360p", "480p", "720p", "1080p"]

# In-memory job state, keyed by a short job id ("chatid:msgid").
# Never persisted to disk — bot restart clears any pending selections.
PENDING_JOBS: Dict[str, dict] = {}


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def progress_bar(pct: int, length: int = 12) -> str:
    pct = max(0, min(100, pct))
    filled = int(length * pct / 100)
    return "▓" * filled + "░" * (length - filled) + f"  {pct}%"


def build_quality_keyboard(job_id: str, selected: set) -> InlineKeyboardMarkup:
    rows = []
    row = []
    for q in ALL_QUALITIES:
        mark = "✅ " if q in selected else "⬜ "
        row.append(InlineKeyboardButton(f"{mark}{q}", callback_data=f"q:{job_id}:{q}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    all_selected = len(selected) == len(ALL_QUALITIES)
    select_all_label = "☑️ Select All" if not all_selected else "◻️ Unselect All"
    rows.append([InlineKeyboardButton(select_all_label, callback_data=f"q:{job_id}:all")])
    rows.append([InlineKeyboardButton("▶️ Start", callback_data=f"q:{job_id}:start")])
    rows.append([InlineKeyboardButton("✖️ Cancel", callback_data=f"q:{job_id}:cancel")])
    return InlineKeyboardMarkup(rows)


def make_job_id(message: Message) -> str:
    # NOTE: must not contain ":" — callback_data below is itself ":"-delimited
    # ("q:<job_id>:<action>"), so a colon inside job_id would break the split.
    return f"{message.chat.id}_{message.id}"


# ---------------------------------------------------------------------------
# core processing pipeline (runs after the user hits Start)
# ---------------------------------------------------------------------------

async def _run_job(client: Client, status_msg: Message, job: dict):
    loop = asyncio.get_event_loop()
    origin_message: Message = job["message"]
    selected = job["selected"]
    local_path = None

    def schedule_edit(text: str):
        """Background threads (run_in_executor callbacks) ke liye safe status
        update. Pehle status_msg.edit_text(text) ko seedha run_coroutine_threadsafe
        mein pass kiya jaata tha, jo occasionally 'TypeError: A coroutine object
        is required' de kar poora job crash kar deta tha. Ab ek chota async
        wrapper banate hain jo guaranteed real coroutine hai, aur andar try/except
        se koi bhi edit-related error (MessageNotModified, FloodWait, etc.)
        silently ignore ho jaata hai instead of killing the whole job."""
        async def _do():
            try:
                await status_msg.edit_text(text)
            except Exception:
                pass
        try:
            asyncio.run_coroutine_threadsafe(_do(), loop)
        except Exception:
            pass

    try:
        if job["kind"] == "file":
            await status_msg.edit_text(f"Downloading  {progress_bar(0)}")
            last_edit = {"t": 0.0}

            async def dl_progress(current, total):
                now = time.time()
                if now - last_edit["t"] < 2 and current != total:
                    return
                last_edit["t"] = now
                pct = int(current * 100 / total) if total else 0
                try:
                    await status_msg.edit_text(f"Downloading  {progress_bar(pct)}")
                except Exception:
                    pass

            local_path = await origin_message.download(
                file_name=f"{DOWNLOAD_DIR}/", progress=dl_progress
            )

            title = os.path.basename(local_path)
            video_id = engine.create_video(title)

            last_edit["t"] = 0.0

            def up_progress(uploaded, total):
                now = time.time()
                if now - last_edit["t"] < 2 and uploaded != total:
                    return
                last_edit["t"] = now
                pct = int(uploaded * 100 / total) if total else 0
                text = f"Preparing  {progress_bar(pct)}"
                schedule_edit(text)

            await loop.run_in_executor(
                None, lambda: engine.upload_video(video_id, local_path, on_progress=up_progress)
            )

            os.remove(local_path)
            local_path = None

        else:  # url
            source_url = job["source_url"]
            title = os.path.basename(source_url.split("?")[0]) or "video"
            await status_msg.edit_text(f"Preparing  {progress_bar(0)}")
            video_id = await loop.run_in_executor(
                None, lambda: engine.create_video_from_url(source_url, title)
            )

        # ---- encode / wait ----
        last_enc = {"t": 0.0}

        def encode_progress(status_label, pct):
            now = time.time()
            if now - last_enc["t"] < 2:
                return
            last_enc["t"] = now
            text = f"Encoding  {progress_bar(int(pct))}"
            schedule_edit(text)

        video_data = await loop.run_in_executor(
            None,
            lambda: engine.wait_until_ready(
                video_id,
                poll_interval=POLL_INTERVAL_SECONDS,
                timeout=ENCODE_TIMEOUT_SECONDS,
                on_progress=encode_progress,
            ),
        )

        resolutions = engine.available_resolutions(video_data)
        resolutions = [r for r in resolutions if r in selected]

        if not resolutions:
            await status_msg.edit_text(
                "Requested quality is not available for this video "
                "(source resolution too low, ya us quality mein encode nahi ho payi)."
            )
            if DELETE_FROM_BUNNY_AFTER_SEND:
                engine.delete_video(video_id)
            return

        await status_msg.edit_text(f"Ready! Sending {', '.join(resolutions)}...")

        for res in resolutions:
            dest = os.path.join(DOWNLOAD_DIR, f"{video_id}_{res}.mp4")
            last_edit = {"t": 0.0}

            def make_dl_cb(r=res):
                def cb(downloaded, total):
                    now = time.time()
                    if now - last_edit["t"] < 2 and downloaded != total:
                        return
                    last_edit["t"] = now
                    pct = int(downloaded * 100 / total) if total else 0
                    text = f"{r}  Downloading  {progress_bar(pct)}"
                    schedule_edit(text)
                return cb

            async def upload_progress(current, total, r=res):
                now = time.time()
                if now - last_edit["t"] < 2 and current != total:
                    return
                last_edit["t"] = now
                pct = int(current * 100 / total) if total else 0
                try:
                    await status_msg.edit_text(f"{r}  Uploading  {progress_bar(pct)}")
                except Exception:
                    pass

            try:
                await loop.run_in_executor(
                    None,
                    lambda r=res, d=dest, cb=make_dl_cb(): engine.download_resolution(
                        video_id, r, d, on_progress=cb
                    ),
                )
                last_edit["t"] = 0.0
                await origin_message.reply_video(dest, caption=f"Quality: {res}", progress=upload_progress)
            finally:
                if os.path.exists(dest):
                    os.remove(dest)

        await status_msg.edit_text("Done! Sab selected qualities bhej di gayi ✅")

        if DELETE_FROM_BUNNY_AFTER_SEND:
            engine.delete_video(video_id)

    except Exception:
        log.exception("job failed")
        try:
            await status_msg.edit_text(
                "Kuch gadbad ho gayi processing ke dauraan. Thodi der baad try karo, "
                "ya file/link dobara bhejo."
            )
        except Exception:
            pass
    finally:
        if local_path and os.path.exists(local_path):
            os.remove(local_path)


# ---------------------------------------------------------------------------
# handlers
# ---------------------------------------------------------------------------

@app.on_message(filters.command("start"))
async def start_cmd(client: Client, message: Message):
    await message.reply_text(
        "Video ya file bhejo, ya phir uska direct link (http/https se shuru) bhejo.\n\n"
        "Uske baad quality selection ka option aa jayega — 360p / 480p / 720p / 1080p "
        "mein se jo chahiye wo select karo (ya Select All), phir ▶️ Start dabao.\n\n"
        "Processing complete hote hi selected qualities isi chat mein bhej dunga."
    )


@app.on_message(filters.text & filters.regex(r"^https?://"))
async def handle_url(client: Client, message: Message):
    source_url = message.text.strip()
    if "://" in source_url:
        scheme, rest = source_url.split("://", 1)
        rest = rest.replace("//", "/")
        source_url = f"{scheme}://{rest}"

    job_id = make_job_id(message)
    PENDING_JOBS[job_id] = {
        "kind": "url",
        "source_url": source_url,
        "message": message,
        "selected": set(),
    }

    await message.reply_text(
        "Kaunsi quality chahiye? Select karo aur Start dabao:",
        reply_markup=build_quality_keyboard(job_id, set()),
    )


@app.on_message(filters.video | filters.document)
async def handle_video(client: Client, message: Message):
    job_id = make_job_id(message)
    PENDING_JOBS[job_id] = {
        "kind": "file",
        "message": message,
        "selected": set(),
    }

    await message.reply_text(
        "Kaunsi quality chahiye? Select karo aur Start dabao:",
        reply_markup=build_quality_keyboard(job_id, set()),
    )


@app.on_callback_query(filters.regex(r"^q:"))
async def on_quality_callback(client: Client, cq: CallbackQuery):
    try:
        _, job_id, action = cq.data.split(":", 2)
    except ValueError:
        await cq.answer()
        return

    job = PENDING_JOBS.get(job_id)
    if not job:
        await cq.answer("Ye request expire ho gayi, file/link dobara bhejo.", show_alert=True)
        try:
            await cq.message.delete()
        except Exception:
            pass
        return

    if action == "cancel":
        PENDING_JOBS.pop(job_id, None)
        await cq.answer("Cancel kar diya")
        await cq.message.edit_text("Cancelled.")
        return

    if action == "all":
        if len(job["selected"]) == len(ALL_QUALITIES):
            job["selected"] = set()
        else:
            job["selected"] = set(ALL_QUALITIES)
        await cq.answer()
        await cq.message.edit_reply_markup(build_quality_keyboard(job_id, job["selected"]))
        return

    if action == "start":
        if not job["selected"]:
            await cq.answer("Pehle kam se kam ek quality select karo", show_alert=True)
            return
        await cq.answer("Shuru kar raha hoon...")
        status_msg = cq.message
        await status_msg.edit_text(f"Starting  {progress_bar(0)}", reply_markup=None)
        PENDING_JOBS.pop(job_id, None)
        asyncio.create_task(_run_job(client, status_msg, job))
        return

    if action in ALL_QUALITIES:
        if action in job["selected"]:
            job["selected"].discard(action)
        else:
            job["selected"].add(action)
        await cq.answer()
        await cq.message.edit_reply_markup(build_quality_keyboard(job_id, job["selected"]))
        return

    await cq.answer()


if __name__ == "__main__":
    log.info("Bot starting...")
    app.run()
