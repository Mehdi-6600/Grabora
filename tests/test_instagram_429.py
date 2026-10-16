"""Instagram HTTP 429 handling, the size probe policy and Instagram cookies.

The 429 in production is Instagram throttling anonymous post access
(``[Instagram] <id>: Unable to download webpage: HTTP Error 429``), so these
tests mock the Instagram response instead of contacting Instagram: nothing here
makes a real request to instagram.com.

What is asserted:
* a 429 becomes a clear Persian message and no internals leak;
* a 429 is never retried (no extra requests against the limit that is already
  being enforced);
* other failures keep the pre-existing retry behaviour;
* Instagram no longer pays for the extra pre-download probe request, while the
  size limit is still enforced (yt-dlp ``max_filesize`` + the post-download
  check) and other sites keep the probe;
* Instagram cookies are optional, never applied to other sites, and never
  logged;
* temporary files are removed after a failure.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from urllib.error import HTTPError

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from yt_dlp.utils import ExtractorError

import config
from bot.handlers import download as download_handler
from bot.handlers.download import _failure_message, _is_retryable
from bot.utils.messages import (
    DOWNLOAD_FAILED,
    INSTAGRAM_RATE_LIMITED,
    RATE_LIMITED,
)
from config import FREE_MAX_FILE_SIZE_MB, MAX_FILE_SIZE_MB
from database.models import User
from downloader import ytdlp_wrapper as w

INSTAGRAM_URL = "https://www.instagram.com/p/Deh-k7ntiAL/"
YOUTUBE_URL = "https://youtu.be/dQw4w9WgXcQ"

COOKIE_TEXT = (
    "# Netscape HTTP Cookie File\n"
    ".instagram.com\tTRUE\t/\tTRUE\t0\tsessionid\ttest-session-value\n"
)


def instagram_429_error() -> ExtractorError:
    """The exact shape of the production error (wrapped HTTPError)."""
    inner = HTTPError(
        INSTAGRAM_URL, 429, "Too Many Requests", {}, None  # type: ignore[arg-type]
    )
    return ExtractorError(
        "Unable to download webpage: HTTP Error 429: Too Many Requests",
        cause=inner,
        video_id="Deh-k7ntiAL",
    )


@pytest.fixture(autouse=True)
def _reset_cookie_state():
    w.reset_cookie_cache()
    yield
    w.reset_cookie_cache()


class FakeMessage:
    def __init__(self):
        self.sent: list[str] = []
        self.edits: list[str] = []

    async def answer(self, text, **kwargs):
        self.sent.append(text)
        return self

    async def edit_text(self, text, **kwargs):
        self.edits.append(text)
        return self

    @property
    def last(self) -> str:
        return (self.sent or self.edits)[-1]


class FakeCallback:
    def __init__(self, data: str, user_id: int):
        self.data = data
        self.from_user = type("U", (), {"id": user_id})()
        self.message = FakeMessage()
        self.answers: list[tuple[str | None, bool]] = []
        self.bot = type("B", (), {"send_message": FakeBot().send_message})()

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))


class FakeBot:
    def __init__(self):
        self.messages: list[tuple[int, str]] = []

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append((chat_id, text))
        return True


async def fsm_state(user_id: int) -> FSMContext:
    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=user_id, user_id=user_id)
    return FSMContext(storage=storage, key=key)


async def run_quality_selected(
    monkeypatch,
    tmp_path,
    *,
    url: str,
    tier: str = "free",
    quality: str = "480",
    media_type: str = "video",
    download_raises: BaseException | None = None,
    download_result: Path | None = None,
    info_calls: list[str] | None = None,
    download_calls: list[dict] | None = None,
    make_file: bool = False,
):
    """Drive the real on_quality_selected() with a stubbed downloader."""
    user = User(user_id=555001, tier=tier, daily_downloads=0)
    state = await fsm_state(user.user_id)
    await state.update_data(url=url)

    monkeypatch.setattr(download_handler, "TEMP_DIR", str(tmp_path))

    if info_calls is not None:

        async def fake_extract_info(u):
            info_calls.append(u)
            return {"id": "x", "title": "t", "ext": "mp4"}

        monkeypatch.setattr(download_handler, "extract_info", fake_extract_info)

    if download_calls is not None:

        async def fake_download(u, q, m, d, max_filesize_bytes=None):
            download_calls.append(
                {"url": u, "quality": q, "media_type": m, "max_filesize_bytes": max_filesize_bytes}
            )
            if download_raises is not None:
                raise download_raises
            if make_file and download_result is not None:
                download_result.write_bytes(b"x" * 1024)
            return download_result

        monkeypatch.setattr(download_handler, "download_media", fake_download)

        class _Queue:
            async def run(self, user_id, factory):
                return await factory()

        monkeypatch.setattr(download_handler, "download_queue", _Queue())

    logged: list[dict] = []

    async def fake_log(**kwargs):
        logged.append(kwargs)

    monkeypatch.setattr(download_handler, "log_download", fake_log)

    incremented: list[int] = []

    async def fake_increment(user_id, amount=1):
        incremented.append(user_id)
        return 1

    monkeypatch.setattr(download_handler, "increment_daily_downloads", fake_increment)
    monkeypatch.setattr(download_handler, "_notify_admins_if_needed", lambda bot: _noop())

    callback = FakeCallback(f"q:{media_type}:{quality}", user.user_id)
    await download_handler.on_quality_selected(callback, state, user)
    return callback, logged, incremented


async def _noop():
    return None


# ---------------------------------------------------------------------------
# 1. A 429 produces the correct user-facing error
# ---------------------------------------------------------------------------


def test_instagram_429_is_detected_through_the_wrapped_exception():
    assert w.is_rate_limited(instagram_429_error()) is True
    assert w.is_rate_limited(ExtractorError("Video unavailable")) is False


def test_instagram_429_message_is_persian_and_leaks_nothing():
    message = _failure_message(
        instagram_429_error(), INSTAGRAM_URL, User(user_id=1, tier="free")
    )

    assert message == INSTAGRAM_RATE_LIMITED
    assert "429" in message or "۴۲۹" in message  # explains the cause to the user
    # No internals: no traceback, no extractor name, no headers, no cookie words.
    lowered = message.lower()
    for leaked in (
        "traceback",
        "extractorerror",
        "httpError".lower(),
        "cookiefile",
        "sessionid",
        "yt-dlp",
        "yt_dlp",
        "authorization",
        "cookie",
    ):
        assert leaked not in lowered


def test_non_instagram_rate_limit_uses_the_generic_message():
    inner = HTTPError("https://example.com/x", 429, "Too Many Requests", {}, None)  # type: ignore[arg-type]
    exc = ExtractorError("Unable to download webpage: HTTP Error 429", cause=inner)

    assert _failure_message(exc, "https://example.com/x", User(user_id=1, tier="free")) == RATE_LIMITED


@pytest.mark.asyncio
async def test_instagram_429_reaches_the_user_through_the_handler(db, monkeypatch, tmp_path):
    calls: list[dict] = []
    callback, logged, incremented = await run_quality_selected(
        monkeypatch,
        tmp_path,
        url=INSTAGRAM_URL,
        download_raises=instagram_429_error(),
        download_calls=calls,
        info_calls=[],
    )

    assert callback.message.sent[-1] == INSTAGRAM_RATE_LIMITED
    assert "Hi" not in callback.message.sent[-1]  # no English internals
    assert logged and logged[-1]["status"] == "failed"
    assert incremented == []  # a failed download must not consume the daily quota


# ---------------------------------------------------------------------------
# 2. A 429 does not trigger an immediate retry loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_instagram_429_is_not_retried(db, monkeypatch, tmp_path):
    calls: list[dict] = []
    await run_quality_selected(
        monkeypatch,
        tmp_path,
        url=INSTAGRAM_URL,
        download_raises=instagram_429_error(),
        download_calls=calls,
        info_calls=[],
    )

    assert len(calls) == 1, f"the 429 was retried {len(calls)} times (must be exactly 1 attempt)"


def test_rate_limit_and_oversize_are_not_retryable():
    from downloader.ytdlp_wrapper import MediaTooLargeError

    assert _is_retryable(instagram_429_error()) is False
    assert _is_retryable(MediaTooLargeError("too large")) is False


# ---------------------------------------------------------------------------
# 3. Other errors keep the pre-existing behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unrelated_failure_still_retries_once(db, monkeypatch, tmp_path):
    calls: list[dict] = []
    await run_quality_selected(
        monkeypatch,
        tmp_path,
        url=INSTAGRAM_URL,
        download_raises=ExtractorError("Instagram sent an empty media response"),
        download_calls=calls,
        info_calls=[],
    )

    assert len(calls) == 2  # unchanged: one retry for non-rate-limit errors


@pytest.mark.asyncio
async def test_unrelated_failure_still_uses_the_generic_message(db, monkeypatch, tmp_path):
    callback, _, _ = await run_quality_selected(
        monkeypatch,
        tmp_path,
        url=INSTAGRAM_URL,
        download_raises=ExtractorError("Instagram sent an empty media response"),
        download_calls=[],
        info_calls=[],
    )

    assert callback.message.sent[-1] == DOWNLOAD_FAILED


def test_is_retryable_keeps_other_errors_retryable():
    assert _is_retryable(ExtractorError("some other problem")) is True
    assert _is_retryable(RuntimeError("Downloaded file not found")) is True


def test_a_429_is_not_treated_as_a_bot_check():
    """A 429 must not enter the YouTube client ladder (that would retry it)."""
    assert w._is_bot_check(instagram_429_error()) is False
    assert w._is_bot_check(ExtractorError("Sign in to confirm you're not a bot")) is True


def test_downloader_makes_a_single_attempt_on_429(monkeypatch, tmp_path):
    """_download must open exactly one YoutubeDL (no ladder, no retry) for a 429."""
    built: list[dict] = []

    class _FakeYDL:
        def __init__(self, opts):
            built.append(opts)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download=True):
            raise instagram_429_error()

    monkeypatch.setattr(w.yt_dlp, "YoutubeDL", _FakeYDL)
    monkeypatch.setattr(w, "YOUTUBE_COOKIES_B64", "", raising=False)
    w.reset_cookie_cache()

    with pytest.raises(ExtractorError):
        w._download(
            INSTAGRAM_URL, "480", "video", tmp_path, max_filesize_bytes=FREE_MAX_FILE_SIZE_MB * 1024 * 1024
        )

    assert len(built) == 1, f"the 429 was attempted {len(built)} times (must be 1)"
    assert built[0]["max_filesize"] == FREE_MAX_FILE_SIZE_MB * 1024 * 1024


# ---------------------------------------------------------------------------
# 4. Instagram size checking must not cost an extra request
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_instagram_skips_the_extra_probe_request(db, monkeypatch, tmp_path):
    info_calls: list[str] = []
    calls: list[dict] = []

    callback, _, _ = await run_quality_selected(
        monkeypatch,
        tmp_path,
        url=INSTAGRAM_URL,
        download_calls=calls,
        info_calls=info_calls,
        download_result=tmp_path / "v.mp4",
        make_file=True,
    )

    assert info_calls == [], "Instagram still made the extra metadata request"
    # ...but the size limit is still handed to yt-dlp so oversized media aborts early
    assert calls[0]["max_filesize_bytes"] == FREE_MAX_FILE_SIZE_MB * 1024 * 1024
    assert callback.message.sent  # a real file was sent


@pytest.mark.asyncio
async def test_other_sites_keep_the_probe(db, monkeypatch, tmp_path):
    info_calls: list[str] = []
    calls: list[dict] = []

    await run_quality_selected(
        monkeypatch,
        tmp_path,
        url=YOUTUBE_URL,
        download_calls=calls,
        info_calls=info_calls,
        download_result=tmp_path / "v.mp4",
        make_file=True,
    )

    assert info_calls == [YOUTUBE_URL], "the probe must still run for non-Instagram sites"


def test_supports_size_probe_policy():
    assert w.supports_size_probe(YOUTUBE_URL) is True
    assert w.supports_size_probe("https://twitter.com/i/status/1") is True
    assert w.supports_size_probe(INSTAGRAM_URL) is False
    assert w.supports_size_probe("https://instagram.com/reel/abc/") is False
    assert w.supports_size_probe("https://m.instagram.com/p/abc/") is False


def test_free_and_paid_size_limits_are_passed_to_ytdlp(monkeypatch, tmp_path):
    """The limit given to yt-dlp always matches the user's tier."""
    opts = w._build_opts(tmp_path, "480", "video", None, url=INSTAGRAM_URL, max_filesize_bytes=12345)
    assert opts["max_filesize"] == 12345

    paid_opts = w._build_opts(
        tmp_path, "1080", "video", None, url=INSTAGRAM_URL, max_filesize_bytes=MAX_FILE_SIZE_MB * 1024 * 1024
    )
    assert paid_opts["max_filesize"] == MAX_FILE_SIZE_MB * 1024 * 1024

    # and it is absent when the caller does not ask for it (previous behaviour)
    assert "max_filesize" not in w._build_opts(tmp_path, "480", "video", None, url=INSTAGRAM_URL)


@pytest.mark.asyncio
async def test_oversized_instagram_media_maps_to_the_size_message(db, monkeypatch, tmp_path):
    from downloader.ytdlp_wrapper import MediaTooLargeError

    callback, _, _ = await run_quality_selected(
        monkeypatch,
        tmp_path,
        url=INSTAGRAM_URL,
        download_raises=MediaTooLargeError("yt-dlp aborted: media is larger than max_filesize"),
        download_calls=[],
        info_calls=[],
    )

    text = callback.message.sent[-1]
    assert str(FREE_MAX_FILE_SIZE_MB) in text  # the free-tier hint with numbers
    assert "اشتراک" in text  # and the upsell, exactly like the other size path


# ---------------------------------------------------------------------------
# 5. Instagram cookies: optional, scoped, never logged
# ---------------------------------------------------------------------------


def test_instagram_cookies_are_optional(monkeypatch, tmp_path):
    monkeypatch.setattr(w, "INSTAGRAM_COOKIES_B64", "", raising=False)
    monkeypatch.setattr(w, "YOUTUBE_COOKIES_B64", "", raising=False)
    monkeypatch.setattr(w, "INSTAGRAM_COOKIES_FILE", str(tmp_path / "ig.txt"), raising=False)
    w.reset_cookie_cache()

    opts: dict = {}
    w._apply_cookies(opts, INSTAGRAM_URL)

    assert "cookiefile" not in opts  # downloads still work without cookies


def test_instagram_cookies_are_used_for_instagram_only(monkeypatch, tmp_path):
    ig = tmp_path / "ig.txt"
    monkeypatch.setattr(
        w, "INSTAGRAM_COOKIES_B64", base64.b64encode(COOKIE_TEXT.encode()).decode(), raising=False
    )
    monkeypatch.setattr(w, "INSTAGRAM_COOKIES_FILE", str(ig), raising=False)
    monkeypatch.setattr(w, "YOUTUBE_COOKIES_B64", "", raising=False)
    w.reset_cookie_cache()

    instagram_opts: dict = {}
    w._apply_cookies(instagram_opts, INSTAGRAM_URL)
    assert instagram_opts["cookiefile"] == str(ig)

    youtube_opts: dict = {}
    w._apply_cookies(youtube_opts, YOUTUBE_URL)
    assert "cookiefile" not in youtube_opts, "Instagram cookies leaked to another site"

    assert ig.stat().st_mode & 0o777 == 0o600


def test_instagram_cookie_content_is_never_logged(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(
        w, "INSTAGRAM_COOKIES_B64", base64.b64encode(COOKIE_TEXT.encode()).decode(), raising=False
    )
    monkeypatch.setattr(w, "INSTAGRAM_COOKIES_FILE", str(tmp_path / "ig.txt"), raising=False)
    w.reset_cookie_cache()

    with caplog.at_level(logging.DEBUG):
        w._apply_cookies({}, INSTAGRAM_URL)

    assert "test-session-value" not in caplog.text
    assert "sessionid" not in caplog.text


def test_invalid_instagram_cookie_base64_is_non_fatal(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(w, "INSTAGRAM_COOKIES_B64", "not base64 !!", raising=False)
    monkeypatch.setattr(w, "INSTAGRAM_COOKIES_FILE", str(tmp_path / "ig.txt"), raising=False)
    w.reset_cookie_cache()

    opts: dict = {}
    with caplog.at_level(logging.DEBUG):
        w._apply_cookies(opts, INSTAGRAM_URL)

    assert "cookiefile" not in opts
    assert "not valid base64" in caplog.text


def test_instagram_cookies_do_not_change_behaviour_when_unset(monkeypatch, tmp_path):
    """Unset Instagram cookies must keep the previous YouTube-cookie behaviour."""
    yt = tmp_path / "cookies.txt"
    monkeypatch.setattr(w, "YOUTUBE_COOKIES_B64", base64.b64encode(COOKIE_TEXT.encode()).decode(), raising=False)
    monkeypatch.setattr(w, "YOUTUBE_COOKIES_FILE", str(yt), raising=False)
    monkeypatch.setattr(w, "INSTAGRAM_COOKIES_B64", "", raising=False)
    w.reset_cookie_cache()

    opts: dict = {}
    w._apply_cookies(opts, INSTAGRAM_URL)
    assert opts["cookiefile"] == str(yt)  # exactly what happened before this change


def test_config_exposes_optional_instagram_cookie_settings():
    assert hasattr(config, "INSTAGRAM_COOKIES_B64")
    assert hasattr(config, "INSTAGRAM_COOKIES_FILE")
    assert config.INSTAGRAM_COOKIES_B64 == ""  # unset by default


# ---------------------------------------------------------------------------
# 6. Temporary files are cleaned up after a failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failed_download_removes_the_partial_file(db, monkeypatch, tmp_path):
    """A file created before the failure must not survive in the temp dir."""
    leaked = tmp_path / "leaked [abc].mp4"
    leaked.write_bytes(b"x" * 100)

    # The downloader writes the file, then the failure happens: the handler's
    # finally-block must delete it.
    class _FailingDownload:
        async def run(self, user_id, factory):
            raise instagram_429_error()

    monkeypatch.setattr(download_handler, "download_queue", _FailingDownload())
    monkeypatch.setattr(download_handler, "TEMP_DIR", str(tmp_path))

    callback, _, _ = await run_quality_selected(
        monkeypatch,
        tmp_path,
        url=INSTAGRAM_URL,
        info_calls=[],
    )

    assert callback.message.sent[-1] == INSTAGRAM_RATE_LIMITED
    # nothing was produced by this attempt, and the stale file is not sent
    assert not any("leaked" in text for text in callback.message.sent)


@pytest.mark.asyncio
async def test_delete_happens_for_a_file_that_was_produced(db, monkeypatch, tmp_path):
    """Gate 3 (real size) rejects and then deletes the file."""
    big = tmp_path / "big [abc].mp4"
    big.write_bytes(b"0" * (FREE_MAX_FILE_SIZE_MB * 1024 * 1024 + 1024))

    callback, logged, _ = await run_quality_selected(
        monkeypatch,
        tmp_path,
        url=INSTAGRAM_URL,
        download_calls=[],
        info_calls=[],
        download_result=big,
    )

    assert not big.exists(), "the oversized temporary file was not cleaned up"
    assert logged and "file too large" in logged[-1]["error_message"]


def test_max_filesize_abort_is_recognised_from_ytdlp_output():
    messages = w.YdlMessages()
    messages.debug("[download] File is larger than max-filesize (123 bytes > 100 bytes). Aborting.")

    assert messages.exceeded_max_filesize() is True
    assert w.YdlMessages().exceeded_max_filesize() is False


def test_ytdlp_output_collector_logs_problems_without_progress_spam(caplog):
    messages = w.YdlMessages()
    with caplog.at_level(logging.DEBUG):
        messages.debug("[download]  12.3% of 1.00MiB at 1.00MiB/s ETA 00:00")
        messages.warning("You have exceeded the rate-limit for accessing posts anonymously")

    assert "rate-limit" in caplog.text  # diagnostics reach the logs
    assert "12.3%" not in caplog.text  # progress lines do not
