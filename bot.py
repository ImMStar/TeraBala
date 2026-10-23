import asyncio
import ipaddress
import logging
import os
import re
import shutil
import socket
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes,
    MessageHandler, filters,
)
import yt_dlp

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=os.getenv("LOG_LEVEL", "INFO"),
)
log = logging.getLogger("video-bot")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
PORT = int(os.getenv("PORT", "10000"))
PUBLIC_URL = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "49"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
DOWNLOAD_TIMEOUT = int(os.getenv("DOWNLOAD_TIMEOUT_SECONDS", "240"))
MAX_CONCURRENT_DOWNLOADS = int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "1"))

semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)

TERABOX_DOMAINS = {"terabox.com", "www.terabox.com", "terabox.app", "www.terabox.app",
                   "1024tera.com", "www.1024tera.com", "teraboxlink.com", "www.teraboxlink.com",
                   "terasharelink.com", "www.terasharelink.com", "4funbox.com", "www.4funbox.com"}
NOTYDRIVE_DOMAINS = {"notydrive.com", "www.notydrive.com", "notydrive.in", "www.notydrive.in"}
DISKWALA_DOMAINS = {"diskwala.com", "www.diskwala.com"}
FLEZEN_DOMAINS = {"flezen.net", "www.flezen.net", "flezen.com", "www.flezen.com"}

def special_service(url: str):
    try:
        host = (urlparse(url).hostname or "").lower().rstrip(".")
    except Exception:
        return None
    if host in TERABOX_DOMAINS or host.endswith(".terabox.com") or host.endswith(".terabox.app"):
        return "TeraBox"
    if host in NOTYDRIVE_DOMAINS:
        return "NoTyDrive"
    if host in DISKWALA_DOMAINS:
        return "DiskWala"
    if host in FLEZEN_DOMAINS:
        return "Flezen"
    return None

# Telegram Bot API upload limit is commonly 50 MB for sendVideo/sendDocument.
# Leave a small margin because limits and platform behaviour can vary.
QUALITY_OPTIONS = {
    "best": ("Best available", "bv*+ba/b"),
    "1080": ("Up to 1080p", "bv*[height<=1080]+ba/b[height<=1080]/b"),
    "720": ("Up to 720p", "bv*[height<=720]+ba/b[height<=720]/b"),
    "480": ("Up to 480p", "bv*[height<=480]+ba/b[height<=480]/b"),
    "audio": ("Audio only (MP3 if available)", "bestaudio/best"),
}

def validate_public_url(url: str) -> str:
    """Basic SSRF guard: accept only HTTP(S) URLs resolving to public IPs."""
    url = (url or "").strip()
    if len(url) > 2048:
        raise ValueError("That URL is too long.")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Please send a complete http:// or https:// video URL.")
    host = parsed.hostname.rstrip(".").lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith((".localhost", ".local", ".internal")):
        raise ValueError("Private/local addresses are not allowed.")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, None)}
    except socket.gaierror:
        raise ValueError("I couldn't resolve that website. Check the URL and try again.")
    if not addresses:
        raise ValueError("I couldn't resolve that website.")
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%")[0])
        if not ip.is_global:
            raise ValueError("Private or local network addresses are not allowed.")
    return url

def quality_keyboard():
    rows = [
        [InlineKeyboardButton("Best available", callback_data="q:best")],
        [InlineKeyboardButton("1080p", callback_data="q:1080"),
         InlineKeyboardButton("720p", callback_data="q:720")],
        [InlineKeyboardButton("480p", callback_data="q:480"),
         InlineKeyboardButton("Audio only", callback_data="q:audio")],
    ]
    return InlineKeyboardMarkup(rows)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎬 Send me a public video URL from a supported website.\n\n"
        "I'll offer quality choices and send the downloaded file here.\n"
        "Use /help for limits and supported-content rules."
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "How to use:\n"
        "1. Send a public video URL.\n"
        "2. Choose a quality.\n"
        "3. Wait while I download and send it.\n\n"
        "Limits: downloads must finish within the server timeout and the file "
        f"must be under about {MAX_UPLOAD_MB} MB to send through this bot.\n\n"
        "Only download content you own or have permission to save. Private, "
        "DRM-protected, paywalled, or otherwise restricted content may not work. "
        "Website support depends on yt-dlp extractors and site changes."
    )

async def receive_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = (update.message.text or "").strip()
    # Avoid treating surrounding text as a URL; accept a single URL only.
    match = re.fullmatch(r"(https?://\S+)", raw)
    if not match:
        await update.message.reply_text("Please send one complete video URL starting with http:// or https://.")
        return
    try:
        url = validate_public_url(match.group(1))
    except ValueError as exc:
        await update.message.reply_text(str(exc))
        return
    service = special_service(url)
    if service:
        await update.message.reply_text(
            f"⚠️ This looks like a {service} link. The bot recognizes the service, "
            "but yt-dlp does not guarantee support for its share-link format. "
            "It may fail unless the provider exposes a compatible public media URL "
            "or an official integration. I won't request passwords or bypass private access."
        )
    context.user_data["pending_url"] = url
    await update.message.reply_text("Choose download quality:", reply_markup=quality_keyboard())

def download_video(url: str, format_selector: str, folder: str, audio_only: bool = False):
    output_template = str(Path(folder) / "%(title).100s-%(id)s.%(ext)s")
    opts = {
        "format": format_selector,
        "outtmpl": output_template,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
        "retries": 2,
        "fragment_retries": 2,
        "socket_timeout": 20,
        "max_filesize": MAX_UPLOAD_BYTES,
        "merge_output_format": "mp4",
        "cachedir": False,
    }
    if audio_only:
        opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}]
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        requested = info.get("requested_downloads") or []
        filepath = requested[0].get("filepath") if requested else None
        if not filepath:
            filepath = ydl.prepare_filename(info)
            # yt-dlp may merge into mp4 after prepare_filename returns another extension.
            candidate = str(Path(filepath).with_suffix(".mp4"))
            if os.path.exists(candidate):
                filepath = candidate
        path = Path(filepath)
        if not path.exists():
            # Fall back to the newest file created in the temporary directory.
            candidates = [p for p in Path(folder).iterdir() if p.is_file() and not p.name.endswith((".part", ".ytdl"))]
            if candidates:
                path = max(candidates, key=lambda p: p.stat().st_mtime)
        if not path.exists() or not path.is_file():
            raise RuntimeError("Download finished but the output file could not be found.")
        return str(path), info

async def quality_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    key = query.data.split(":", 1)[1]
    url = context.user_data.get("pending_url")
    if not url or key not in QUALITY_OPTIONS:
        await query.edit_message_text("That request expired. Send the video URL again.")
        return

    label, selector = QUALITY_OPTIONS[key]
    await query.edit_message_text(f"⏳ Downloading: {label}\nPlease wait…")

    async with semaphore:
        with tempfile.TemporaryDirectory(prefix="tg-video-") as folder:
            try:
                # yt-dlp is blocking; run it away from the async event loop.
                filepath, info = await asyncio.wait_for(
                    asyncio.to_thread(download_video, url, selector, folder, key == "audio"),
                    timeout=DOWNLOAD_TIMEOUT,
                )
                size = os.path.getsize(filepath)
                if size > MAX_UPLOAD_BYTES:
                    await query.message.reply_text(
                        f"❌ This file is {size / (1024*1024):.1f} MB. "
                        f"I can send files up to about {MAX_UPLOAD_MB} MB with this setup. "
                        "Try a lower quality."
                    )
                    return

                title = (info.get("title") or "Downloaded video")[:900]
                await context.bot.send_chat_action(chat_id=query.message.chat_id, action=ChatAction.UPLOAD_DOCUMENT)
                with open(filepath, "rb") as f:
                    if key == "audio":
                        await context.bot.send_audio(
                            chat_id=query.message.chat_id, audio=f,
                            title=title[:64], caption=f"🎵 {title[:900]}",
                            read_timeout=120, write_timeout=120, connect_timeout=30,
                        )
                    else:
                        # send as video when possible; fall back to document if Telegram can't process it.
                        try:
                            await context.bot.send_video(
                                chat_id=query.message.chat_id, video=f,
                                caption=f"🎬 {title[:900]}",
                                supports_streaming=True,
                                read_timeout=120, write_timeout=120, connect_timeout=30,
                            )
                        except Exception as send_error:
                            log.warning("sendVideo failed; trying sendDocument: %s", send_error)
                            f.seek(0)
                            await context.bot.send_document(
                                chat_id=query.message.chat_id, document=f,
                                filename=os.path.basename(filepath),
                                caption=f"📁 {title[:900]}",
                                read_timeout=120, write_timeout=120, connect_timeout=30,
                            )
                await query.message.reply_text("✅ Finished.")
            except asyncio.TimeoutError:
                await query.message.reply_text("❌ The download took too long. Try a shorter video or lower quality.")
            except yt_dlp.utils.DownloadError as exc:
                log.info("yt-dlp download error: %s", exc)
                service = special_service(url)
                if service:
                    await query.message.reply_text(
                        f"❌ {service} extraction failed. This share-link format may need an official integration. "
                        "Try the provider's official app/site or a direct public media URL supplied by the owner."
                    )
                else:
                    await query.message.reply_text(
                        "❌ I couldn't download that URL. The site may be unsupported, the video may be unavailable, "
                        "or the site may require access I can't use."
                    )
            except Exception:
                log.exception("Unexpected download failure")
                await query.message.reply_text("❌ Something went wrong. Check the URL or try a lower quality.")
            finally:
                context.user_data.pop("pending_url", None)

def main():
    if not TOKEN:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN in the environment.")
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CallbackQueryHandler(quality_selected, pattern=r"^q:(best|1080|720|480|audio)$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_url))
    if not PUBLIC_URL:
        raise RuntimeError("RENDER_EXTERNAL_URL is missing. Deploy this project as a Render Web Service.")
    log.info("Bot is starting with Telegram webhook on port %s.", PORT)
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path="telegram",
        webhook_url=f"{PUBLIC_URL}/telegram",
        allowed_updates=Update.ALL_TYPES,
    )

if __name__ == "__main__":
    main()
