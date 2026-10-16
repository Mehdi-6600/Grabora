# Changelog

## [Unreleased]

### Fixed
- **CRITICAL: `/start` (and every other message) could be silently swallowed, making the bot look dead.**
  `ThrottlingMiddleware` runs on `dp.message`, i.e. it wraps the whole router chain (`Dispatcher.propagate_event`
  → `_propagate_event` → sub-routers), and it returned `None` for any message sent within `THROTTLE_SECONDS`
  (2s) of the same user's previous message. In aiogram 3 a middleware returning `None` makes the dispatcher log
  `Update id=N is handled` and stop — no router runs, no reply is sent, no error is written. Three `/start` taps
  in a row could therefore produce **zero** replies. Commands and reply-keyboard buttons (pure navigation) are
  no longer throttled, and a drop is now logged at `WARNING` with the user, the elapsed time and the text
  preview. Free text and URLs keep their anti-spam throttle.
- **Polling can no longer die silently**: `log_polling_exit()` reports why `start_polling` returned, with an
  explicit hint for `TelegramConflictError` ("another process is polling the same BOT_TOKEN"), instead of the
  service restarting in a loop without a reason in the log.

### Added
- **`/ping`** → answers `pong` from a middleware registered *before* all others (`bot/middlewares/ping.py`), so it
  works even when throttling, the access middleware, the database or any router is broken. It is the
  "is the bot alive?" probe: `/ping` replies while `/start` does not ⇒ the process and Telegram connection are
  fine and the message is being dropped further down the chain.
- **Fallback handlers** (`bot/handlers/fallback.py`, included last): any message or callback no router claims is
  logged at `WARNING` with user, chat, content type and text preview, and answered — silence is never the
  outcome.
- Middleware diagnostics: `ThrottlingMiddleware` (pass/drop with reasons), `AccessMiddleware` (granted/denied and
  a `BANNED` answer for callbacks too), `AdminMiddleware`/`AdminPanelMiddleware` (reject/pass) all log what they
  do with an update; enable `LOG_LEVEL=DEBUG` for the per-update lines.
- `main.build_dispatcher()`: the middleware/router wiring now lives in one function used by both `main()` and the
  tests, which log the wiring at startup. The dispatcher/middleware/router tests in `tests/` were switched to
  it, so a wiring regression fails the suite instead of reaching production.
- `tests/test_bot_reachability.py`: `/start` from a regular non-admin user must reply, three rapid `/start` taps
  must all reply, `/ping` must reply even when the access middleware's DB call explodes, unmatched messages must
  be logged and answered, and polling deaths must be reported.

### Added
- **Independent admin panel** (`/admin`, alias `/panel`): a dedicated reply keyboard (`📊 آمار`,
  `💳 پرداخت‌های در انتظار`, `💰 قیمت‌ها`, `💳 شماره کارت`, `📢 پیام همگانی`, `🔧 تست اطلاع‌رسانی`,
  `❌ خروج از پنل`) handled by the new `bot/handlers/admin_panel.py` router. Every button reuses the exact body
  of its slash command, pending payments keep their inline approval buttons, and the exit button restores the
  user keyboard. Non-admins receive `ADMIN_ONLY` instead of reaching the panel.
- **`/test_alert`** (`🔧 تست اطلاع‌رسانی`): sends a test notification to every admin and reports per-admin
  success/failure plus the fix when one is unreachable.
- `bot/utils/admin_notify.py`: one delivery path (`send_to_admins`) for all admin notifications, with Markdown
  escaping, one retry for transient errors, a plain-text fallback, and a per-admin result object.
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
- **Default package prices updated**: monthly 800,000 IRR / 80 Stars, quarterly 2,100,000 IRR / 210 Stars,
  yearly 7,200,000 IRR / 720 Stars, 2K addon 500,000 IRR / 50 Stars (i.e. 80,000 / 210,000 / 720,000 / 50,000
  Toman). These are seed values: a database that already has a `prices` row keeps its stored value.
- All payment code (card-to-card instructions, Stars stages, subscription views) now reads prices from the
  database instead of the `config` dictionaries; `PACKAGE_PRICES_IRR` / `PACKAGE_PRICES_STARS` are seed-only
  defaults.

### Fixed
- **Admin notifications for card payments could be lost silently**: the payment card is sent with
  `parse_mode="Markdown"` and embedded the payer's name and username unescaped, so a single unbalanced `_`, `*`
  or backtick (`Ali_Reza` + `ali_reza_72x` is enough) made Telegram reject the whole message with
  "can't parse entities" — the admin simply never saw the payment. User-controlled values are now escaped, the
  message is resent as plain text if formatting is still rejected, transient errors are retried once, and every
  failure is logged at `ERROR` level (previously: a `WARNING` with no hint).
- Cards payments and Stars payments now share that delivery path, so the Stars admin notice can no longer be
  swallowed by a `logger.debug` call.
- The "pending payments" empty state is now `هیچ پرداخت در انتظاری نیست.`
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
