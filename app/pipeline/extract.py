import asyncio
import ipaddress
import json
import socket
import zipfile
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree

import httpx
from bs4 import BeautifulSoup

from ..config import cfg

W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
UA = {"User-Agent": "Mozilla/5.0 (compatible; StashBot/1.0)"}
MAX_REDIRECTS = 5


def check_public_url(url: str) -> None:
    """SSRF guard: reject non-http(s) and any host resolving to non-global IPs."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Unsupported URL scheme")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(parsed.hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise ValueError("Cannot resolve host")
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if not addr.is_global:
            raise ValueError("Internal addresses are not allowed")


async def extract_link(url: str) -> tuple[str, str | None, str | None]:
    # shortcut: check-then-fetch leaves a DNS-rebinding window; pin the resolved IP via a custom transport if this surface grows
    async with httpx.AsyncClient(
        timeout=30, follow_redirects=False, headers=UA
    ) as client:
        for _ in range(MAX_REDIRECTS + 1):
            await asyncio.to_thread(check_public_url, url)
            r = await client.get(url)
            if r.is_redirect and r.headers.get("location"):
                url = str(r.url.join(r.headers["location"]))
                continue
            r.raise_for_status()
            break
        else:
            raise ValueError("Too many redirects")
    if "html" not in r.headers.get("content-type", ""):
        return r.text[:2_000_000], None, None
    soup = BeautifulSoup(r.text, "html.parser")
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    title = soup.title.get_text(strip=True) if soup.title else None
    text = " ".join(soup.get_text(" ", strip=True).split())
    return text[:2_000_000], None, title or None


def _pdf_text(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        root = ElementTree.fromstring(z.read("word/document.xml"))
    paras = []
    for p in root.iter(f"{W_NS}p"):
        text = "".join(t.text or "" for t in p.iter(f"{W_NS}t"))
        if text.strip():
            paras.append(text)
    return "\n".join(paras)


def _xlsx_text(path: Path) -> str:
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    lines = []
    for ws in wb.worksheets:
        lines.append(f"# {ws.title}")
        for row in ws.iter_rows(values_only=True):
            cells = [str(c) for c in row if c is not None]
            if cells:
                lines.append("\t".join(cells))
    wb.close()
    return "\n".join(lines)


async def _transcribe(path: Path) -> tuple[str, str | None]:
    out = path.with_name(path.name + ".transcript.json")
    proc = await asyncio.create_subprocess_exec(
        cfg.transcribe_python,
        cfg.transcribe_script,
        str(path),
        "--json",
        "--output",
        str(out),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"Transcription failed: {err.decode()[-500:]}")
    data = json.loads(out.read_text(encoding="utf-8"))
    text = "\n".join(s["text"] for s in data.get("segments", []))
    return text, data.get("language")


async def extract_item(row, conn=None) -> tuple[str, str | None, str | None] | None:
    """Returns (text, lang, new_title) or None when extraction is not applicable."""
    type_ = row["type"]
    if type_ == "text":
        return None
    if type_ == "link":
        url = row["source"]["url"]
        if conn is not None:
            from .video.pipeline import video_branch
            result = await video_branch(conn, row, url)
            if result is not None:
                return result
        return await extract_link(url)
    path = Path(row["file_path"])
    if type_ == "pdf":
        return await asyncio.to_thread(_pdf_text, path), None, None
    if type_ == "docx":
        return await asyncio.to_thread(_docx_text, path), None, None
    if type_ == "xlsx":
        return await asyncio.to_thread(_xlsx_text, path), None, None
    if type_ in ("audio", "video"):
        text, lang = await _transcribe(path)
        return text, lang, None
    return None
