# Grabora — Telegram Media Downloader Bot

**Grabora** is an async Telegram bot that downloads video and audio from 1800+ websites (YouTube, Instagram, Twitter/X, Facebook, and more) using `yt-dlp`, with tiered subscriptions, quality gating, and payments via Telegram Stars and card-to-card (Iranian Rial).

**Grabora** یک ربات تلگرامی async برای دانلود ویدیو و صدا از بیش از ۱۸۰۰ وب‌سایت (یوتیوب، اینستاگرام، توییتر/ایکس، فیسبوک و ...) با استفاده از `yt-dlp` است که دارای اشتراک‌های سطح‌بندی‌شده، محدودیت کیفیت و پرداخت با تلگرام‌استارز و کارت به کارت (ریال ایران) می‌باشد.

---

## Features

- **Download engine**: `yt-dlp` (1800+ sites) + `ffmpeg` for audio extraction (MP3/FLAC).
- **Quality selection**: inline buttons before download; locked formats trigger an upgrade prompt.
- **Format gating**: free users get video up to 480p and audio up to 192 kbps; 720p/1080p require a subscription and 1440p (2K) requires the 2K addon.
- **File-size gating**: 500 MB per file for free users, 1900 MB for subscribers — enforced before the send, using a pre-download metadata estimate plus a post-download check.
- **Subscription tiers**: free / monthly / quarterly / yearly + optional 2K addon.
- **Daily download limits** with automatic daily reset.
- **Payments**: Telegram Stars (via `neonpay`, automatic activation) and card-to-card in IRR (manual admin approval).
- **Admin tools**: `/stats`, `/broadcast`, `/ban`, `/unban`, `/give`, `/revoke`, `/payments`, `/pending`, `/card`, `/reject`.
- **Queue & throttling**: global concurrency 3, per-user 1, 1 msg/2s throttle.
- **Scheduler**: temp-file cleanup, daily counter reset, subscription expiry checks & reminders.
- **Logging**: rotating file handler (5 MB × 3 backups), domain-only URLs.
- **PostgreSQL-ready** SQLite schema (TIMESTAMP/INTEGER/TEXT only).

## Supported platforms

YouTube, Instagram, Twitter/X, Facebook, TikTok, Vimeo, SoundCloud, and 1800+ sites supported by `yt-dlp`. Single media only (playlists take the first video).

## Pricing

| Package | Duration | Daily limit | IRR | Stars |
|---|---|---|---|---|
| Free | — | 3 | 0 | 0 |
| Monthly | 30 days | 30 | 50,000 | 50 |
| Quarterly | 90 days | 50 | 120,000 | 120 |
| Yearly | 365 days | Unlimited | 400,000 | 400 |
| 2K Addon | 30 days | — | 30,000 | 30 |

IRR amounts are displayed with thousand separators. The card-to-card flow reads prices from `PACKAGE_PRICES_IRR`; Stars invoices use `PACKAGE_PRICES_STARS`.

## Access matrix

| Capability | Free | Paid (monthly/quarterly/yearly) | 2K addon holders |
|---|---|---|---|
| Video | up to 480p | up to 1080p | + 1440p (2K) |
| Audio | up to 192 kbps | 320 kbps + FLAC | — |
| Max file size | 500 MB | 1900 MB | 1900 MB |
| Daily downloads | 3 | 30 / 50 / unlimited | — |

720p and 1080p require an active subscription; 1440p additionally requires an active 2K addon.

## Architecture

```
User
  │
  ▼
Telegram ──► Aiogram Dispatcher
                │
                ├── Middlewares: Throttling (1 msg/2s), TrackingCancel (FSM), Access (user/ban/tier)
                │
                ├── Handlers: start, download, card_payment, subscription, admin
                │       │
                │       ├── Downloader Queue (global 3 / per-user 1)
                │       │       └── yt-dlp + ffmpeg ──► size/quality gate ──► temp file ──► Telegram send
                │       │
                │       └── Payments
                │               ├── Stars ──► neonpay ──► Telegram invoice ──► auto activate
                │               └── Card ──► user sends tracking number ──► admin approval
                │                                                              └── activate + notify
                │
                ├── Database (SQLite / PostgreSQL-ready)
                │       ├── users, payments, download_logs
                │       └── settings (card_number, card_holder — editable via /card)
                │
                └── Scheduler (APScheduler)
                        ├── temp-file cleanup (hourly)
                        ├── daily counter reset (hourly)
                        ├── subscription expiry downgrade (daily)
                        └── expiry reminders (daily)
```

## Tech stack

- Python 3.11+
- Aiogram 3.x (async)
- yt-dlp + ffmpeg
- SQLite via aiosqlite (PostgreSQL-ready)
- APScheduler
- python-dotenv
- neonpay (Telegram Stars)
- Python `logging` (RotatingFileHandler)

## Project structure

```
grabora/
├── .env.example
├── .gitignore
├── README.md
├── CHANGELOG.md
├── requirements.txt
├── main.py
├── config.py
├── database/
│   ├── __init__.py
│   ├── db.py
│   ├── models.py
│   └── queries.py
├── bot/
│   ├── __init__.py
│   ├── handlers/
│   │   ├── start.py
│   │   ├── download.py
│   │   ├── card_payment.py
│   │   ├── subscription.py
│   │   └── admin.py
│   ├── keyboards/
│   │   ├── inline.py
│   │   └── reply.py
│   ├── middlewares/
│   │   ├── access.py
│   │   └── throttling.py
│   └── utils/
│       ├── messages.py
│       └── helpers.py
├── downloader/
│   ├── __init__.py
│   ├── ytdlp_wrapper.py
│   └── queue.py
├── payments/
│   ├── __init__.py
│   └── stars.py
├── tests/
└── logs/
    └── .gitkeep
```

## Setup

### Prerequisites

- Python 3.11+
- `ffmpeg` in PATH
- (Optional) local Telegram Bot API server for 2 GB uploads

No payment gateway account, merchant ID, domain, or HTTPS callback endpoint is required — card-to-card payments are approved by an admin inside Telegram.

### Install

```bash
git clone https://github.com/Mehdi-6600/Grabora.git
cd Grabora
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
# edit .env with your values
```

### Run

```bash
.venv/bin/python main.py
```

### Local Bot API server (optional, for 2 GB uploads)

Set `BOT_API_URL=http://localhost:8081` in `.env` and run a local Bot API server. Otherwise the cloud API (50 MB limit) is used.

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `BOT_TOKEN` | — | Telegram bot token (required) |
| `BOT_API_URL` | `https://api.telegram.org` | Bot API base URL |
| `ADMIN_IDS` | — | Comma-separated admin user IDs |
| `DATABASE_PATH` | `./grabora.db` | SQLite database path |
| `TEMP_DIR` | `./tmp/downloads` | Temp download directory |
| `MAX_FILE_SIZE_MB` | `1900` | Max file size for subscribers (free users are capped at 500 MB) |
| `CARD_NUMBER` | — | Card number shown to users (seeded into `settings`, editable via `/card`) |
| `CARD_HOLDER` | — | Card holder name shown to users (editable via `/card`) |
| `LOG_LEVEL` | `INFO` | Logging level |
| `LOG_FILE` | `logs/grabora.log` | Log file path |
| `CLEANUP_INTERVAL_HOURS` | `1` | Temp cleanup interval |
| `TEMP_FILE_MAX_AGE_HOURS` | `1` | Temp file max age |

`CARD_NUMBER` / `CARD_HOLDER` are only the initial values: they are seeded into the `settings` table on first start and can be changed at runtime with `/card` without a restart (existing values are never overwritten on restart).

## Payments

### Telegram Stars (international)

Users pick a package, choose `⭐ پرداخت با استارز`, and `neonpay` sends the invoice. On a successful payment the shared `activate_subscription()` grants the tier/addon automatically and the user + admins are notified. Replays of the same `transaction_id` are idempotent.

### Card-to-card (Iranian Rial)

Paid overseas users use Stars; Iranian users pay by card and an admin approves manually. Amounts are in IRR with thousand separators.

## Card-to-card workflow for admins

1. The user runs `/subscription`, picks a package, and chooses `💳 پرداخت با کارت`.
2. The bot shows the card number and holder (from `settings`), the package, and the amount, then asks for the tracking number. The user sends it (4–64 chars, letters/digits/dashes; URLs and commands cancel the flow and the state is kept on invalid input).
3. The payment is stored as `pending_manual` (only one pending payment per user) and every admin receives a notification containing the user, package, amount, tracking number, time and payment ID.
4. The notification carries **four buttons** — `فعال‌سازی ماهانه`, `فعال‌سازی سه‌ماهه`, `فعال‌سازی سالانه`, `فعال‌سازی افزونه 2K` — with **only the chosen package active**; the others are marked 🔒 and do nothing.
5. The admin taps the active button: the subscription is activated, the user gets the success message with tier, expiry and daily limit, and the admin message is edited to `✅ تأیید شد توسط @admin_username`.
6. If the tracking number was already used by another accepted payment, the bot warns the admin and does **not** auto-activate — the case must be investigated manually.

### Admin commands

| Command | Description |
|---|---|
| `/pending` | List every `pending_manual` payment, each as its own message with the approval keyboard |
| `/card` | Show the current card number and holder from `settings` |
| `/card <number> \| <holder>` | Update the card details used in the instructions |
| `/reject <payment_id>` | Reject a pending payment and notify the user |

## Deployment (systemd)

```ini
[Unit]
Description=Grabora Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/grabora
ExecStart=/opt/grabora/.venv/bin/python main.py
Restart=always
RestartSec=5
EnvironmentFile=/opt/grabora/.env

[Install]
WantedBy=multi-user.target
```

## Deploy on Render (free tier)

Grabora can run on Render's free Web Service plan using the included Dockerfile and `.render.yaml`.

### One-click deploy via Blueprint
1. Push the repo to GitHub.
2. In Render dashboard: **New +** → **Blueprint** → select this repo.
3. Render reads `.render.yaml` and creates the service automatically.
4. Fill in required env vars (`BOT_TOKEN`, `ADMIN_IDS`, `CARD_NUMBER`, `CARD_HOLDER`).
5. Deploy.

### Manual deploy
1. **New +** → **Web Service** → select this repo.
2. Render auto-detects the Dockerfile.
3. Plan: **Free**.
4. Add the required env vars.
5. Deploy.

### Keep it awake (free tier)
Free Web Services sleep after 15 minutes of inactivity. Add the service URL to UptimeRobot (https://uptimerobot.com) with a 5-minute HTTP ping to:

```
https://<your-service>.onrender.com/health
```

### Free-tier limitations
- **512 MB RAM** — enough for the bot, but 1080p downloads may OOM.
- **Sleeps after 15 min idle**, cold start ~30 s (UptimeRobot prevents this).
- **Ephemeral filesystem**: `grabora.db` and `tmp/` are wiped on restart. For persistence, attach a Render persistent disk (paid).

## Testing

```bash
.venv/bin/pytest -q
```

## Roadmap

- PostgreSQL migration + Alembic
- Redis-backed queue for horizontal scaling
- Web dashboard for admins
- Referral system
- Multi-language support (English UI toggle)
- Persistent analytics (downloads history per user)
- Automatic bank-SMS matching for card payments

## License

MIT — see [LICENSE](LICENSE).

## Author

Mehdi-6600 — [github.com/Mehdi-6600](https://github.com/Mehdi-6600)
