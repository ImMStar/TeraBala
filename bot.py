
import asyncio
import logging
import os
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlparse

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

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("video-bot")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
BASE_URL = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
MAX_BYTES = 48 * 1024 * 1024

if not TOKEN:
    raise RuntimeError("Missing TELEGRAM_BOT_TOKEN")
if not BASE_URL:
    raise RuntimeError("Missing RENDER_EXTERNAL_URL")
if len(WEBHOOK_SECRET) < 16:
    raise RuntimeError("WEBHOOK_SECRET must be at least 16 characters")

web = Flask(__name__)
loop = asyncio.new_event_loop()
bot_app = Application.builder().token(TOKEN).build()


def quality_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Best (up to 720p)", callback_data="best")],
        [InlineKeyboardButton("720p", callback_data="720")],
        [InlineKeyboardButton("480p", callback_data="480")],
        [InlineKeyboardButton("360p", callback_data="360")],
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "🎬 Video Downloader\n\n"
        "Send a public video URL and choose a quality.\n"
        "Only download content you own or have permission to save."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "Send a public video URL, then select a quality.\n"
        "Maximum file size: approximately 48 MB.\n"
        "Some websites may not be supported. Private, paywalled, "
        "or DRM-protected content is not supported."
    )


async def receive_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = (update.effective_message.text or "").strip()
    parsed = urlparse(url)

    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or len(url) > 2048
        or any(c.isspace() for c in url)
    ):
        await update.effective_message.reply_text(
            "Please send one complete public HTTP/HTTPS video URL."
        )
        return

    context.user_data["video_url"] = url
    await update.effective_message.reply_text(
        "Choose a quality:",
        reply_markup=quality_keyboard(),
    )


def download_video(url: str, quality: str, folder: str):
    height = 720 if quality == "best" else int(quality)
    output = str(Path(folder) / "video.%(ext)s")

    options = {
        "format": f"best[height<={height}]/best",
        "outtmpl": output,
        "noplaylist": True,
        "max_filesize": MAX_BYTES,
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "retries": 2,
        "socket_timeout": 20,
    }

    with yt_dlp.YoutubeDL(options) as downloader:
        info = downloader.extract_info(url, download=True)
        filename = Path(downloader.prepare_filename(info))

        if not filename.is_file():
            mp4 = filename.with_suffix(".mp4")
            if mp4.is_file():
                filename = mp4
            else:
                candidates = [
                    p for p in Path(folder).iterdir()
                    if p.is_file() and not p.name.endswith((".part", ".ytdl"))
                ]
                if not candidates:
                    raise FileNotFoundError("Downloaded file not found")
                filename = max(candidates, key=lambda p: p.stat().st_mtime)

        if filename.stat().st_size > MAX_BYTES:
            raise ValueError("FILE_TOO_LARGE")

        return str(filename), info.get("title", "Downloaded video")


async def handle_quality(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    url = context.user_data.get("video_url")
    quality = query.data

    if not url or quality not in {"best", "720", "480", "360"}:
        await query.edit_message_text("Request expired. Send the URL again.")
        return

    status = await query.edit_message_text("⏳ Downloading. Please wait...")

    try:
        with tempfile.TemporaryDirectory(prefix="tg-video-") as folder:
            path, title = await asyncio.to_thread(
                download_video, url, quality, folder
            )

            with open(path, "rb") as file_obj:
                await query.message.reply_document(
                    document=file_obj,
                    filename=Path(path).name,
                    caption=title[:900],
                    read_timeout=120,
                    write_timeout=120,
                    connect_timeout=30,
                )

        await status.edit_text("✅ Download completed.")

    except ValueError as exc:
        if str(exc) == "FILE_TOO_LARGE":
            message = "File exceeds 48 MB. Try a lower quality."
        else:
            message = "Download failed. Try another supported URL."
        await status.edit_text(message)

    except Exception:
        log.exception("Download failed")
        try:
            await status.edit_text(
                "❌ Download failed. The site may be unsupported, "
                "or the video may be unavailable."
            )
        except Exception:
            pass

    finally:
        context.user_data.pop("video_url", None)


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

    asyncio.run_coroutine_threadsafe(process_update(data), loop)
    return "OK", 200


async def initialize_bot():
    await bot_app.initialize()
    await bot_app.start()

    await bot_app.bot.set_webhook(
        url=f"{BASE_URL}/telegram/{WEBHOOK_SECRET}",
        allowed_updates=Update.ALL_TYPES,
    )

    log.info("Telegram webhook configured successfully")


def run_event_loop():
    asyncio.set_event_loop(loop)
    loop.run_until_complete(initialize_bot())
    loop.run_forever()


bot_app.add_handler(CommandHandler("start", start))
bot_app.add_handler(CommandHandler("help", help_command))
bot_app.add_handler(
    CallbackQueryHandler(
        handle_quality,
        pattern=r"^(best|720|480|360)$",
    )
)
bot_app.add_handler(
    MessageHandler(filters.TEXT & ~filters.COMMAND, receive_url)
)

threading.Thread(
    target=run_event_loop,
    name="telegram-event-loop",
    daemon=True,
).start()
