"""yt-dlp wrapper (async): cookies, bot-check ladder and rate-limit handling."""

from __future__ import annotations

import asyncio
import base64
import binascii
import importlib
import logging
import threading
from collections import deque
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yt_dlp

from config import (
    INSTAGRAM_COOKIES_B64,
    INSTAGRAM_COOKIES_FILE,
    YOUTUBE_COOKIES_B64,
    YOUTUBE_COOKIES_FILE,
)

logger = logging.getLogger(__name__)

_COOKIE_LOCK = threading.Lock()
_cookie_file: Path | None = None
_cookie_resolved = False
_instagram_cookie_file: Path | None = None
_instagram_cookie_resolved = False


class MediaTooLargeError(RuntimeError):
    """yt-dlp refused the media because it exceeds the requested max size.

    Raised instead of a generic failure so the caller can answer with the
    existing "file too large" message rather than "download failed".
    """


# Hosts handled by the Instagram extractor (www./m. subdomains included).
_INSTAGRAM_HOSTS = ("instagram.com", "instagr.am")

# Text markers that mean "YouTube wants a signed-in session".
# Matched case-insensitively against the exception message chain.
_BOT_CHECK_MARKERS = (
    "not a bot",
    "sign in to confirm",
    "confirm your age",
    "login_required",
    "login required",
)

# Used when yt-dlp's private client registry cannot be introspected.
_FALLBACK_CLIENTS: frozenset[str] = frozenset(
    {
        "web",
        "web_safari",
        "web_embedded",
        "web_music",
        "web_creator",
        "android",
        "android_vr",
        "ios",
        "visionos",
        "mweb",
        "tv",
        "tv_downgraded",
        "tv_simply",
    }
)

# Player clients tried, in order, when YouTube demands a sign-in.
# 'tv_embedded' is kept for yt-dlp builds that still ship it; names the
# installed build does not know about are dropped before use.
_CLIENT_LADDERS: tuple[tuple[str, ...], ...] = (
    ("web_creator", "tv_embedded", "android_vr"),
    ("tv_simply", "android_vr", "ios"),
    ("web_embedded", "mweb", "tv"),
)


def _supported_clients() -> frozenset[str]:
    """Player clients the installed yt-dlp build actually supports.

    yt-dlp's client registry is private and has already moved modules once
    (``yt_dlp.extractor.youtube`` -> ``yt_dlp.extractor.youtube._base``), so
    probe both and fall back to a known-good static list rather than silently
    disabling the fallback ladder if introspection breaks again.
    """
    for module_path in (
        "yt_dlp.extractor.youtube._base",  # yt-dlp >= 2025
        "yt_dlp.extractor.youtube",  # older builds
    ):
        try:
            module = importlib.import_module(module_path)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not import %s: %s", module_path, exc)
            continue
        clients = getattr(module, "INNERTUBE_CLIENTS", None)
        if clients:
            return frozenset(name for name in clients if not name.startswith("_"))
    logger.debug("Could not introspect yt-dlp player clients; using the built-in list")
    return _FALLBACK_CLIENTS


# Text markers that mean "this site is throttling us" (HTTP 429).
# Instagram answers with 429 or redirects anonymous requests to its login page,
# and yt-dlp wraps both in ExtractorError('Unable to download webpage: ...'),
# so the markers are matched along the whole exception chain.
_RATE_LIMIT_MARKERS = (
    "http error 429",
    "too many requests",
    "429: too many",
    "rate-limit for accessing posts",
    "rate limited",
    "ratelimit",
)


def _exception_chain_text(exc: BaseException) -> str:
    """Joined, lower-cased text of an exception and its cause/context chain."""
    parts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        parts.extend(str(arg) for arg in current.args)
        parts.append(type(current).__name__)
        current = current.__cause__ or current.__context__
    return " ".join(parts).lower()


def _is_bot_check(exc: BaseException) -> bool:
    """True if the failure is YouTube's bot/age verification wall."""
    text = _exception_chain_text(exc)
    return any(marker in text for marker in _BOT_CHECK_MARKERS)


def is_rate_limited(exc: BaseException) -> bool:
    """True if the failure is a site-side rate limit (HTTP 429).

    Used by the download flow to stop retrying: a rate limit is not transient
    on a seconds timescale, and every retry is one more request against the
    limit that is already being enforced (which is what amplifies a 429).
    """
    text = _exception_chain_text(exc)
    return any(marker in text for marker in _RATE_LIMIT_MARKERS)


def is_instagram_url(url: str) -> bool:
    """True for instagram.com links (www./m. subdomains included)."""
    try:
        host = (urlparse(url).netloc or "").lower().split(":")[0]
    except Exception:  # noqa: BLE001
        return False
    host = host.removeprefix("www.").removeprefix("m.")
    return any(host == h or host.endswith("." + h) for h in _INSTAGRAM_HOSTS)


def supports_size_probe(url: str) -> bool:
    """Whether the pre-download size probe is worth its extra request.

    Instagram: no. The probe calls ``extract_info(download=False)``, which for an
    Instagram post is another anonymous ``GET /p/{id}`` (the request Instagram
    rate-limits), and the post metadata usually carries no ``filesize`` at all —
    so the request is spent and the estimate is discarded anyway. The existing
    post-download size check still enforces the limit for every download, and
    ``download(max_filesize_bytes=...)`` adds a pre-download abort with no extra
    request. Other sites keep the probe unchanged.
    """
    return not is_instagram_url(url)


def _clean_b64(value: str) -> str:
    """Strip whitespace/newlines from a pasted base64 blob."""
    return "".join(value.split())


# yt-dlp loads a cookies file with http.cookiejar.MozillaCookieJar, whose
# magic_re requires this marker on the FIRST line. A file without it is not
# "maybe wrong": yt-dlp raises (DownloadError: Cookies file must be Netscape
# formatted, ...) on every download that uses it, so it must never be written.
_NETSCAPE_HEADER = "Netscape HTTP Cookie File"
_HTTPONLY_PREFIX = "#HttpOnly_"
_COOKIE_ROW_FIELDS = 7


def _parse_cookie_row(line: str) -> tuple[str, str] | None:
    """Return ``(domain, cookie name)`` for one Netscape row, else ``None``.

    Mirrors ``YoutubeDLCookieJar.load()``'s field handling (``#HttpOnly_``
    prefix, ``#`` comments, exactly 7 tab-separated fields). Only the domain and
    the cookie *name* are returned — cookie values are never read or logged.
    """
    line = line.rstrip("\r\n")
    if line.startswith(_HTTPONLY_PREFIX):
        line = line[len(_HTTPONLY_PREFIX) :]
    elif line.startswith("#") or not line.strip():
        return None
    fields = line.split("\t")
    if len(fields) != _COOKIE_ROW_FIELDS:
        return None
    return fields[0].strip().lower(), fields[5].strip()


def _inspect_cookiefile(raw: bytes) -> tuple[str | None, list[tuple[str, str]]]:
    """Return ``(problem, rows)``; ``problem`` is why yt-dlp cannot use the file.

    ``rows`` holds ``(domain, cookie name)`` pairs for the caller's own sanity
    checks. No cookie value ever leaves this function.
    """
    text = raw[:1_000_000].decode("utf-8", errors="ignore")
    lines = text.splitlines()
    if not lines or _NETSCAPE_HEADER not in lines[0]:
        return (
            "it does not start with the '# Netscape HTTP Cookie File' header line "
            "(yt-dlp only reads Netscape-format cookies.txt files)",
            [],
        )
    rows = [row for row in (_parse_cookie_row(line) for line in lines[1:]) if row is not None]
    if not rows:
        return "it contains no cookie rows", []
    return None, rows


def _has_instagram_session(rows: list[tuple[str, str]]) -> bool:
    """True when the file carries the cookie Instagram's extractor looks for.

    ``instagram.py`` switches to the logged-in request path only when a
    ``sessionid`` cookie exists for instagram.com.
    """
    return any("instagram.com" in domain and name == "sessionid" for domain, name in rows)


def _decode_cookie_blob(
    b64_value: str, env_name: str, target: Path, label: str, *, instagram: bool = False
) -> Path | None:
    """Decode a base64 cookies.txt, validate it, write it 0600, return its path.

    Cookie content is never logged — only the byte count of what was written.
    The env var name is used in messages so the owner knows which secret to fix.

    An unusable value is ignored instead of written: handing yt-dlp a file it
    cannot parse makes *every* download fail (``CookieLoadError``), whereas
    ignoring it keeps downloads running with anonymous access, which is exactly
    the behaviour before cookies were configured.
    """
    try:
        raw = base64.b64decode(_clean_b64(b64_value), validate=False)
    except (binascii.Error, ValueError) as exc:
        # Never log the value itself — it grants account access.
        logger.error("%s is not valid base64: %s", env_name, exc)
        return None

    problem, rows = _inspect_cookiefile(raw)
    if problem is not None:
        logger.error(
            "Ignoring %s: %s. It is not written, so %s downloads keep working "
            "without it (passing it on would make every download fail)",
            env_name,
            problem,
            label,
        )
        return None

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        target.chmod(0o600)
    except OSError as exc:
        logger.error("Could not write cookies file %s: %s", target, exc)
        return None

    if instagram:
        if _has_instagram_session(rows):
            logger.info(
                "Instagram cookies include an instagram.com sessionid cookie; "
                "the logged-in extraction path will be used",
            )
        else:
            logger.warning(
                "Instagram cookies contain no instagram.com 'sessionid' cookie, so "
                "Instagram will stay on anonymous access (the path that answers "
                "HTTP 429). Export the cookies while logged in, not from a "
                "logged-out or private window",
            )
    logger.info("%s cookies written to %s (%d bytes)", label, target, len(raw))
    return target


def _resolve_cookie_file() -> Path | None:
    """Decode YOUTUBE_COOKIES_B64 into a cookies file once. Returns its path."""
    global _cookie_file, _cookie_resolved
    with _COOKIE_LOCK:
        if _cookie_resolved:
            return _cookie_file
        _cookie_resolved = True

        if not YOUTUBE_COOKIES_B64:
            logger.warning("YOUTUBE_COOKIES_B64 not set, YouTube may block downloads")
            _cookie_file = None
            return None

        _cookie_file = _decode_cookie_blob(
            YOUTUBE_COOKIES_B64, "YOUTUBE_COOKIES_B64", Path(YOUTUBE_COOKIES_FILE), "YouTube"
        )
        return _cookie_file


def _resolve_instagram_cookie_file() -> Path | None:
    """Decode INSTAGRAM_COOKIES_B64 once (optional). Returns its path or None.

    Not configured is a normal state: Instagram downloads keep using anonymous
    access and every other site is unaffected.
    """
    global _instagram_cookie_file, _instagram_cookie_resolved
    with _COOKIE_LOCK:
        if _instagram_cookie_resolved:
            return _instagram_cookie_file
        _instagram_cookie_resolved = True

        if not INSTAGRAM_COOKIES_B64:
            logger.debug(
                "INSTAGRAM_COOKIES_B64 not set; Instagram downloads use anonymous access"
            )
            _instagram_cookie_file = None
            return None

        _instagram_cookie_file = _decode_cookie_blob(
            INSTAGRAM_COOKIES_B64,
            "INSTAGRAM_COOKIES_B64",
            Path(INSTAGRAM_COOKIES_FILE),
            "Instagram",
            instagram=True,
        )
        return _instagram_cookie_file


def prepare_youtube_cookies() -> bool:
    """Resolve the cookies file at startup. Returns True if cookies are in use."""
    return _resolve_cookie_file() is not None


def reset_cookie_cache() -> None:
    """Forget the cached cookies files (used by tests)."""
    global _cookie_file, _cookie_resolved
    global _instagram_cookie_file, _instagram_cookie_resolved
    global _instagram_cookies_rejected_reported
    with _COOKIE_LOCK:
        _instagram_cookies_rejected_reported = False
        _cookie_file = None
        _cookie_resolved = False
        _instagram_cookie_file = None
        _instagram_cookie_resolved = False


def _client_attempts() -> list[tuple[str, ...] | None]:
    """Ordered client ladders to try; ``None`` means yt-dlp's defaults.

    With cookies available the defaults are tried first (cookies are the
    proper fix and keep every format available). Without cookies the explicit
    client fallbacks are used from the start, since the default web client is
    what usually trips the bot check on cloud IPs.
    """
    supported = _supported_clients()
    ladders: list[tuple[str, ...]] = []
    for ladder in _CLIENT_LADDERS:
        filtered = tuple(client for client in ladder if client in supported)
        if filtered and filtered not in ladders:
            ladders.append(filtered)

    if _resolve_cookie_file() is not None:
        return [None, *ladders]
    return list(ladders) or [None]


def _apply_cookies(opts: dict[str, Any], url: str | None = None) -> None:
    """Attach a cookies file to the yt-dlp options.

    Instagram URLs use INSTAGRAM_COOKIES_* when it is configured. When it is
    not, the behaviour is exactly what it was before this option existed (the
    YouTube cookies file is passed for every site, and yt-dlp only sends the
    cookies that match the target domain). ``url=None`` keeps the old call
    shape for callers that only need the YouTube cookies.
    """
    cookie_file = _resolve_cookie_file()
    if url is not None and is_instagram_url(url):
        cookie_file = _resolve_instagram_cookie_file() or cookie_file
    if cookie_file is not None:
        opts["cookiefile"] = str(cookie_file)


def _apply_clients(opts: dict[str, Any], clients: tuple[str, ...] | None) -> None:
    if clients:
        opts["extractor_args"] = {"youtube": {"player_client": list(clients)}}


# yt-dlp's own wording when it aborts because of ``max_filesize``.
_MAX_FILESIZE_MARKER = "max-filesize"

# Instagram's extractor says this (and clears the cookie) when the configured
# session is no longer valid: https://github.com/yt-dlp/yt-dlp (extractor/instagram.py).
_INSTAGRAM_COOKIES_REJECTED_MARKER = "instagram account cookies are no longer valid"

# One report per process: a stale secret must not flood the log on every attempt.
_instagram_cookies_rejected_reported = False


class YdlMessages:
    """Captures yt-dlp output so nothing is lost and nothing is spammed.

    Passing a ``logger`` to yt-dlp routes *all* of its output here instead of
    stdout, which matters for two reasons:

    * diagnostics — with the default settings (``quiet``/``no_warnings``) the
      real reason for a failure (rate limit, login wall, retry notice) never
      reaches our logs. Warnings and errors are now logged, and progress lines
      are kept in a small ring buffer;
    * ``max_filesize`` — the downloader aborts with a plain ``to_screen``
      message and a ``False`` return value (no exception), so the message is the
      only way to tell "too large" apart from "download failed".
    """

    def __init__(self, keep: int = 50) -> None:
        self.lines: deque[str] = deque(maxlen=keep)

    def debug(self, message: str) -> None:
        self.lines.append(str(message))

    def info(self, message: str) -> None:
        self.lines.append(str(message))

    def warning(self, message: str) -> None:
        self.lines.append(str(message))
        logger.warning("yt-dlp: %s", message)

    def error(self, message: str) -> None:
        self.lines.append(str(message))
        logger.error("yt-dlp: %s", message)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    def exceeded_max_filesize(self) -> bool:
        return _MAX_FILESIZE_MARKER in self.text.lower()

    def instagram_cookies_rejected(self) -> bool:
        """True if Instagram refused the configured session cookie."""
        return _INSTAGRAM_COOKIES_REJECTED_MARKER in self.text.lower()


def _report_rejected_instagram_cookies(messages: YdlMessages) -> None:
    """Tell the owner, once per process, that the Instagram cookie is stale.

    Without this the only sign of an expired secret is the generic outcome
    (anonymous access -> HTTP 429), which looks identical to not having
    configured cookies at all.
    """
    global _instagram_cookies_rejected_reported
    if _instagram_cookies_rejected_reported or not messages.instagram_cookies_rejected():
        return
    _instagram_cookies_rejected_reported = True
    logger.error(
        "Instagram rejected the configured cookies ('The provided Instagram account "
        "cookies are no longer valid'): INSTAGRAM_COOKIES_B64 is expired, was logged "
        "out, or came from a different account/session. Export a fresh cookies.txt "
        "from a browser that is still logged in, or unset the variable. Instagram "
        "downloads currently use anonymous access, which is rate-limited.",
    )


def _base_opts(output_dir: Path, messages: YdlMessages | None = None) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "outtmpl": str(output_dir / "%(title).80s [%(id)s].%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 3,
    }
    if messages is not None:
        opts["logger"] = messages
    return opts


def _apply_format(opts: dict[str, Any], quality: str, media_type: str) -> None:
    if media_type == "audio":
        # quality like 128/192/320/flac
        opts["format"] = "bestaudio/best"
        if quality == "flac":
            opts["postprocessors"] = [
                {"key": "FFmpegExtractAudio", "preferredcodec": "flac"}
            ]
        else:
            opts["postprocessors"] = [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": quality,
                }
            ]
    else:
        height = quality.replace("p", "")
        opts["format"] = f"bestvideo[height<={height}]+bestaudio/best[height<={height}]/best"


def _first_entry(info: dict[str, Any]) -> dict[str, Any]:
    # Playlist handling: take first entry.
    if "entries" in info and info["entries"]:
        return info["entries"][0]
    return info


def _build_opts(
    output_dir: Path,
    quality: str,
    media_type: str,
    clients: tuple[str, ...] | None,
    url: str | None = None,
    max_filesize_bytes: int | None = None,
    messages: YdlMessages | None = None,
) -> dict[str, Any]:
    opts = _base_opts(output_dir, messages)
    _apply_cookies(opts, url)
    _apply_clients(opts, clients)
    _apply_format(opts, quality, media_type)
    if max_filesize_bytes is not None:
        # Let yt-dlp abort oversized media before it is written to disk. Used
        # for sites where the pre-download probe is skipped (Instagram), so the
        # size limit still stops a download early without an extra request.
        opts["max_filesize"] = int(max_filesize_bytes)
    return opts


def _extract_info(url: str) -> dict[str, Any]:
    # A single attempt: the download path below owns the retry ladder, and a
    # failed probe is non-fatal (it only skips the pre-download size estimate).
    attempts = _client_attempts()
    clients = attempts[0] if attempts else None
    opts = _base_opts(Path("."))
    opts.pop("outtmpl", None)
    _apply_cookies(opts, url)
    _apply_clients(opts, clients)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if info is None:
        raise RuntimeError("No info extracted")
    return _first_entry(info)


async def extract_info(url: str) -> dict[str, Any]:
    return await asyncio.to_thread(_extract_info, url)


def _download(
    url: str,
    quality: str,
    media_type: str,
    output_dir: Path,
    max_filesize_bytes: int | None = None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    attempts = _client_attempts()
    last_exc: BaseException | None = None
    messages = YdlMessages()

    for index, clients in enumerate(attempts):
        opts = _build_opts(
            output_dir,
            quality,
            media_type,
            clients,
            url=url,
            max_filesize_bytes=max_filesize_bytes,
            messages=messages,
        )
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if info is None:
                    raise RuntimeError("Download failed")
                info = _first_entry(info)
                filename = ydl.prepare_filename(info)
                path = Path(filename)
                # Adjust extension for audio postprocessing.
                if media_type == "audio":
                    path = path.with_suffix(".flac" if quality == "flac" else ".mp3")
                if not path.exists():
                    # Fallback: find newest file in output_dir.
                    files = sorted(
                        output_dir.iterdir(),
                        key=lambda p: p.stat().st_mtime,
                        reverse=True,
                    )
                    if files:
                        path = files[0]
                if not path.exists():
                    if messages.exceeded_max_filesize():
                        # yt-dlp aborted before writing (no exception), so the
                        # reason has to come from its output.
                        raise MediaTooLargeError(
                            "yt-dlp aborted: media is larger than max_filesize"
                        )
                    raise RuntimeError("Downloaded file not found")
                return path
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, MediaTooLargeError):
                raise
            # Surface an expired Instagram session, which otherwise looks
            # exactly like having no cookies at all.
            _report_rejected_instagram_cookies(messages)
            is_last = index == len(attempts) - 1
            if is_last or not _is_bot_check(exc):
                raise
            last_exc = exc
            next_clients = attempts[index + 1]
            logger.warning(
                "YouTube bot check hit with client(s) %s: %s — retrying with %s",
                clients or "default",
                exc,
                next_clients or "default",
            )

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Download failed")


async def download(
    url: str,
    quality: str,
    media_type: str,
    output_dir: Path,
    max_filesize_bytes: int | None = None,
) -> Path:
    """Download ``url``; ``max_filesize_bytes`` aborts oversized media early."""
    return await asyncio.to_thread(
        _download, url, quality, media_type, output_dir, max_filesize_bytes
    )
