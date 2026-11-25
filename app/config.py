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
        # Defaults confirmed with the user on 2026-11-23 (airouter proxy).
        self.default_ai = {
            "provider": "openai",
            "base_url": "https://ru.airouter.host/v1",
            "model": "deepseek/deepseek-v4.1-flash",
        }


cfg = Config()
