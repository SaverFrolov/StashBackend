import asyncio
import os
import sys
from pathlib import Path

import pytest

from app.config import cfg
from app.pipeline.video import providers, subtitles
from app.pipeline.video.errors import VideoError, map_ytdlp_error
from app.pipeline.video.runner import run_tool
from app.pipeline.video.urls import parse_video_url

FAKE = str(Path(__file__).parent.parent / "scripts" / "fake_ytdlp.py")


@pytest.mark.parametrize(
    "url,platform,vid",
    [
        ("https://vk.com/video-123_456", "vk", "-123_456"),
        ("https://m.vkvideo.ru/video-123_456", "vk", "-123_456"),
        ("https://vkvideo.ru/clip-1_2", "vk", "-1_2"),
        ("https://rutube.ru/video/e26775f62715053e39161c7d7d619961/", "rutube", "e26775f62715053e39161c7d7d619961"),
        ("https://rutube.ru/play/embed/e26775f62715053e39161c7d7d619961", "rutube", "e26775f62715053e39161c7d7d619961"),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "youtube", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ?t=42", "youtube", "dQw4w9WgXcQ"),
        ("https://m.youtube.com/shorts/dQw4w9WgXcQ", "youtube", "dQw4w9WgXcQ"),
    ],
)
def test_parse_valid(url, platform, vid):
    r = parse_video_url(url)
    assert r is not None and r.platform == platform and r.video_id == vid


def test_parse_youtube_start():
    assert parse_video_url("https://youtu.be/dQw4w9WgXcQ?t=42").start_sec == 42


@pytest.mark.parametrize(
    "url",
    [
        "https://youtube.com.evil.com/watch?v=dQw4w9WgXcQ",
        "https://user:pass@vk.com/video-1_2",
        "https://127.0.0.1/video-1_2",
        "https://localhost/video-1_2",
        "ftp://vk.com/video-1_2",
        "-https://vk.com/video-1_2",
        "https://vk.com/playlist-1_2",
        "https://vk.com/@channel",
        "https://rutube.ru/channel/123/",
        "https://example.com/video-1_2",
        "https://vk.com/video-1_2extra",
        "",
    ],
)
def test_parse_rejects(url):
    assert parse_video_url(url) is None


@pytest.mark.parametrize(
    "stderr,code",
    [
        ("Private video. You must log in", "private_or_login_required"),
        ("Sign in to confirm your age", "age_restricted"),
        ("not available in your country", "region_blocked"),
        ("This is a live stream", "live_stream"),
        ("Sign in to confirm you're not a bot", "platform_blocked"),
        ("Name or service not known", "platform_unreachable"),
        ("Connection timed out", "platform_unreachable"),
        ("Video unavailable (404)", "not_found"),
    ],
)
def test_error_mapping(stderr, code):
    assert map_ytdlp_error(stderr, 1) == code


VTT = """WEBVTT

00:00:00.000 --> 00:00:02.000
привет мир

00:00:02.000 --> 00:00:04.000
привет мир

00:00:04.000 --> 00:00:06.000
пока мир
"""

SRT = """1
00:00:00,000 --> 00:00:02,000
привет мир

2
00:00:02,000 --> 00:00:04,000
пока мир
"""


def test_parse_vtt_and_dedupe():
    segs = subtitles.parse_vtt(VTT)
    assert len(segs) == 3
    deduped = subtitles.dedupe_rolling(segs)
    assert len(deduped) == 2
    assert deduped[0].end == 4.0
    assert subtitles.coverage(deduped, 6) == 1.0
    assert subtitles.coverage(deduped, 60) == pytest.approx(6 / 60)


def test_parse_srt():
    segs = subtitles.parse_srt(SRT)
    assert [s.text for s in segs] == ["привет мир", "пока мир"]


def test_pick_track_manual_first_and_language():
    T = providers.SubtitleTrack
    tracks = [T("en", True, "vtt"), T("ru", True, "vtt"), T("ru", False, "vtt")]
    assert subtitles.pick_track(tracks, "ru").automatic is False
    assert subtitles.pick_track(tracks, None) is tracks[2]  # manual preferred even without language
    assert subtitles.pick_track([], "ru") is None


@pytest.fixture
def fake_bin(monkeypatch):
    monkeypatch.setattr(cfg, "ytdlp_bin", FAKE)
    return FAKE


def _run(coro):
    return asyncio.run(coro)


def test_provider_metadata_success(fake_bin):
    p = providers.YtdlpProvider("vk", "vk")
    meta = _run(p.metadata("https://vk.com/video-1_2"))
    assert meta.title == "Тестовая лекция"
    assert meta.duration_sec == 93
    assert any(t.language == "ru" and not t.automatic for t in meta.tracks)


def test_provider_long_video_not_rejected(fake_bin):
    p = providers.YtdlpProvider("rutube", "rutube")
    meta = _run(p.metadata("https://rutube.ru/video/" + "a" * 32 + "?scenario=long_10h"))
    assert meta.duration_sec == 36000  # no rejection, no trimming


@pytest.mark.parametrize(
    "scenario,code",
    [
        ("private", "private_or_login_required"),
        ("age_restricted", "age_restricted"),
        ("region_blocked", "region_blocked"),
        ("live", "live_stream"),
        ("platform_blocked", "platform_blocked"),
        ("platform_unreachable", "platform_unreachable"),
        ("not_found", "not_found"),
    ],
)
def test_provider_error_scenarios(fake_bin, scenario, code):
    p = providers.YtdlpProvider("vk", "vk")
    with pytest.raises(VideoError) as ei:
        _run(p.metadata(f"https://vk.com/video-1_2?scenario={scenario}"))
    assert ei.value.code == code


def test_provider_hang_times_out(fake_bin, monkeypatch):
    monkeypatch.setattr(providers, "META_TIMEOUT", 1)
    p = providers.YtdlpProvider("vk", "vk")
    with pytest.raises(VideoError) as ei:
        _run(p.metadata("https://vk.com/video-1_2?scenario=hang"))
    assert ei.value.code == "platform_unreachable"


def test_provider_subtitles_and_audio(fake_bin, tmp_path):
    p = providers.YtdlpProvider("vk", "vk")
    track = providers.SubtitleTrack("ru", False, "vtt")
    subs = _run(p.fetch_subtitles("https://vk.com/video-1_2", track, tmp_path))
    segs = subtitles.parse_subtitles(subs)
    assert len(segs) == 3
    audio = _run(p.fetch_audio("https://vk.com/video-1_2?scenario=success_audio", tmp_path))
    assert audio.exists() and audio.stat().st_size > 1000


def test_runner_env_sanitized(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "super-secret")
    r = _run(run_tool(
        [sys.executable, "-c", "import os; print('SECRET_KEY' in os.environ)"],
        timeout=10,
    ))
    assert r.stdout.strip() == "False"


def test_runner_nice_applied():
    r = _run(run_tool(
        [sys.executable, "-c", "import os; print(os.nice(0))"],
        timeout=10, nice=5,
    ))
    assert int(r.stdout.strip()) >= 5
