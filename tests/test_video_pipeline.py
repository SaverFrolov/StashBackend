import asyncio
from pathlib import Path
from uuid import uuid4

from app.pipeline.video import pipeline as P
from app.pipeline.video.errors import VideoError
from app.pipeline.video.providers import SubtitleTrack, VideoMeta


class Conn:
    def __init__(self): self.sql = []; self.rows = []
    async def execute(self, q, *a): self.sql.append((q, a))
    async def fetch(self, q, *a): return self.rows
    async def fetchrow(self, q, *a):
        return {"transcribe_video_links": True, "transcribe_language": "auto"}


def _ts(t):
    h, r = divmod(t, 3600)
    m, s = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{s:02d}.000"


class Prov:
    platform = "vk"

    def __init__(self, tracks, boom=None, duration=120):
        self.tracks, self.boom, self.duration = tracks, boom, duration

    async def metadata(self, url):
        if self.boom:
            raise VideoError(self.boom, "x")
        return VideoMeta("Заголовок", "Канал", self.duration, "ru", False, self.tracks)

    async def fetch_subtitles(self, url, track, dest):
        p = Path(dest) / "subs.vtt"
        # 24 куска по 5 с подряд = 120 с речи на 120 с видео → покрытие 1.0
        body = "WEBVTT\n\n" + "".join(
            f"{_ts(i * 5)} --> {_ts(i * 5 + 5)}\nтекст {i}\n\n" for i in range(24))
        p.write_text(body, encoding="utf-8")
        return p

    async def fetch_audio(self, url, dest):
        p = Path(dest) / "audio.wav"; p.write_bytes(b"RIFF"); return p


def _row():
    return {"id": uuid4(), "user_id": uuid4(), "title": "",
            "source": {"url": "https://vk.com/video-1_2"}}


def _run(monkeypatch, prov, tmp):
    monkeypatch.setattr(P, "PROVIDERS", {"vk": prov})
    monkeypatch.setattr(P.cfg, "video_links_enabled", True)
    monkeypatch.setattr(P.cfg, "video_platforms", ["vk"])
    monkeypatch.setattr(P.cfg, "video_tmp_dir", Path(tmp))
    conn = Conn()
    return conn, asyncio.run(P.video_branch(conn, _row(), "https://vk.com/video-1_2"))


def test_captions_win(tmp_path, monkeypatch):
    conn, res = _run(monkeypatch, Prov([SubtitleTrack("ru", False, "vtt")]), tmp_path)
    assert res is not None, [a for q, a in conn.sql if "extract_error_code" in q]
    assert "текст" in res[0]
    assert any("captions_manual" in str(a) for _, a in conn.sql)


def test_thin_captions_fall_through(tmp_path, monkeypatch):
    # 120 с видео, но субтитры покрывают только первые 5 с → уходим в аудио-ветку
    conn, res = _run(monkeypatch, Prov([SubtitleTrack("ru", False, "vtt")], duration=3600), tmp_path)
    assert res is None
    assert any("transcription_failed" in str(a) or "download_failed" in str(a)
               for _, a in conn.sql)


def test_permanent_error_falls_back(tmp_path, monkeypatch):
    conn, res = _run(monkeypatch, Prov([], boom="private_or_login_required"), tmp_path)
    assert res is None
    assert any("private_or_login_required" in str(a) for _, a in conn.sql)


def test_flag_off_is_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(P.cfg, "video_links_enabled", False)
    conn = Conn()
    assert asyncio.run(P.video_branch(conn, _row(), "https://vk.com/video-1_2")) is None
    assert conn.sql == []


def test_jsonb_params_are_native(tmp_path, monkeypatch):
    """asyncpg сам кодирует jsonb: повторный json.dumps даёт строку вместо массива."""
    conn, res = _run(monkeypatch, Prov([SubtitleTrack("ru", False, "vtt")]), tmp_path)
    assert res is not None
    for q, args in conn.sql:
        if "video_transcripts" in q and "INSERT" in q:
            assert isinstance(args[4], list), "segments должен быть списком, не строкой"
        if "extract_progress" in q and args[2] is not None:
            assert isinstance(args[2], dict), "progress должен быть dict, не строкой"
