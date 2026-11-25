-- Хранит исходную ИИ-аннотацию, чтобы restoreAi возвращал её без повторного вызова модели.
-- Запускать от роли stash (владелец таблиц): psql "$APP_DSN" -f migrations/001_ai_original_annotation.sql
ALTER TABLE items ADD COLUMN IF NOT EXISTS ai_original_annotation text;
