import zipfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.obsidian import build_vault, safe_name, tag_slug


def _row(**kw):
    r = dict(id=uuid4(), type="text", status="done", title="Заметка", lang="ru",
             captured_at=datetime(2026, 11, 26, tzinfo=timezone.utc), source={},
             video_meta=None, original_text="тело", extracted_text=None,
             ai_annotation="аннотация", ai_tags=[], user_comment="коммент",
             file_name=None, file_path=None, file_size=None)
    r.update(kw)
    return r


def test_names_and_tags():
    assert safe_name('a/b:c*d?"e<f>g|h', "x") == "a b c d e f g h"
    assert safe_name("   ", "fallback") == "fallback"
    assert tag_slug("зелёный лук") == "зелёный-лук"
    assert tag_slug("  Recipe! ") == "recipe"


def test_vault_structure(tmp_path):
    a, b = _row(title="Одинаково"), _row(title="Одинаково")
    dest = tmp_path / "v.zip"
    n = build_vault(dest, [a, b], collections={a["id"]: ["Рецепты"]},
                    tags={a["id"]: ["тест"]}, files_dir=tmp_path)
    assert n == 2
    with zipfile.ZipFile(dest) as z:
        names = z.namelist()
        assert "Stash/Одинаково.md" in names and "Stash/Одинаково (2).md" in names
        assert "Stash/Collections/Рецепты.md" in names
        assert "Stash/Stash MOC.md" in names
        note = z.read("Stash/Одинаково.md").decode()
        assert note.startswith("---\n") and "stash_id:" in note
        assert "> [!abstract] Аннотация" in note and "## Комментарий" in note
        assert '- "тест"' in note.split("---")[1]  # тег во frontmatter
        assert "[[Одинаково]]" in z.read("Stash/Collections/Рецепты.md").decode()


def test_attachment_embed(tmp_path):
    f = tmp_path / "doc.pdf"; f.write_bytes(b"%PDF-1.4")
    row = _row(type="pdf", title="Док", file_name="doc.pdf", file_path=str(f),
               file_size=8, original_text=None)
    dest = tmp_path / "v.zip"
    build_vault(dest, [row], files_dir=tmp_path, include_files=True)
    with zipfile.ZipFile(dest) as z:
        assert any(n.startswith("Stash/attachments/") for n in z.namelist())
        assert "![[attachments/" in z.read("Stash/Док.md").decode()
