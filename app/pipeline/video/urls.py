import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

VIDEO_HOSTS = {
    "vk.com": "vk",
    "www.vk.com": "vk",
    "m.vk.com": "vk",
    "vkvideo.ru": "vk",
    "www.vkvideo.ru": "vk",
    "m.vkvideo.ru": "vk",
    "rutube.ru": "rutube",
    "www.rutube.ru": "rutube",
    "youtube.com": "youtube",
    "www.youtube.com": "youtube",
    "m.youtube.com": "youtube",
    "music.youtube.com": "youtube",
    "youtu.be": "youtube",
    "www.youtu.be": "youtube",
}

VK_VIDEO_RE = re.compile(r"^/video(-?\d+_\d+)$")
VK_CLIP_RE = re.compile(r"^/clip(-?\d+_\d+)$")
RUTUBE_VIDEO_RE = re.compile(r"^/video/([0-9a-f]{32})/?$")
RUTUBE_EMBED_RE = re.compile(r"^/(?:play/embed|play|embed)/([0-9a-f]{32})/?$")
YT_ID_RE = re.compile(r"^[\w-]{11}$")


@dataclass(frozen=True)
class VideoUrl:
    platform: str
    video_id: str
    canonical_url: str
    start_sec: int | None = None


def _reject_host(host: str) -> bool:
    if not host or host.startswith("-"):
        return True
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        pass
    if host in ("localhost",) or host.endswith(".localhost"):
        return True
    return False


def parse_video_url(url: str) -> VideoUrl | None:
    """Structural parse; returns None for anything that is not a known video URL.

    None means: treat as a regular link (existing HTML path), never an error.
    """
    if not url or url.startswith("-"):
        return None
    try:
        p = urlparse(url)
    except ValueError:
        return None
    if p.scheme not in ("http", "https") or p.username or p.password:
        return None
    host = (p.hostname or "").lower()
    if _reject_host(host):
        return None
    platform = VIDEO_HOSTS.get(host)
    if platform is None:
        return None
    path = p.path or "/"
    query = parse_qs(p.query)
    start = None
    if platform == "vk":
        m = VK_VIDEO_RE.match(path) or VK_CLIP_RE.match(path)
        if not m:
            return None
        vid = m.group(1)
        return VideoUrl("vk", vid, f"https://vk.com/video{vid}")
    if platform == "rutube":
        m = RUTUBE_VIDEO_RE.match(path) or RUTUBE_EMBED_RE.match(path)
        if not m:
            return None
        vid = m.group(1)
        return VideoUrl("rutube", vid, f"https://rutube.ru/video/{vid}/")
    # youtube: recognized for platform_disabled handling; no provider implemented
    vid = None
    if host == "youtu.be":
        cand = path.lstrip("/").split("/")[0]
        if YT_ID_RE.match(cand):
            vid = cand
    elif path == "/watch":
        cand = (query.get("v") or [""])[0]
        if YT_ID_RE.match(cand):
            vid = cand
    else:
        m = re.match(r"^/(?:shorts|embed|live)/([\w-]{11})", path)
        if m:
            vid = m.group(1)
    if vid is None:
        return None
    t = (query.get("t") or query.get("start") or [""])[0]
    if t.isdigit():
        start = int(t)
    return VideoUrl("youtube", vid, f"https://www.youtube.com/watch?v={vid}", start)
