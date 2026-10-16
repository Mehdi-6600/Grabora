"""Tests for YouTube cookie handling, client fallbacks and bot-check retries."""

import base64
from pathlib import Path

import pytest

from downloader import ytdlp_wrapper as w

COOKIE_TEXT = (
    "# Netscape HTTP Cookie File\n"
    ".youtube.com\tTRUE\t/\tTRUE\t0\tSID\ttest-session-value\n"
)
COOKIE_B64 = base64.b64encode(COOKIE_TEXT.encode()).decode()


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    """Isolate module-level caches and config between tests."""
    w.reset_cookie_cache()
    yield
    w.reset_cookie_cache()


def _set_cookies(monkeypatch, value: str, path: Path | None = None):
    monkeypatch.setattr(w, "YOUTUBE_COOKIES_B64", value, raising=False)
    if path is not None:
        monkeypatch.setattr(w, "YOUTUBE_COOKIES_FILE", str(path), raising=False)
        w.reset_cookie_cache()


# ---------------------------------------------------------------------------
# Bot-check detection
# ---------------------------------------------------------------------------


def test_bot_check_detection_matches_youtube_wording():
    assert w._is_bot_check(Exception("Sign in to confirm you're not a bot"))
    assert w._is_bot_check(Exception("Sign in to confirm you’re not a bot"))
    assert w._is_bot_check(RuntimeError("ERROR: [youtube] abc: Sign in to confirm you're not a bot. Use --cookies"))
    assert w._is_bot_check(Exception("Sign in to confirm your age"))
    assert w._is_bot_check(Exception("login_required"))


def test_bot_check_detection_ignores_unrelated_errors():
    assert not w._is_bot_check(Exception("Video unavailable"))
    assert not w._is_bot_check(Exception("HTTP Error 404: Not Found"))
    assert not w._is_bot_check(TimeoutError("read timed out"))
    assert not w._is_bot_check(RuntimeError("Downloaded file not found"))


def test_bot_check_follows_exception_chain():
    """yt-dlp wraps the extractor error in a DownloadError."""
    inner = Exception("Sign in to confirm you're not a bot")
    outer = RuntimeError("ERROR: unable to download video")
    outer.__cause__ = inner
    assert w._is_bot_check(outer)


# ---------------------------------------------------------------------------
# Cookie file resolution
# ---------------------------------------------------------------------------


def test_missing_cookies_warns_and_disables_cookiefile(monkeypatch, caplog):
    _set_cookies(monkeypatch, "")
    with caplog.at_level("WARNING"):
        assert w.prepare_youtube_cookies() is False
        opts = w._base_opts(Path("/tmp"))
        w._apply_cookies(opts)
    assert "cookiefile" not in opts
    assert "YOUTUBE_COOKIES_B64 not set" in caplog.text


def test_cookies_are_decoded_to_file_and_passed_to_ytdlp(monkeypatch, tmp_path):
    target = tmp_path / "cookies.txt"
    _set_cookies(monkeypatch, COOKIE_B64, target)

    assert w.prepare_youtube_cookies() is True
    assert target.read_bytes().decode() == COOKIE_TEXT

    opts = w._base_opts(Path("/tmp"))
    w._apply_cookies(opts)
    assert opts["cookiefile"] == str(target)


def test_cookies_file_is_private(monkeypatch, tmp_path):
    target = tmp_path / "cookies.txt"
    _set_cookies(monkeypatch, COOKIE_B64, target)
    w.prepare_youtube_cookies()
    # Cookie files grant account access — owner-only.
    assert (target.stat().st_mode & 0o077) == 0


def test_cookies_accept_wrapped_base64(monkeypatch, tmp_path):
    """People paste multi-line base64; whitespace must be ignored."""
    target = tmp_path / "cookies.txt"
    wrapped = "\n".join(COOKIE_B64[i : i + 20] for i in range(0, len(COOKIE_B64), 20))
    _set_cookies(monkeypatch, wrapped, target)
    assert w.prepare_youtube_cookies() is True
    assert target.read_bytes().decode() == COOKIE_TEXT


def test_invalid_base64_is_non_fatal(monkeypatch, tmp_path, caplog):
    _set_cookies(monkeypatch, "!!!not-base64!!!", tmp_path / "cookies.txt")
    with caplog.at_level("ERROR"):
        assert w.prepare_youtube_cookies() is False
    assert "not valid base64" in caplog.text
    # The secret must never be echoed into the logs.
    assert "!!!not-base64!!!" not in caplog.text


def test_non_netscape_payload_is_refused(monkeypatch, tmp_path, caplog):
    """A payload yt-dlp cannot load must be ignored, never written.

    Verified against yt-dlp: passing such a file raises
    ``DownloadError: Cookies file must be Netscape formatted, not JSON`` on
    *every* download, i.e. one bad secret would break all sites. Ignoring it
    keeps the previous anonymous behaviour instead.
    """
    payload = base64.b64encode(b'{"cookies": []}').decode()
    target = tmp_path / "cookies.txt"
    _set_cookies(monkeypatch, payload, target)

    with caplog.at_level("ERROR"):
        assert w.prepare_youtube_cookies() is False  # no cookies in use

    assert not target.exists(), "an unloadable cookies file must not be written"
    assert "Ignoring YOUTUBE_COOKIES_B64" in caplog.text
    assert '{"cookies": []}' not in caplog.text  # the payload is never echoed


def test_header_only_payload_is_refused(monkeypatch, tmp_path, caplog):
    """A header-only export carries no cookies: it must not be reported as active."""
    payload = base64.b64encode(b"# Netscape HTTP Cookie File\n\n").decode()
    target = tmp_path / "cookies.txt"
    _set_cookies(monkeypatch, payload, target)

    with caplog.at_level("ERROR"):
        assert w.prepare_youtube_cookies() is False

    assert not target.exists()
    assert "contains no cookie rows" in caplog.text


# ---------------------------------------------------------------------------
# Client fallbacks
# ---------------------------------------------------------------------------


def test_client_ladders_only_use_supported_clients(monkeypatch, tmp_path):
    _set_cookies(monkeypatch, COOKIE_B64, tmp_path / "cookies.txt")
    supported = w._supported_clients()
    assert supported, "yt-dlp client list should be introspectable"

    attempts = w._client_attempts()
    # With cookies, yt-dlp defaults come first.
    assert attempts[0] is None
    for clients in attempts[1:]:
        assert clients, "empty ladders must be dropped"
        for client in clients:
            assert client in supported
    # 'tv_embedded' is gone from modern yt-dlp builds and must be filtered out.
    if "tv_embedded" not in supported:
        assert all("tv_embedded" not in (c or ()) for c in attempts)


def test_without_cookies_explicit_clients_are_used_first(monkeypatch, tmp_path):
    _set_cookies(monkeypatch, "", tmp_path / "cookies.txt")
    attempts = w._client_attempts()
    assert attempts[0], "a client fallback should be tried without cookies"
    assert "web_creator" in attempts[0]

    opts = w._base_opts(tmp_path)
    w._apply_clients(opts, attempts[0])
    assert opts["extractor_args"]["youtube"]["player_client"] == list(attempts[0])


def test_unknown_clients_are_ignored(monkeypatch, tmp_path):
    _set_cookies(monkeypatch, "", tmp_path / "cookies.txt")
    monkeypatch.setattr(w, "_CLIENT_LADDERS", (("web_creator", "definitely_not_a_client"),))

    attempts = w._client_attempts()
    assert attempts == [("web_creator",)]


def test_apply_clients_skipped_for_defaults(tmp_path):
    opts = w._base_opts(tmp_path)
    w._apply_clients(opts, None)
    assert "extractor_args" not in opts


# ---------------------------------------------------------------------------
# Retry ladder
# ---------------------------------------------------------------------------


class _FakeYDL:
    """Stands in for yt_dlp.YoutubeDL, failing the first N attempts."""

    failures: list[Exception] = []
    calls: list[dict] = []
    output_path: Path | None = None

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def extract_info(self, url, download=False):
        _FakeYDL.calls.append(self.opts)
        if _FakeYDL.failures:
            raise _FakeYDL.failures.pop(0)
        return {"id": "abc", "title": "T", "ext": "mp4"}

    def prepare_filename(self, info):
        assert _FakeYDL.output_path is not None
        return str(_FakeYDL.output_path)


@pytest.fixture
def fake_ydl(monkeypatch, tmp_path):
    _FakeYDL.failures = []
    _FakeYDL.calls = []
    out = tmp_path / "video.mp4"
    out.write_bytes(b"x" * 10)
    _FakeYDL.output_path = out
    monkeypatch.setattr(w.yt_dlp, "YoutubeDL", _FakeYDL)
    return _FakeYDL


def test_retries_with_next_client_on_bot_check(monkeypatch, tmp_path, fake_ydl):
    _set_cookies(monkeypatch, "", tmp_path / "cookies.txt")
    ladder = w._client_attempts()

    fake_ydl.failures = [Exception("Sign in to confirm you're not a bot")]
    path = w._download("https://youtu.be/abc", "480", "video", tmp_path)

    assert path == fake_ydl.output_path
    assert len(fake_ydl.calls) == 2
    assert fake_ydl.calls[0]["extractor_args"]["youtube"]["player_client"] == list(ladder[0])
    assert fake_ydl.calls[1]["extractor_args"]["youtube"]["player_client"] == list(ladder[1])


def test_no_retry_for_unrelated_errors(monkeypatch, tmp_path, fake_ydl):
    _set_cookies(monkeypatch, "", tmp_path / "cookies.txt")
    fake_ydl.failures = [Exception("Video unavailable")]

    with pytest.raises(Exception, match="Video unavailable"):
        w._download("https://youtu.be/abc", "480", "video", tmp_path)
    assert len(fake_ydl.calls) == 1


def test_retry_ladder_is_exhausted_before_giving_up(monkeypatch, tmp_path, fake_ydl):
    _set_cookies(monkeypatch, "", tmp_path / "cookies.txt")
    attempts = w._client_attempts()

    fake_ydl.failures = [Exception("Sign in to confirm you're not a bot")] * len(attempts)
    with pytest.raises(Exception, match="not a bot"):
        w._download("https://youtu.be/abc", "480", "video", tmp_path)
    assert len(fake_ydl.calls) == len(attempts)


def test_cookies_attempt_uses_default_clients_first(monkeypatch, tmp_path, fake_ydl):
    _set_cookies(monkeypatch, COOKIE_B64, tmp_path / "cookies.txt")
    fake_ydl.failures = [Exception("Sign in to confirm you're not a bot")]

    w._download("https://youtu.be/abc", "480", "video", tmp_path)

    first, second = fake_ydl.calls[0], fake_ydl.calls[1]
    assert first["cookiefile"] == str(tmp_path / "cookies.txt")
    assert "extractor_args" not in first  # default clients with cookies
    assert second["cookiefile"] == str(tmp_path / "cookies.txt")
    assert second["extractor_args"]["youtube"]["player_client"]


def test_audio_extension_and_format_kept_across_retries(monkeypatch, tmp_path, fake_ydl):
    _set_cookies(monkeypatch, "", tmp_path / "cookies.txt")
    mp3 = tmp_path / "audio [abc].mp3"
    mp3.write_bytes(b"y" * 10)
    fake_ydl.output_path = mp3
    fake_ydl.failures = [Exception("Sign in to confirm you're not a bot")]

    path = w._download("https://youtu.be/abc", "320", "audio", tmp_path)

    assert path == mp3
    for opts in fake_ydl.calls:
        assert opts["format"] == "bestaudio/best"
        assert opts["postprocessors"][0]["preferredquality"] == "320"
