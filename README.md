# Telegram Video Downloader (Render Web Service)

## Deploy
1. Upload the contents of this folder to the root of a GitHub repository.
2. In Render, choose New > Web Service and connect the repository, or use Blueprint from `render.yaml`.
3. Choose Docker runtime. If the free plan is available on your account, select it.
4. Add `TELEGRAM_BOT_TOKEN` from Telegram's official `@BotFather`.
5. Set `WEBHOOK_SECRET` to a random string of at least 16 characters if it was not generated automatically.
6. Deploy. Render supplies `RENDER_EXTERNAL_URL` automatically.
7. Open the bot in Telegram and send `/start`.

## Notes
- This is a starter project and has not been live-tested on your Render account.
- Free web services may sleep when idle; webhook delivery may be delayed while sleeping.
- Downloads are limited to approximately 48 MB by this project; Telegram limits and hosting resources may vary.
- `yt-dlp` supports many sites, but no site or individual video is guaranteed to work.
- It does not bypass logins, paywalls, DRM, or access restrictions. Download only content you have permission to save.
