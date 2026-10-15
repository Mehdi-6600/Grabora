"""yt-dlp wrapper (async) with YouTube cookie / bot-check handling."""

from __future__ import annotations

import asyncio
import base64
import binascii
import importlib
import logging
import threading
from pathlib import Path
from typing import Any

import yt_dlp

from config import YOUTUBE_COOKIES_B64, YOUTUBE_COOKIES_FILE

logger = logging.getLogger(__name__)

_COOKIE_LOCK = threading.Lock()
_cookie_file: Path | None = None
_cookie_resolved = False

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


def _is_bot_check(exc: BaseException) -> bool:
    """True if the failure is YouTube's bot/age verification wall."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        text = " ".join(str(arg) for arg in current.args).lower()
        if any(marker in text for marker in _BOT_CHECK_MARKERS):
            return True
        current = current.__cause__ or current.__context__
    return False


def _clean_b64(value: str) -> str:
    """Strip whitespace/newlines from a pasted base64 blob."""
    return "".join(value.split())


def _looks_like_cookiefile(raw: bytes) -> bool:
    head = raw[:4096].decode("utf-8", errors="ignore")
    return "Netscape HTTP Cookie File" in head or "\t" in head


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

        target = Path(YOUTUBE_COOKIES_FILE)
        try:
            raw = base64.b64decode(_clean_b64(YOUTUBE_COOKIES_B64), validate=False)
        except (binascii.Error, ValueError) as exc:
            # Never log the value itself — it grants account access.
            logger.error("YOUTUBE_COOKIES_B64 is not valid base64: %s", exc)
            _cookie_file = None
            return None

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            target.chmod(0o600)
        except OSError as exc:
            logger.error("Could not write cookies file %s: %s", target, exc)
            _cookie_file = None
            return None

        if not _looks_like_cookiefile(raw):
            logger.warning(
                "Decoded YOUTUBE_COOKIES_B64 does not look like a Netscape "
                "cookies.txt (expected the 'Netscape HTTP Cookie File' header); "
                "yt-dlp may reject it",
            )
        logger.info("YouTube cookies written to %s (%d bytes)", target, len(raw))
        _cookie_file = target
        return _cookie_file


def prepare_youtube_cookies() -> bool:
    """Resolve the cookies file at startup. Returns True if cookies are in use."""
    return _resolve_cookie_file() is not None


def reset_cookie_cache() -> None:
    """Forget the cached cookies file (used by tests)."""
    global _cookie_file, _cookie_resolved
    with _COOKIE_LOCK:
        _cookie_file = None
        _cookie_resolved = False


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


def _apply_cookies(opts: dict[str, Any]) -> None:
    cookie_file = _resolve_cookie_file()
    if cookie_file is not None:
        opts["cookiefile"] = str(cookie_file)


def _apply_clients(opts: dict[str, Any], clients: tuple[str, ...] | None) -> None:
    if clients:
        opts["extractor_args"] = {"youtube": {"player_client": list(clients)}}


def _base_opts(output_dir: Path) -> dict[str, Any]:
    return {
        "outtmpl": str(output_dir / "%(title).80s [%(id)s].%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 3,
    }


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
) -> dict[str, Any]:
    opts = _base_opts(output_dir)
    _apply_cookies(opts)
    _apply_clients(opts, clients)
    _apply_format(opts, quality, media_type)
    return opts


def _extract_info(url: str) -> dict[str, Any]:
    # A single attempt: the download path below owns the retry ladder, and a
    # failed probe is non-fatal (it only skips the pre-download size estimate).
    attempts = _client_attempts()
    clients = attempts[0] if attempts else None
    opts = _base_opts(Path("."))
    opts.pop("outtmpl", None)
    _apply_cookies(opts)
    _apply_clients(opts, clients)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if info is None:
        raise RuntimeError("No info extracted")
    return _first_entry(info)


async def extract_info(url: str) -> dict[str, Any]:
    return await asyncio.to_thread(_extract_info, url)


def _download(url: str, quality: str, media_type: str, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    attempts = _client_attempts()
    last_exc: BaseException | None = None

    for index, clients in enumerate(attempts):
        opts = _build_opts(output_dir, quality, media_type, clients)
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
                    raise RuntimeError("Downloaded file not found")
                return path
        except Exception as exc:  # noqa: BLE001
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


async def download(url: str, quality: str, media_type: str, output_dir: Path) -> Path:
    return await asyncio.to_thread(_download, url, quality, media_type, output_dir)
