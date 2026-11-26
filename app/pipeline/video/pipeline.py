"""Видео-ветка: субтитры → аудио → ASR. Прогресс, чанки, resume, коды ошибок.

Возвращает (text, lang, title) как extract_item, либо None — тогда ссылка
обрабатывается обычным HTML-путём без изменения поведения.
"""
import asyncio
import json
import logging
import shutil
from collections import defaultdict
from pathlib import Path

from ...config import cfg
from .errors import VideoError
from .providers import PROVIDERS
from .runner import run_tool
from .subtitles import coverage, dedupe_rolling, parse_subtitles, pick_track
from .urls import parse_video_url

logger = logging.getLogger("stash.pipeline.video")

MIN_COVERAGE = 0.7
CHUNK_SEC = 600          # ~10 минут на кусок ASR
OVERLAP_SEC = 5          # перехлёст, дедуп по таймкодам
ASR_MODEL = "faster-whisper-large-v3"

_video_sem = asyncio.Semaphore(max(1, cfg.video_concurrency))
_user_locks: dict = defaultdict(asyncio.Lock)


async def _stage(conn, item_id, stage, current=None, total=None):
    progress = None
    if current is not None and total:
        progress = {"current": current, "total": total,
                    "percent": round(100 * current / total)}
    await conn.execute(
        "UPDATE items SET extract_stage = $2, extract_progress = $3::jsonb, "
        "updated_at = now() WHERE id = $1",
        item_id, stage, json.dumps(progress) if progress else None,
    )


async def _error(conn, item_id, code):
    await conn.execute(
        "UPDATE items SET extract_error_code = $2, updated_at = now() WHERE id = $1",
        item_id, code,
    )


async def _save(conn, item_id, user_id, *, source, language, segments, text,
                model, duration_sec, status, chunk_no):
    await conn.execute(
        "INSERT INTO video_transcripts (item_id, user_id, source, language, segments, "
        "text, model, duration_sec, status, chunk_no) "
        "VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7,$8,$9,$10) "
        "ON CONFLICT (item_id, chunk_no) DO UPDATE SET "
        "source=EXCLUDED.source, language=EXCLUDED.language, segments=EXCLUDED.segments, "
        "text=EXCLUDED.text, model=EXCLUDED.model, duration_sec=EXCLUDED.duration_sec, "
        "status=EXCLUDED.status, updated_at=now()",
        item_id, user_id, source, language, json.dumps(segments), text,
        model, duration_sec, status, chunk_no,
    )


def _seg_json(segs):
    return [{"start": round(s.start, 3), "end": round(s.end, 3), "text": s.text} for s in segs]


def _join(segs):
    return " ".join(s.text.strip() for s in segs if s.text.strip()).strip()


async def _cut(wav: Path, tmp: Path, duration_sec):
    """Куски с перехлёстом. Без duration — один кусок целиком (ffprobe не нужен)."""
    starts = list(range(0, duration_sec, CHUNK_SEC)) if duration_sec else [0]
    out = []
    for i, start in enumerate(starts):
        dst = tmp / f"chunk_{i:04d}.wav"
        argv = [getattr(cfg, "ffmpeg_bin", "ffmpeg"), "-nostdin", "-loglevel", "error",
                "-ss", str(start), "-i", str(wav)]
        if i < len(starts) - 1:
            argv += ["-t", str(CHUNK_SEC + OVERLAP_SEC)]
        argv += ["-ar", "16000", "-ac", "1", str(dst)]
        r = await run_tool(argv, timeout=600, nice=cfg.video_nice)
        if r.returncode != 0 or not dst.exists():
            raise VideoError("transcription_failed", r.stderr[-500:])
        out.append((dst, float(start)))
    return out


async def _asr(path: Path, language):
    out = path.with_suffix(".json")
    argv = [str(cfg.transcribe_python), str(cfg.transcribe_script), str(path),
            "--json", "--output", str(out), "--threads", str(cfg.video_whisper_threads)]
    if language and language != "auto":
        argv += ["--language", language]
    r = await run_tool(argv, timeout=None, nice=cfg.video_nice)
    if r.returncode != 0:
        raise VideoError("transcription_failed", r.stderr[-500:])
    data = json.loads(out.read_text(encoding="utf-8"))
    return data.get("segments", []), data.get("language")


class _Seg:
    __slots__ = ("start", "end", "text")

    def __init__(self, start, end, text):
        self.start, self.end, self.text = start, end, text


async def _transcribe(conn, row, url, vurl, settings):
    item_id, user_id = row["id"], row["user_id"]
    provider = PROVIDERS[vurl.platform]
    tmp = Path(cfg.video_tmp_dir) / str(item_id)
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        free_mb = shutil.disk_usage(tmp).free // (1024 * 1024)
        if free_mb < cfg.video_min_free_disk_mb:
            raise VideoError("download_failed", f"low disk: {free_mb}MB")

        await _stage(conn, item_id, "metadata")
        meta = await provider.metadata(url)
        if meta.is_live:
            raise VideoError("live_stream", "live")
        await conn.execute(
            "UPDATE items SET video_meta = $2::jsonb WHERE id = $1",
            item_id,
            json.dumps({"platform": vurl.platform, "video_id": vurl.video_id,
                        "title": meta.title, "channel": meta.channel,
                        "duration_sec": meta.duration_sec, "source_url": url}),
        )
        if meta.title and not (row["title"] or "").strip():
            await conn.execute("UPDATE items SET title = $2 WHERE id = $1", item_id, meta.title[:300])

        pref = (settings["transcribe_language"] if settings else "auto") or "auto"
        lang_hint = None if pref == "auto" else (meta.language or pref)

        # 1) субтитры
        if cfg.video_allow_subtitles and meta.tracks:
            track = pick_track(meta.tracks, lang_hint)
            if track is not None:
                await _stage(conn, item_id, "captions")
                try:
                    path = await provider.fetch_subtitles(url, track, tmp)
                    segs = dedupe_rolling(parse_subtitles(path))
                except VideoError as e:
                    logger.info("captions failed for %s: %s", item_id, e.code)
                    segs = []
                if segs and coverage(segs, meta.duration_sec) >= MIN_COVERAGE:
                    text = _join(segs)
                    await _save(conn, item_id, user_id,
                                source="captions_auto" if track.automatic else "captions_manual",
                                language=track.language, segments=_seg_json(segs), text=text,
                                model=None, duration_sec=meta.duration_sec,
                                status="done", chunk_no=0)
                    await _stage(conn, item_id, "done", 1, 1)
                    return text, track.language, meta.title

        # 2) аудио + ASR
        if not cfg.video_allow_audio_download:
            raise VideoError("no_speech", "captions insufficient, audio disabled")
        await _stage(conn, item_id, "audio")
        wav = await provider.fetch_audio(url, tmp)
        pieces = await _cut(wav, tmp, meta.duration_sec)
        done = {r["chunk_no"] for r in await conn.fetch(
            "SELECT chunk_no FROM video_transcripts WHERE item_id = $1 AND status = 'done'",
            item_id)}

        await _stage(conn, item_id, "asr", 0, len(pieces))
        all_segs, lang = [], None
        for i, (piece, offset) in enumerate(pieces):
            if i in done:  # resume после перезапуска
                await _stage(conn, item_id, "asr", i + 1, len(pieces))
                continue
            raw, seg_lang = await _asr(piece, pref)
            lang = lang or seg_lang
            segs = [_Seg(offset + float(s.get("start", 0)),
                         offset + float(s.get("end", 0)),
                         (s.get("text") or "").strip()) for s in raw]
            segs = [s for s in segs if s.text]
            if all_segs:  # дедуп перехлёста по таймкоду
                tail = all_segs[-1].end
                segs = [s for s in segs if s.start >= tail - 0.01]
            all_segs.extend(segs)
            await _save(conn, item_id, user_id, source="asr", language=lang,
                        segments=_seg_json(segs), text=_join(segs), model=ASR_MODEL,
                        duration_sec=meta.duration_sec, status="done", chunk_no=i)
            await _stage(conn, item_id, "asr", i + 1, len(pieces))

        text = _join(all_segs)
        if not text:
            raise VideoError("no_speech", "empty transcript")
        await _stage(conn, item_id, "done", len(pieces), len(pieces))
        return text, lang, meta.title
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def video_branch(conn, row, url):
    """None → обычная HTML-обработка ссылки. Tuple → готовый транскрипт."""
    if not cfg.video_links_enabled:
        return None
    vurl = parse_video_url(url)
    if vurl is None:
        return None
    item_id = row["id"]
    if vurl.platform not in cfg.video_platforms or vurl.platform not in PROVIDERS:
        await _error(conn, item_id, "platform_disabled")
        return None
    settings = await conn.fetchrow(
        "SELECT transcribe_video_links, transcribe_language FROM ai_settings WHERE user_id = $1",
        row["user_id"])
    if settings is not None and not settings["transcribe_video_links"]:
        return None
    async with _user_locks[row["user_id"]]:
        async with _video_sem:
            try:
                return await _transcribe(conn, row, url, vurl, settings)
            except VideoError as e:
                logger.info("video %s failed: %s", item_id, e.code)
                await _error(conn, item_id, e.code)
                await _stage(conn, item_id, None)
                return None
            except Exception:
                logger.warning("video %s unexpected failure", item_id, exc_info=True)
                await _error(conn, item_id, "download_failed")
                return None
