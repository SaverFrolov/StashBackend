import re
from dataclasses import dataclass

TS_RE = re.compile(r"(\d+):(\d{2}):(\d{2})[.,](\d{3})")
SRT_TS_RE = re.compile(r"(\d+):(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*(\d+):(\d{2}):(\d{2})[.,](\d{3})")


@dataclass
class Segment:
    start: float
    end: float
    text: str


def _ts(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def parse_vtt(content: str) -> list[Segment]:
    segments: list[Segment] = []
    lines = content.splitlines()
    i = 0
    while i < len(lines):
        m = TS_RE.search(lines[i])
        if m and "-->" in lines[i]:
            parts = lines[i].split("-->")
            a = TS_RE.search(parts[0])
            b = TS_RE.search(parts[1]) if len(parts) > 1 else None
            if a and b:
                start = _ts(*a.groups())
                end = _ts(*b.groups())
                texts = []
                i += 1
                while i < len(lines) and lines[i].strip():
                    texts.append(re.sub(r"<[^>]+>", "", lines[i]).strip())
                    i += 1
                text = " ".join(t for t in texts if t)
                if text:
                    segments.append(Segment(start, end, text))
                continue
        i += 1
    return segments


def parse_srt(content: str) -> list[Segment]:
    segments: list[Segment] = []
    blocks = re.split(r"\n\s*\n", content.strip())
    for block in blocks:
        lines = block.strip().splitlines()
        for j, line in enumerate(lines):
            m = SRT_TS_RE.search(line)
            if m:
                g = m.groups()
                start = _ts(*g[:4])
                end = _ts(*g[4:])
                text = " ".join(re.sub(r"<[^>]+>", "", t).strip() for t in lines[j + 1:])
                if text.strip():
                    segments.append(Segment(start, end, text.strip()))
                break
    return segments


def parse_subtitles(path) -> list[Segment]:
    content = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix.lower() == ".srt":
        return parse_srt(content)
    return parse_vtt(content)


def dedupe_rolling(segments: list[Segment]) -> list[Segment]:
    """Auto-captions repeat rolling lines; collapse consecutive identical texts."""
    out: list[Segment] = []
    for s in segments:
        if out and s.text == out[-1].text:
            out[-1].end = max(out[-1].end, s.end)
            continue
        if out and s.start < out[-1].end and s.text in out[-1].text:
            continue
        out.append(Segment(s.start, s.end, s.text))
    return out


def coverage(segments: list[Segment], duration_sec: float | None) -> float:
    if not duration_sec or duration_sec <= 0:
        return 1.0 if segments else 0.0
    covered = sum(max(0.0, s.end - s.start) for s in segments)
    return min(1.0, covered / duration_sec)


def pick_track(tracks, language: str | None):
    """Manual in material language first, then automatic; never translations."""
    if not tracks:
        return None
    lang = (language or "").lower()

    def base(t):
        return t.language.lower().split("-")[0]

    manual = [t for t in tracks if not t.automatic]
    auto = [t for t in tracks if t.automatic]
    for group in (manual, auto):
        if lang:
            exact = [t for t in group if base(t) == lang]
            if exact:
                return exact[0]
        if group:
            return group[0]
    return None
