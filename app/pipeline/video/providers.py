import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from ...config import cfg
from .errors import VideoError, map_ytdlp_error
from .runner import ToolTimeout, run_tool

META_TIMEOUT = 90
SUBS_TIMEOUT = 120
AUDIO_TIMEOUT = 3600  # network/stall guard for the download step, not a duration limit


@dataclass
class SubtitleTrack:
    language: str
    automatic: bool
    ext: str


@dataclass
class VideoMeta:
    title: str
    channel: str | None
    duration_sec: int | None
    language: str | None
    is_live: bool
    tracks: list[SubtitleTrack] = field(default_factory=list)


class VideoProvider(Protocol):
    platform: str

    async def metadata(self, url: str) -> VideoMeta: ...
    async def fetch_subtitles(self, url: str, track: SubtitleTrack, dest_dir: Path) -> Path: ...
    async def fetch_audio(self, url: str, dest_dir: Path) -> Path: ...


class YtdlpProvider:
    """VK and Rutube via yt-dlp; registry is open for future providers."""

    def __init__(self, platform: str, extractor: str) -> None:
        self.platform = platform
        self.extractor = extractor

    def _base(self) -> list[str]:
        return [
            cfg.ytdlp_bin,
            "--ignore-config",
            "--no-playlist",
            "--socket-timeout", "20",
            "--retries", "2",
            "--use-extractors", self.extractor,
            "--no-warnings",
        ]

    async def metadata(self, url: str) -> VideoMeta:
        argv = self._base() + ["-J", "--skip-download", url]
        try:
            r = await run_tool(argv, timeout=META_TIMEOUT)
        except ToolTimeout:
            raise VideoError("platform_unreachable", "metadata timeout")
        if r.returncode != 0:
            raise VideoError(map_ytdlp_error(r.stderr, r.returncode), r.stderr[-2000:])
        try:
            data = json.loads(r.stdout)
        except json.JSONDecodeError:
            raise VideoError("download_failed", "metadata not JSON")
        tracks = []
        for lang, fmts in (data.get("subtitles") or {}).items():
            ext = (fmts[0].get("ext") if fmts else None) or "vtt"
            tracks.append(SubtitleTrack(lang, False, ext))
        for lang, fmts in (data.get("automatic_captions") or {}).items():
            ext = (fmts[0].get("ext") if fmts else None) or "vtt"
            tracks.append(SubtitleTrack(lang, True, ext))
        return VideoMeta(
            title=data.get("title") or "",
            channel=data.get("channel") or data.get("uploader"),
            duration_sec=int(data["duration"]) if data.get("duration") else None,
            language=data.get("language"),
            is_live=bool(data.get("is_live")),
            tracks=tracks,
        )

    async def fetch_subtitles(self, url: str, track: SubtitleTrack, dest_dir: Path) -> Path:
        flag = "--write-auto-subs" if track.automatic else "--write-subs"
        argv = self._base() + [
            flag, "--sub-langs", track.language, "--sub-format", "vtt/srt",
            "--skip-download", "-o", str(dest_dir / "subs.%(ext)s"), url,
        ]
        try:
            r = await run_tool(argv, timeout=SUBS_TIMEOUT)
        except ToolTimeout:
            raise VideoError("platform_unreachable", "subs timeout")
        if r.returncode != 0:
            raise VideoError(map_ytdlp_error(r.stderr, r.returncode), r.stderr[-2000:])
        files = sorted(dest_dir.glob("subs.*"))
        if not files:
            raise VideoError("download_failed", "no subtitle file written")
        return files[0]

    async def fetch_audio(self, url: str, dest_dir: Path) -> Path:
        argv = self._base() + [
            "-f", "bestaudio",
            "-x", "--audio-format", "wav", "--audio-quality", "0",
            "--postprocessor-args", "ffmpeg:-ar 16000 -ac 1",
            "-o", str(dest_dir / "audio.%(ext)s"), url,
        ]
        try:
            r = await run_tool(argv, timeout=AUDIO_TIMEOUT)
        except ToolTimeout:
            raise VideoError("download_failed", "audio download timeout")
        if r.returncode != 0:
            raise VideoError(map_ytdlp_error(r.stderr, r.returncode), r.stderr[-2000:])
        wav = dest_dir / "audio.wav"
        if not wav.exists():
            raise VideoError("download_failed", "no audio file written")
        return wav


PROVIDERS: dict[str, VideoProvider] = {
    "vk": YtdlpProvider("vk", "vk"),
    "rutube": YtdlpProvider("rutube", "rutube"),
}
