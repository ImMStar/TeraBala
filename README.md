# Telegram Video Downloader Bot

A Python Telegram bot for downloading public videos from websites supported by `yt-dlp`. Users send a URL, choose a quality, and receive the file in Telegram.

## Features

- Quality menu: best available, up to 1080p, 720p, 480p, or audio-only
- Supports many public websites with compatible `yt-dlp` extractors
- FFmpeg merges separate audio/video streams where needed
- Telegram delivery only; oversized files are rejected
- Docker deployment for Render
- Basic URL checks to reject local/private network addresses

## Important limitations

- No downloader supports literally every website. Sites change, and some need login or block automated downloads.
- Use only content you own or have permission to download. This bot does not bypass DRM, paywalls, private access controls, or other protections.
- Telegram bot uploads are limited (commonly 50 MB via the hosted Bot API). This project uses a 49 MB safety limit. A lower quality may help.
- Render free services can have resource limits and may spin down or be unavailable depending on current plan terms. Downloads use temporary disk storage and are deleted after each request.
- This is intended for personal/small-scale use. Do not expose the bot token or commit it to GitHub.

## TeraBox, NoTyDrive, DiskWala and Flezen links

The bot recognizes common domains for these services and displays service-specific guidance. This is **not a guarantee that their share links can be downloaded**: the current generic `yt-dlp` setup may not support their link formats. NoTyDrive and DiskWala offer official Telegram integrations; use those where appropriate. For reliable direct downloading, an authorized direct-media URL or official API/integration is needed. This project does not request account passwords or bypass private/DRM/access restrictions.

## 1. Create your Telegram bot

1. Open [@BotFather](https://t.me/BotFather) in Telegram.
2. Send `/newbot`.
3. Follow the instructions and copy the bot token.
4. Keep the token private.

## 2. Deploy with GitHub + Render

1. Create a new GitHub repository, for example `telegram-video-downloader`.
2. Upload all files in this project to the repository root.
3. Sign in to [Render](https://render.com) and connect your GitHub account.
4. In Render, choose **New + → Blueprint** and select this repository. Render reads `render.yaml`.
5. When prompted, set `TELEGRAM_BOT_TOKEN` to the token from BotFather.
6. Deploy. Open the service logs and check for `Bot is starting (long polling).`

The service is a background worker, not a website. It does not need a public URL or web service port.

## 3. Use the bot

1. Open your bot using the username BotFather gave you.
2. Press **Start**.
3. Send one public video URL.
4. Choose a quality button and wait for the video to arrive.

## Optional environment settings

| Variable | Default | Purpose |
|---|---:|---|
| `TELEGRAM_BOT_TOKEN` | required | BotFather token |
| `MAX_UPLOAD_MB` | `49` | Reject files above this size |
| `DOWNLOAD_TIMEOUT_SECONDS` | `240` | Maximum download processing time |
| `MAX_CONCURRENT_DOWNLOADS` | `1` | Keep memory and disk usage manageable |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

If Render's free plan is not available for background workers on your account, use a supported paid worker plan. Avoid running multiple copies of the same bot token at once, as that can cause Telegram polling conflicts.

## Run locally (optional)

Requires Python 3.12+ and FFmpeg installed on your computer.

Windows PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:TELEGRAM_BOT_TOKEN="YOUR_BOT_TOKEN"
python bot.py
```

macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN="YOUR_BOT_TOKEN"
python bot.py
```

## Troubleshooting

- **No response:** inspect Render logs and confirm `TELEGRAM_BOT_TOKEN` is correct.
- **Unsupported URL:** try a different public video page; not every site is supported.
- **File too large:** select a lower quality.
- **Download timeout:** try a shorter video or increase `DOWNLOAD_TIMEOUT_SECONDS` within your host's limits.
- **Extractor errors:** update the `yt-dlp` version in `requirements.txt`, commit the change, and redeploy.


## Render Web Service deployment

This version uses Telegram webhooks and is intended for a Render **Web Service** (not Background Worker). Upload the files in this folder to the root of your GitHub repository, then create a Render Web Service from that repository using Docker. Add `TELEGRAM_BOT_TOKEN` as an environment variable. Render provides `PORT` and `RENDER_EXTERNAL_URL` automatically.

Free web services may spin down when idle and have limited resources; video downloads can time out or exceed Telegram upload limits.
