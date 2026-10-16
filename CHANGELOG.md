# Changelog

## [Unreleased]

### Fixed
- **Instagram downloads failed with `HTTP Error 429: Too Many Requests` and the bot answered with a generic
  "download failed".** Three verified causes, all in our code:
  *(a)* `bot/handlers/download.py` retried **every** exception immediately once (`for attempt in range(2)`,
  no backoff). For a 429 that is a second request against a limit that is already being enforced, which is
  what makes a throttled IP stay throttled. A rate limit is now detected through the whole exception chain
  (`__cause__`/`__context__`, so a wrapped `ExtractorError('Unable to download webpage: HTTP Error 429')` is
  caught) and is never retried; other errors keep their previous single retry.
  *(b)* Gate 2 (`_probe_size_mb`) ran a **second full extractor request** for the same URL before downloading.
  For Instagram that request is another anonymous `GET /p/{id}` — the exact request being rate-limited — and
  the post metadata usually has no `filesize` at all, so the request was spent and the estimate discarded.
  Instagram now skips the probe and gets `max_filesize` for the user's tier instead: `yt-dlp` aborts oversized
  media *before* writing it, and the post-download size check still runs for every download. Other sites keep
  the probe. (`downloader/ytdlp_wrapper.py::supports_size_probe`)
  *(c)* the failure text: a rate limit now answers with a Persian message that Instagram temporarily limited the
  bot's requests and to try later (`INSTAGRAM_RATE_LIMITED`; other sites get `RATE_LIMITED`), never a stack
  trace, header, URL parameter or cookie. A `max_filesize` abort maps back to the existing size-limit/upsell
  message instead of "download failed".
- **yt-dlp's own diagnostics were invisible.** With `quiet`/`no_warnings` the real reason for a failure never
  reached the log. All yt-dlp output now goes through `YdlMessages` (`logger=` param): warnings and errors are
  logged (the rate-limit notice included), progress lines are kept in a small ring buffer, and the buffer is
  what detects a `max_filesize` abort (`downloader/http.py` returns `False` silently, so no exception exists to
  catch).

### Added
- **Optional Instagram cookies**: `INSTAGRAM_COOKIES_B64` (+ `INSTAGRAM_COOKIES_FILE`, default
  `/tmp/instagram_cookies.txt`, written `0600`). A signed-in session makes Instagram use a different extraction
  path, which is the documented way around anonymous rate limiting. Unset by default: Instagram falls back to
  anonymous access and every other site is untouched (the option is never applied to a non-Instagram URL). The
  value is never logged, there is no login flow and no password handling — cookies do not bypass an IP-level
  limit. See the README section "Instagram rate limits (HTTP 429)".
- `downloader.ytdlp_wrapper.MediaTooLargeError`, plus `is_rate_limited()`, `is_instagram_url()` and
  `supports_size_probe()` helpers, and `download(..., max_filesize_bytes=...)`.
- `tests/test_instagram_429.py` (26 tests, all mocked — no request is ever sent to Instagram): detection through
  wrapped exceptions, no-retry on 429, retry kept for other errors, probe skipped for Instagram but kept
  elsewhere, tier limits passed to yt-dlp, cookie scoping/optionality/log-safety, temp-file cleanup, and the
  `max_filesize` output detection.

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
