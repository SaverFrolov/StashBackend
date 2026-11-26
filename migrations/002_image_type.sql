-- 002: разрешить тип материала 'image'.
-- search_tsv (generated, уже включает title) и GIN items_fts существуют — не трогаем.
-- Запускать от роли stash (владелец таблиц).
ALTER TABLE items DROP CONSTRAINT IF EXISTS items_type_check;
ALTER TABLE items ADD CONSTRAINT items_type_check
  CHECK (type IN ('link','text','pdf','docx','audio','video','xlsx','image'));
