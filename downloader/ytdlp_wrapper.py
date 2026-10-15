"""yt-dlp wrapper (async)."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import yt_dlp

logger = logging.getLogger(__name__)


def _base_opts(output_dir: Path) -> dict[str, Any]:
    return {
        "outtmpl": str(output_dir / "%(title).80s [%(id)s].%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 3,
    }


def _extract_info(url: str) -> dict[str, Any]:
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if info is None:
        raise RuntimeError("No info extracted")
    # Playlist handling: take first entry.
    if "entries" in info and info["entries"]:
        info = info["entries"][0]
    return info


async def extract_info(url: str) -> dict[str, Any]:
    return await asyncio.to_thread(_extract_info, url)


def _download(url: str, quality: str, media_type: str, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    opts = _base_opts(output_dir)

    if media_type == "audio":
        # quality like 128/192/320/flac
        if quality == "flac":
            opts["format"] = "bestaudio/best"
            opts["postprocessors"] = [
                {"key": "FFmpegExtractAudio", "preferredcodec": "flac"}
            ]
        else:
            opts["format"] = "bestaudio/best"
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

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if info is None:
            raise RuntimeError("Download failed")
        if "entries" in info and info["entries"]:
            info = info["entries"][0]
        filename = ydl.prepare_filename(info)
        # Adjust extension for audio postprocessing.
        path = Path(filename)
        if media_type == "audio":
            if quality == "flac":
                path = path.with_suffix(".flac")
            else:
                path = path.with_suffix(".mp3")
        if not path.exists():
            # Fallback: find newest file in output_dir.
            files = sorted(output_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
            if files:
                path = files[0]
        if not path.exists():
            raise RuntimeError("Downloaded file not found")
        return path


async def download(url: str, quality: str, media_type: str, output_dir: Path) -> Path:
    return await asyncio.to_thread(_download, url, quality, media_type, output_dir)
