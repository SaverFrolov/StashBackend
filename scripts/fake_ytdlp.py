#!/usr/bin/env python3
"""Подменный yt-dlp для тестов (YTDLP_BIN). Сценарий задаётся query-параметром
?scenario=... в URL; по умолчанию success_subs. Реальные платформы не трогает."""
import json
import sys
import wave
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SCENARIOS = {
    "private": "ERROR: [vk] Private video. You must log in to view it.",
    "age_restricted": "ERROR: [rutube] Sign in to confirm your age. This video is age-restricted.",
    "region_blocked": "ERROR: [vk] The uploader has not made this video available in your country.",
    "live": "ERROR: [vk] This is a live stream and cannot be downloaded.",
    "platform_blocked": "ERROR: [rutube] Sign in to confirm you're not a bot.",
    "platform_unreachable": "ERROR: unable to download: <urlopen error [Errno -2] Name or service not known>",
    "not_found": "ERROR: [vk] Video unavailable. This video does not exist. (404)",
    "temp_failure": "ERROR: unable to download video data: HTTP Error 503: Service Unavailable",
}


def main() -> int:
    args = sys.argv[1:]
    url = next((a for a in args if a.startswith("http")), "")
    scenario = (parse_qs(urlparse(url).query).get("scenario") or ["success_subs"])[0]

    if scenario == "hang":
        import time
        time.sleep(600)
        return 0

    if scenario in SCENARIOS:
        print(SCENARIOS[scenario], file=sys.stderr)
        return 1

    duration = 36000 if scenario == "long_10h" else 93
    meta = {
        "title": "Тестовая лекция",
        "channel": "Тестовый канал",
        "duration": duration,
        "language": "ru",
        "is_live": False,
        "subtitles": {} if scenario in ("success_audio", "no_speech") else {"ru": [{"ext": "vtt"}]},
        "automatic_captions": {} if scenario != "auto_subs" else {"ru": [{"ext": "vtt"}]},
    }
    if "-J" in args:
        print(json.dumps(meta, ensure_ascii=False))
        return 0

    out_tpl = next((args[i + 1] for i, a in enumerate(args) if a == "-o"), "out.%(ext)s")
    if "--write-subs" in args or "--write-auto-subs" in args:
        p = Path(out_tpl.replace("%(ext)s", "vtt"))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            "WEBVTT\n\n00:00:00.000 --> 00:00:30.000\nПервая строка субтитров\n\n"
            "00:00:30.000 --> 00:01:00.000\nВторая строка субтитров\n\n"
            "00:01:00.000 --> 00:01:33.000\nТретья строка субтитров\n",
            encoding="utf-8",
        )
        return 0

    if "-x" in args:
        p = Path(out_tpl.replace("%(ext)s", "wav"))
        p.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(p), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b"\x00\x00" * 16000)  # 1 с тишины
        return 0

    print("fake_ytdlp: unsupported invocation", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
