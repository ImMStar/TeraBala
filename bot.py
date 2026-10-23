import asyncio
import logging
import os
import tempfile
from pathlib import Path
from threading import Thread

import yt_dlp
from flask import Flask, abort, request
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger(__name__)

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
BASE_URL = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
MAX_BYTES = 48 * 1024 * 1024  # conservative limit; Telegram limits can vary

if not BASE_URL:
    raise RuntimeError("RENDER_EXTERNAL_URL is missing. Deploy as a Render Web Service.")
if not WEBHOOK_SECRET or len(WEBHOOK_SECRET) < 16:
    raise RuntimeError("Set WEBHOOK_SECRET to a random string of at least 16 characters.")

web = Flask(__name__)
loop = asyncio.new_event_loop()
bot_app = Application.builder().token(TOKEN).build()


def quality_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Best available (up to 720p)", callback_data="best")],
        [InlineKeyboardButton("720p", callback_data="720")],
        [InlineKeyboardButton("480p", callback_data="480")],
        [InlineKeyboardButton("360p", callback_data="360")],
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "🎬 Video Downloader\n\nSend a public video URL, then choose a quality.\n"
        "Only download content you have permission to save. Some sites or videos may not work."
    )


async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    url = (message.text or "").strip()
    if not url.startswith(("https://", "http://")):
        await message.reply_text("Please send a valid URL beginning with http:// or https://.")
        return
    context.user_data["video_url"] = url
    await message.reply_text("Choose a quality:", reply_markup=quality_keyboard())


def _download(url: str, quality: str, directory: str):
    output_template = str(Path(directory) / "video.%(ext)s")
    height = 720 if quality == "best" else int(quality)
    opts = {
        "format": f"best[height<={height}]/best",
        "outtmpl": output_template,
        "noplaylist": True,
        "max_filesize": MAX_BYTES,
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "retries": 2,
        "socket_timeout": 20,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = Path(ydl.prepare_filename(info))
        if not filename.exists():
            mp4 = filename.with_suffix(".mp4")
            if mp4.exists():
                filename = mp4
            else:
                files = list(Path(directory).glob("video.*"))
                if not files:
                    raise FileNotFoundError("Downloaded file was not found")
                filename = files[0]
        if filename.stat().st_size > MAX_BYTES:
            raise ValueError("FILE_TOO_LARGE")
        return str(filename), info.get("title", "Downloaded video")


async def handle_quality(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    url = context.user_data.get("video_url")
    if not url:
        await query.edit_message_text("Send the video URL again, then choose a quality.")
        return
    quality = query.data
    status = await query.edit_message_text("⏳ Downloading. This may take a while...")
    try:
        with tempfile.TemporaryDirectory() as directory:
            path, title = await asyncio.to_thread(_download, url, quality, directory)
            with open(path, "rb") as file_obj:
                await query.message.reply_document(
                    document=file_obj,
                    filename=Path(path).name,
                    caption=title[:900],
                    read_timeout=120,
                    write_timeout=120,
                    connect_timeout=30,
                )
        await status.delete()
    except ValueError as exc:
        if str(exc) == "FILE_TOO_LARGE":
            await status.edit_text("The file is over the bot's configured 48 MB limit. Try a lower quality.")
        else:
            await status.edit_text("Download failed. This video/site may not be supported.")
    except Exception:
        log.exception("Video download failed")
        try:
            await status.edit_text(
                "Download failed. The site may be unsupported, require access this bot cannot provide, "
                "or the file may exceed available limits."
            )
        except Exception:
            pass


async def process_update(data):
    update = Update.de_json(data, bot_app.bot)
    await bot_app.process_update(update)


@web.get("/")
def health():
    return "Telegram video downloader is running.", 200


@web.post("/telegram/<secret>")
def telegram_webhook(secret):
    if secret != WEBHOOK_SECRET:
        abort(403)
    data = request.get_json(silent=True)
    if not data:
        abort(400)
    future = asyncio.run_coroutine_threadsafe(process_update(data), loop)
    # Return promptly; the event loop handles the update asynchronously.
    return "OK", 200


async def initialize():
    await bot_app.initialize()
    await bot_app.start()
    await bot_app.bot.set_webhook(
        url=f"{BASE_URL}/telegram/{WEBHOOK_SECRET}",
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=False,
    )
    log.info("Telegram webhook configured")


def run_event_loop():
    asyncio.set_event_loop(loop)
    loop.run_until_complete(initialize())
    loop.run_forever()


Thread(target=run_event_loop, name="telegram-event-loop", daemon=True).start()
