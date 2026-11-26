import time
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app import security
from app.config import cfg
from app.dto import _clean, iso, item_dto
from app.pipeline.ai import chunk_text
from app.routers.export import ExportOptions, _markdown, _strip
from app.routers.items import guess_lang, title_from_text, type_from_mime


def test_password_roundtrip():
    stored = security.hash_password("correct horse battery staple")
    assert security.verify_password("correct horse battery staple", stored)
    assert not security.verify_password("wrong", stored)
    assert not security.verify_password("x", "garbage")
    assert security.hash_password("x") != security.hash_password("x")


def test_api_key_encryption(monkeypatch):
    enc = security.encrypt_api_key("sk-secret-123")
    assert enc != "sk-secret-123"
    assert security.decrypt_api_key(enc) == "sk-secret-123"
    monkeypatch.setattr(cfg, "secret_key", "another-key")
    assert security.decrypt_api_key(enc) is None


def test_file_url_signing(monkeypatch):
    uid, iid = uuid4(), uuid4()
    url = security.sign_file_url(uid, iid, ttl=60)
    parts = dict(p.split("=") for p in url.split("?")[1].split("&"))
    assert security.verify_file_url(str(uid), str(iid), parts["exp"], parts["sig"])
    assert not security.verify_file_url(str(uid), str(uuid4()), parts["exp"], parts["sig"])
    assert not security.verify_file_url(str(uid), str(iid), parts["exp"], "0" * 64)
    expired = security.sign_file_url(uid, iid, ttl=-1)
    parts = dict(p.split("=") for p in expired.split("?")[1].split("&"))
    assert not security.verify_file_url(str(uid), str(iid), parts["exp"], parts["sig"])


def test_chunk_text():
    assert chunk_text("") == []
    assert chunk_text("короткий текст") == ["короткий текст"]
    text = "\n\n".join(f"Абзац номер {i} " + "слово " * 40 for i in range(60))
    chunks = chunk_text(text, size=1500, overlap=200)
    assert len(chunks) > 1
    assert all(c.strip() for c in chunks)
    assert all(len(c) <= 1500 for c in chunks)
    # every paragraph survives in some chunk
    for i in range(60):
        assert any(f"Абзац номер {i} " in c for c in chunks)


def test_chunk_text_no_whitespace():
    text = "я" * 5000
    chunks = chunk_text(text, size=1500, overlap=200)
    assert len(chunks) >= 3
    assert "".join(c for c in chunks).count("я") >= 5000


def test_type_from_mime():
    assert type_from_mime("application/pdf", "a.pdf") == "pdf"
    assert type_from_mime("", "a.PDF") == "pdf"
    assert type_from_mime("audio/mpeg", "a.mp3") == "audio"
    assert type_from_mime("video/mp4", "a.mp4") == "video"
    assert type_from_mime("", "a.mp3") == "audio"
    assert type_from_mime("", "a.mkv") == "video"
    assert type_from_mime("application/vnd.ms-excel", "a.xls") == "xlsx"
    assert type_from_mime("", "a.xlsx") == "xlsx"
    assert (
        type_from_mime(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "a.docx",
        )
        == "docx"
    )


def test_type_from_mime_image():
    assert type_from_mime("image/png", "a.png") == "image"
    assert type_from_mime("image/jpeg", "a.jpg") == "image"
    assert type_from_mime("", "photo.JPEG") == "image"
    assert type_from_mime("", "pic.webp") == "image"
    # SVG must not become an inline-rendered image type
    assert type_from_mime("image/svg+xml", "a.svg") != "image"


def test_image_skips_extraction():
    import asyncio

    from app.pipeline.extract import extract_item

    row = {"type": "image", "file_path": "/tmp/whatever.png"}
    assert asyncio.run(extract_item(row)) is None


def test_guess_lang_and_title():
    assert guess_lang("привет мир") == "ru"
    assert guess_lang("hello world") == "en"
    assert guess_lang("1234") is None
    assert title_from_text("первая строка\nвторая") == "первая строка"
    assert title_from_text("я" * 100) == "я" * 80 + "…"


def test_iso_and_clean():
    dt = datetime(2026, 11, 24, 12, 0, 0, 500000, tzinfo=timezone.utc)
    assert iso(dt) == "2026-11-24T12:00:00.500Z"
    assert iso(None) is None
    assert _clean({"a": 1, "b": None}) == {"a": 1}


def _row(**kw):
    base = {
        "id": uuid4(),
        "user_id": uuid4(),
        "type": "text",
        "status": "done",
        "title": "Заметка",
        "lang": "ru",
        "captured_at": datetime(2026, 11, 24, tzinfo=timezone.utc),
        "source": {},
        "original_text": "текст",
        "file_name": None,
        "file_size": None,
        "file_mime": None,
        "file_path": None,
        "extracted_text": None,
        "extracted_error": None,
        "ai_annotation": "аннотация",
        "ai_model": "m",
        "ai_generated_at": None,
        "ai_edited": False,
        "ai_tags": ["тег"],
        "user_comment": None,
    }
    base.update(kw)
    return base


def test_item_dto_text():
    dto = item_dto(_row(), ["мойтег"], [], [], "https://stashapp.ru")
    assert dto["original"] == {"text": "текст"}
    assert dto["ai"]["tags"] == ["тег"]
    assert dto["suggestedTags"] == ["тег"]
    assert dto["userTags"] == ["мойтег"]
    assert "userComment" not in dto  # null must be omitted, not sent
    assert dto["capturedAt"].endswith("Z")


def test_item_dto_file_url():
    row = _row(
        type="video",
        original_text=None,
        file_name="lecture.mp4",
        file_size=123,
        file_mime="video/mp4",
        file_path="/tmp/x/lecture.mp4",
    )
    dto = item_dto(row, [], [], [], "https://stashapp.ru")
    url = dto["original"]["file"]["url"]
    assert url.startswith("https://stashapp.ru/files/")
    parts = dict(p.split("=") for p in url.split("?")[1].split("&"))
    assert security.verify_file_url(
        str(row["user_id"]), str(row["id"]), parts["exp"], parts["sig"]
    )


def test_export_strip_and_markdown():
    dto = item_dto(_row(user_comment="комментарий"), [], [], [], "")
    opts = ExportOptions(
        format="markdown",
        includeOriginals=False,
        includeExtracted=False,
        includeAi=False,
        includeComments=False,
    )
    stripped = _strip(dto, opts)
    assert stripped["original"] == {}
    assert stripped["ai"]["tags"] == []
    assert "userComment" not in stripped
    md = _markdown([dto])
    assert md.startswith("# Заметка")
    assert "## My comment" in md and "комментарий" in md
