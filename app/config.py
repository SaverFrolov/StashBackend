import os
from pathlib import Path


class Config:
    def __init__(self) -> None:
        # Login role is a member of the NOLOGIN groups stash_auth / stash_app;
        # each pool switches to its group via the startup `role` parameter.
        self.dsn = os.environ.get(
            "DATABASE_DSN", "postgresql://stash_login@localhost:5432/stash"
        )
        self.secret_key = os.environ.get("SECRET_KEY", "dev-insecure-secret")
        self.files_dir = Path(
            os.environ.get("FILES_DIR", str(Path.home() / "stash-files"))
        )
        self.public_base_url = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
        self.session_days = int(os.environ.get("SESSION_DAYS", "30"))
        self.worker_concurrency = int(os.environ.get("WORKER_CONCURRENCY", "2"))
        self.file_url_ttl = int(os.environ.get("FILE_URL_TTL", str(24 * 3600)))
        self.ollama_url = os.environ.get(
            "OLLAMA_URL", "http://localhost:11434"
        ).rstrip("/")
        self.embed_model = os.environ.get("EMBED_MODEL", "bge-m3:latest")
        self.transcribe_python = os.environ.get(
            "TRANSCRIBE_PYTHON",
            str(Path.home() / "stash-transcribe/.venv/bin/python"),
        )
        self.transcribe_script = os.environ.get(
            "TRANSCRIBE_SCRIPT", str(Path.home() / "stash-transcribe/transcribe.py")
        )
        self.cors_origins = os.environ.get("CORS_ORIGINS", "*").split(",")
        self.admin_email = os.environ.get("ADMIN_EMAIL", "").strip().lower()
        # Owner-funded fallback so users without their own key still get annotations.
        self.default_ai_key = os.environ.get("DEFAULT_AI_API_KEY", "").strip()
        # Video-by-link transcription (VK/Rutube). Off until the operator checklist passes.
        self.video_links_enabled = os.environ.get("VIDEO_LINKS_ENABLED", "false").lower() == "true"
        self.video_platforms = [
            p.strip() for p in os.environ.get("VIDEO_PLATFORMS", "vk,rutube").split(",") if p.strip()
        ]
        self.video_allow_subtitles = os.environ.get("VIDEO_ALLOW_SUBTITLES", "true").lower() == "true"
        self.video_allow_audio_download = os.environ.get("VIDEO_ALLOW_AUDIO_DOWNLOAD", "true").lower() == "true"
        self.video_tmp_dir = Path(os.environ.get("VIDEO_TMP_DIR", str(Path.home() / "stash-video-tmp")))
        self.video_concurrency = int(os.environ.get("VIDEO_CONCURRENCY", "1"))
        self.video_whisper_threads = int(os.environ.get("VIDEO_WHISPER_THREADS", "8"))
        self.video_nice = int(os.environ.get("VIDEO_NICE", "10"))
        self.video_min_free_disk_mb = int(os.environ.get("VIDEO_MIN_FREE_DISK_MB", "2048"))
        # 0 = unlimited (owner decision: no duration limits by default)
        self.video_max_duration_min = int(os.environ.get("VIDEO_MAX_DURATION_MIN", "0"))
        self.video_daily_minutes_per_user = int(os.environ.get("VIDEO_DAILY_MINUTES_PER_USER", "0"))
        self.ytdlp_bin = os.environ.get("YTDLP_BIN", "yt-dlp")
        self.ffmpeg_bin = os.environ.get("FFMPEG_BIN", "ffmpeg")
        # Defaults confirmed with the user on 2026-11-23 (airouter proxy).
        self.default_ai = {
            "provider": "openai",
            "base_url": "https://ru.airouter.host/v1",
            "model": "deepseek/deepseek-v4.1-flash",
        }


cfg = Config()
