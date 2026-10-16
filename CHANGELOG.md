# Changelog

## [Unreleased]

### Added
- **Runtime-configurable prices**: a new `prices` table (`package_key`, `price_irr`, `price_stars`, `updated_at`)
  seeds the `config.PACKAGE_PRICES_IRR` / `PACKAGE_PRICES_STARS` defaults on first start with `INSERT OR IGNORE`,
  so admin edits are never overwritten on restart.
- **`/prices` admin command**: `/prices` prints every package price (IRR + Stars); `/prices <key> <irr> <stars>`
  (e.g. `/prices monthly 80000 80`) updates one package at runtime and re-applies it to the existing neonpay
  Stars stages.
- Price helpers in `database/queries.py`: `get_price()`, `get_all_prices()` and `set_price()`.
- **YouTube cookie support** (`YOUTUBE_COOKIES_B64`): a base64-encoded `cookies.txt` is decoded at startup to `YOUTUBE_COOKIES_FILE` (default `/tmp/cookies.txt`, mode `0600`) and passed to `yt-dlp` as `cookiefile`. Required on cloud/datacenter IPs such as Render, where YouTube answers `Sign in to confirm you're not a bot`. If unset, the bot still starts and logs a warning.
- **Player-client fallback ladder**: when YouTube demands a sign-in, downloads retry through alternative `yt-dlp` player clients (`web_creator`/`android_vr` → `tv_simply`/`ios` → `web_embedded`/`mweb`/`tv`). Client names unsupported by the installed `yt-dlp` build are filtered out automatically (e.g. `tv_embedded`, removed in recent versions), and the client list is introspected defensively with a static fallback.
- README section "YouTube cookies (cloud hosting)" with cookie export/base64 instructions and guidance on expiry and secret handling.

### Changed
- All payment code (card-to-card instructions, Stars stages, subscription views) now reads prices from the
  database instead of the `config` dictionaries; `PACKAGE_PRICES_IRR` / `PACKAGE_PRICES_STARS` are seed-only
  defaults.

### Fixed
- **Reply-keyboard buttons work**: `اشتراک`, `راهنما`, `👤 حساب من` and `🚀 دانلود` are plain-text messages, not
  commands, and previously matched no handler (only the slash commands worked). The button labels now live in
  `bot/keyboards/reply.py` as `BTN_*` constants that are used both to build the keyboard and to match incoming
  text, and each button has its own handler sharing the same body as its slash command.
- Downloads no longer fail permanently on YouTube's bot check: bot-check errors are detected (including wrapped `DownloadError` causes) and retried with the next client set instead of aborting on the first attempt.

## [0.2.0]

### Changed
- **Format gating reworked**: 720p and 1080p now require an active subscription (previously free users could request them); free users keep 144p–480p, and 1440p (2K) still requires the 2K addon.
- **File-size gating**: free users are limited to 500 MB per file (`FREE_MAX_FILE_SIZE_MB`), subscribers to 1900 MB (`MAX_FILE_SIZE_MB`). Enforced before the file is sent, using a pre-download metadata size estimate plus a post-download check on the real file.
- **Card-to-card (IRR) payment replaced Zarinpal**: users pay to a card and send a tracking number; an admin approves manually. Package prices come from a single `PACKAGE_PRICES_IRR` / `PACKAGE_PRICES_STARS` source.
- Card details and amounts are configurable (`CARD_NUMBER`, `CARD_HOLDER`, seeded into a new `settings` table and editable at runtime with `/card`).
- Access decisions now return an `AccessResult` enum (`bot/middlewares/access.py`) instead of a boolean, shared by the middleware and the download handler.

### Added
- Card-to-card flow: package picker → `💳 پرداخت با کارت` → card instructions → tracking number → admin approval (`bot/handlers/card_payment.py`).
- Admin commands: `/pending`, `/card`, `/card <number> | <holder>`, `/reject <payment_id>`.
- Admin approval keyboard with four package buttons where only the chosen package is active (the rest are 🔒 / `noop`).
- Duplicate-tracking-number detection: warns the admin and refuses to auto-activate.
- `payments.tracking_number` column and a partial unique index preventing the same tracking number from being verified twice.
- `settings` table (`key TEXT PRIMARY KEY`, `value TEXT NOT NULL`) with safe `INSERT OR IGNORE` seeding.
- Safe startup migrations for `payments.tracking_number`, `settings`, and `users.reminder_sent_for_expiry`.
- New messages: `UPGRADE_FOR_QUALITY`, `UPGRADE_FOR_SIZE`, `CARD_PAYMENT_INSTRUCTIONS`, `TRACKING_RECEIVED`, `TRACKING_INVALID`, `PENDING_EXISTS`, `ADMIN_NEW_PAYMENT`, `ADMIN_DUPLICATE_TRACKING_WARNING`, `PAYMENT_REJECTED`, `CARD_CURRENT`, `CARD_UPDATED`, `TIER_LABELS`.
- Shared `activate_subscription()` used by both the Stars and card paths (idempotent).
- Test suite expanded from 7 to 26 tests (access/size gating, quality gating, card-to-card approval flow).

### Removed
- Zarinpal payment provider (`payments/zarinpal.py`) and the FastAPI/uvicorn callback server (`payments/callback_server.py`).
- `fastapi`, `uvicorn[standard]`, and `irgateway` dependencies; no HTTPS callback endpoint or merchant ID is needed to run the bot.
- Legacy `TIER_PRICES_IRR` / `TIER_PRICES_STARS` dicts, the `ADDON_2K_IRR` / `ADDON_2K_STARS` duplicates, and the unused `_get_bool()` config helper.

### Fixed
- **Stars payments crashed with `TypeError`**: `payments/stars.py` passed `transaction_id=` to `create_payment()`, which had no such parameter. The parameter is now accepted and persisted.
- **`update_payment_status()` wiped `transaction_id`**: the column was always overwritten (with `NULL`) when activation was called without a transaction ID, breaking Stars replay idempotency. It now uses `COALESCE` so omitted values leave existing data intact.
- The Stars notification hook in `main.py` sent the raw `PAYMENT_SUCCESS` template without formatting; it was dead code and has been removed (notifications are sent, formatted, from `_handle_stars_payment`).

## [0.1.0] — MVP

### Added
- Async Telegram bot (Aiogram 3.x) with yt-dlp download engine and ffmpeg audio extraction.
- Subscription tiers (free/monthly/quarterly/yearly) + 2K addon.
- Quality gating with locked formats and upgrade prompts.
- Telegram Stars payments (neonpay) and Zarinpal payments (irgateway) — *both gateways; Zarinpal removed in 0.2.0*.
- FastAPI callback server for Zarinpal verification — *removed in 0.2.0*.
- SQLite database with PostgreSQL-ready schema.
- APScheduler jobs: temp cleanup, daily counter reset, expiry checks & reminders.
- Admin commands: stats, broadcast, ban/unban, give/revoke, payments.
- Throttling and access middlewares.
- Rotating file logging (5 MB × 3).
- Test suite (7 tests).
