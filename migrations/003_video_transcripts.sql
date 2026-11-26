-- Видео по ссылке: транскрипты, метаданные, подстатус извлечения, настройки.
-- Аддитивная, идемпотентная. Запускать от роли stash (владелец таблиц):
--   psql "postgresql://stash:PASSWORD@localhost:5432/stash" -f migrations/003_video_transcripts.sql
-- К боевой БД применяется оператором вручную; функция выключена флагом VIDEO_LINKS_ENABLED.

CREATE TABLE IF NOT EXISTS video_transcripts (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  item_id      uuid NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  source       text NOT NULL CHECK (source IN ('captions_manual','captions_auto','asr')),
  language     text,
  segments     jsonb NOT NULL DEFAULT '[]',
  text         text NOT NULL DEFAULT '',
  model        text,
  duration_sec integer,
  status       text NOT NULL DEFAULT 'partial' CHECK (status IN ('partial','done')),
  chunk_no     integer NOT NULL DEFAULT 0,
  updated_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (item_id, chunk_no)
);
CREATE INDEX IF NOT EXISTS video_transcripts_item_idx ON video_transcripts (item_id);

ALTER TABLE video_transcripts ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_self ON video_transcripts;
CREATE POLICY p_self ON video_transcripts FOR ALL TO stash_app
  USING (user_id = current_setting('app.user_id', true)::uuid)
  WITH CHECK (user_id = current_setting('app.user_id', true)::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON video_transcripts TO stash_app;

ALTER TABLE items ADD COLUMN IF NOT EXISTS video_meta jsonb;
ALTER TABLE items ADD COLUMN IF NOT EXISTS extract_stage text;
ALTER TABLE items ADD COLUMN IF NOT EXISTS extract_progress jsonb;
ALTER TABLE items ADD COLUMN IF NOT EXISTS extract_error_code text;

ALTER TABLE ai_settings ADD COLUMN IF NOT EXISTS transcribe_video_links boolean NOT NULL DEFAULT true;
ALTER TABLE ai_settings ADD COLUMN IF NOT EXISTS transcribe_language text NOT NULL DEFAULT 'auto';
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ai_settings_transcribe_language_check') THEN
    ALTER TABLE ai_settings ADD CONSTRAINT ai_settings_transcribe_language_check
      CHECK (transcribe_language IN ('auto','ru','en'));
  END IF;
END $$;
