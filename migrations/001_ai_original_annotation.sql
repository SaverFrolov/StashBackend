-- Хранит исходную ИИ-аннотацию, чтобы restoreAi возвращал её без повторного вызова модели.
-- Запускать от роли stash (владелец таблиц): psql "postgresql://stash:PASSWORD@localhost:5432/stash" -f migrations/001_ai_original_annotation.sql
ALTER TABLE items ADD COLUMN IF NOT EXISTS ai_original_annotation text;
