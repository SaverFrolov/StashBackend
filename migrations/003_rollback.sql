-- Откат 003_video_transcripts.sql. Запускать от роли stash.
DROP TABLE IF EXISTS video_transcripts;
ALTER TABLE items DROP COLUMN IF EXISTS video_meta;
ALTER TABLE items DROP COLUMN IF EXISTS extract_stage;
ALTER TABLE items DROP COLUMN IF EXISTS extract_progress;
ALTER TABLE items DROP COLUMN IF EXISTS extract_error_code;
ALTER TABLE ai_settings DROP COLUMN IF EXISTS transcribe_video_links;
ALTER TABLE ai_settings DROP COLUMN IF EXISTS transcribe_language;
