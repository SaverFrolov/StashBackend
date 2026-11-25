import asyncio
import json
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import httpx
from bs4 import BeautifulSoup

from ..config import cfg

W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
UA = {"User-Agent": "Mozilla/5.0 (compatible; StashBot/1.0)"}


async def extract_link(url: str) -> tuple[str, str | None, str | None]:
    async with httpx.AsyncClient(
        timeout=30, follow_redirects=True, headers=UA
    ) as client:
        r = await client.get(url)
        r.raise_for_status()
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


async def extract_item(row) -> tuple[str, str | None, str | None] | None:
    """Returns (text, lang, new_title) or None when extraction is not applicable."""
    type_ = row["type"]
    if type_ == "text":
        return None
    if type_ == "link":
        return await extract_link(row["source"]["url"])
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
