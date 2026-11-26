"""Сборка Obsidian-vault: заметка на материал, YAML frontmatter, викилинки, вложения."""
from __future__ import annotations

import json
import re
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = "Stash"
ATT = "attachments"
EMBED_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".avif",
             ".pdf", ".mp3", ".wav", ".ogg", ".m4a", ".flac", ".mp4", ".webm", ".mov"}

_BAD = re.compile(r'[\\/:*?"<>|#^\[\]\x00-\x1f]')


def _obj(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return {}
    return v or {}


def safe_name(name: str, fallback: str) -> str:
    name = _BAD.sub(" ", name or "")
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:80] or fallback


def tag_slug(t: str) -> str:
    t = re.sub(r"\s+", "-", (t or "").strip().lower())
    return re.sub(r"[^\w\-/]", "", t, flags=re.UNICODE).strip("-")


def _y(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, datetime):
        return v.isoformat()
    s = str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace("\r", " ")
    return f'"{s}"'


def frontmatter(pairs: dict) -> str:
    out = ["---"]
    for k, v in pairs.items():
        if v is None or v == "" or v == []:
            continue
        if isinstance(v, (list, tuple)):
            out.append(f"{k}:")
            out += [f"  - {_y(x)}" for x in v]
        else:
            out.append(f"{k}: {_y(v)}")
    return "\n".join(out + ["---"])


def _ts(sec) -> str:
    sec = int(sec or 0)
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _cue(url: str, sec) -> str:
    if not url:
        return _ts(sec)
    sep = "&" if "?" in url else "?"
    return f"[{_ts(sec)}]({url}{sep}t={int(sec or 0)})"


def build_note(row, *, collections=(), tags=(), segments=(), attach=None,
               include_transcript=True, include_extracted=True) -> str:
    src = _obj(row["source"])
    url = src.get("url") or ""
    vm = _obj(row["video_meta"])
    title = row["title"] or row["file_name"] or url or str(row["id"])

    fm = frontmatter({
        "title": title,
        "type": row["type"],
        "stash_id": str(row["id"]),
        "captured": row["captured_at"],
        "status": row["status"],
        "source": url or None,
        "platform": vm.get("platform"),
        "channel": vm.get("channel"),
        "duration_sec": vm.get("duration_sec"),
        "lang": row["lang"],
        "tags": sorted({tag_slug(t) for t in tags if tag_slug(t)}),
        "collections": list(collections),
        "file": row["file_name"],
    })

    body = [f"# {title}", ""]
    if row["ai_annotation"]:
        body += ["> [!abstract] Аннотация"]
        body += [f"> {ln}" for ln in row["ai_annotation"].splitlines() if ln.strip()]
        body.append("")
    if row["user_comment"]:
        body += ["## Комментарий", "", row["user_comment"], ""]
    if url:
        body += ["## Источник", "", f"<{url}>", ""]
    if attach:
        emb = "!" if Path(attach).suffix.lower() in EMBED_EXT else ""
        body += ["## Вложение", "", f"{emb}[[{attach}]]", ""]
    if include_transcript and segments:
        body += ["## Транскрипт", ""]
        body += [f"- {_cue(url, s.get('start'))} {(s.get('text') or '').strip()}"
                 for s in segments if (s.get("text") or "").strip()]
        body.append("")
    text = row["original_text"] or (row["extracted_text"] if include_extracted else None)
    if text and not (attach and row["original_text"] is None):
        body += ["## Текст", "", text, ""]
    return fm + "\n\n" + "\n".join(body).rstrip() + "\n"


def build_vault(dest: Path, items, *, collections=None, tags=None, transcripts=None,
                files_dir: Path, include_files=True, include_transcript=True,
                include_extracted=True) -> int:
    collections, tags, transcripts = collections or {}, tags or {}, transcripts or {}
    used: set[str] = set()
    names: dict = {}
    for row in items:
        base = safe_name(row["title"] or row["file_name"] or "", str(row["id"])[:8])
        name, n = base, 2
        while name.lower() in used:
            name, n = f"{base} ({n})", n + 1
        used.add(name.lower())
        names[row["id"]] = name

    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for row in items:
            iid = row["id"]
            attach = None
            fp = row["file_path"]
            if include_files and fp and Path(fp).is_file():
                attach = f"{ATT}/{str(iid)[:8]}/{Path(fp).name}"
                z.write(fp, f"{ROOT}/{attach}")
            z.writestr(
                f"{ROOT}/{names[iid]}.md",
                build_note(row, collections=collections.get(iid, ()), tags=tags.get(iid, ()),
                           segments=transcripts.get(iid, ()), attach=attach,
                           include_transcript=include_transcript,
                           include_extracted=include_extracted),
            )

        by_col: dict[str, list] = {}
        for row in items:
            for c in collections.get(row["id"], ()):
                by_col.setdefault(c, []).append(names[row["id"]])
        for col, notes in sorted(by_col.items()):
            moc = frontmatter({"title": col, "type": "collection"}) + "\n\n"
            moc += f"# {col}\n\n" + "\n".join(f"- [[{n}]]" for n in sorted(notes)) + "\n"
            z.writestr(f"{ROOT}/Collections/{safe_name(col, 'collection')}.md", moc)

        idx = frontmatter({"title": "Stash", "type": "index"}) + "\n\n# Stash\n\n"
        idx += f"Материалов: {len(items)}\n\n"
        for t in sorted({row["type"] for row in items}):
            idx += f"## {t}\n\n"
            idx += "\n".join(f"- [[{names[r['id']]}]]" for r in items if r["type"] == t) + "\n\n"
        if by_col:
            idx += "## Коллекции\n\n"
            idx += "\n".join(f"- [[{safe_name(c, 'collection')}]]" for c in sorted(by_col)) + "\n"
        z.writestr(f"{ROOT}/Stash MOC.md", idx)
    return len(items)
