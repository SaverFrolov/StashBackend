-- Картинки как тип материала + поиск по имени файла (title) для всех материалов.
-- Запускать от роли stash (владелец таблиц).
ALTER TABLE items DROP CONSTRAINT IF EXISTS items_type_check;
ALTER TABLE items ADD CONSTRAINT items_type_check
  CHECK (type IN ('text','link','pdf','docx','xlsx','audio','video','image'));

ALTER TABLE items DROP COLUMN IF EXISTS search_tsv;
ALTER TABLE items ADD COLUMN search_tsv tsvector GENERATED ALWAYS AS (
  to_tsvector('russian',
    coalesce(title, '') || ' ' ||
    coalesce(original_text, '') || ' ' ||
    coalesce(extracted_text, '') || ' ' ||
    coalesce(ai_annotation, '') || ' ' ||
    coalesce(user_comment, ''))
) STORED;
CREATE INDEX IF NOT EXISTS items_search_idx ON items USING gin (search_tsv);
