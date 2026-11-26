CODES = (
    "unsupported_url",
    "private_or_login_required",
    "age_restricted",
    "region_blocked",
    "live_stream",
    "not_found",
    "no_speech",
    "platform_blocked",
    "platform_unreachable",
    "download_failed",
    "transcription_failed",
    "too_long",
    "platform_disabled",
)

# Messages are user-facing: no URLs, tokens or paths.
MESSAGES = {
    "unsupported_url": "Не похоже на ссылку на видео",
    "private_or_login_required": "Видео приватное или требует входа на платформе",
    "age_restricted": "Видео с возрастным ограничением",
    "region_blocked": "Видео недоступно в регионе сервера",
    "live_stream": "Прямые эфиры не поддерживаются",
    "not_found": "Видео не найдено",
    "no_speech": "Речь не распознана",
    "platform_blocked": "Платформа требует подтверждения (защита от ботов)",
    "platform_unreachable": "Платформа недоступна с сервера",
    "download_failed": "Не удалось скачать аудио",
    "transcription_failed": "Ошибка распознавания",
    "too_long": "Видео длиннее лимита оператора",
    "platform_disabled": "Для этой платформы транскрибация пока недоступна",
}


class VideoError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail  # internal only (logs); never returned to clients
        super().__init__(MESSAGES.get(code, code))


def map_ytdlp_error(stderr: str, returncode: int) -> str:
    s = stderr.lower()
    if "confirm you're not a bot" in s or "confirm you’re not a bot" in s or "captcha" in s:
        return "platform_blocked"
    if "sign in to confirm your age" in s or "age-restricted" in s or "age restricted" in s:
        return "age_restricted"
    if "private" in s or "log in" in s or "login" in s or "members only" in s:
        return "private_or_login_required"
    if "in your country" in s or "region" in s:
        return "region_blocked"
    if "live stream" in s or "is live" in s or "live event" in s:
        return "live_stream"
    if "name or service not known" in s or "temporary failure in name resolution" in s \
            or "network is unreachable" in s or "connection refused" in s \
            or "timed out" in s or "timeout" in s:
        return "platform_unreachable"
    if "video unavailable" in s or "not found" in s or "404" in s or "does not exist" in s:
        return "not_found"
    if returncode != 0:
        return "download_failed"
    return "not_found"
