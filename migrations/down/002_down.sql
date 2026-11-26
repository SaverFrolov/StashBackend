-- Откат 002: вернуть старый список типов. Требует отсутствия строк с type='image'.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM items WHERE type = 'image') THEN
    RAISE EXCEPTION 'Откат 002 невозможен: % строк с type=image. Удали или переклассифицируй их вручную.',
      (SELECT count(*) FROM items WHERE type = 'image');
  END IF;
END $$;

ALTER TABLE items DROP CONSTRAINT IF EXISTS items_type_check;
ALTER TABLE items ADD CONSTRAINT items_type_check
  CHECK (type IN ('link','text','pdf','docx','audio','video','xlsx'));

DO $$
BEGIN
  IF to_regclass('public.schema_migrations') IS NOT NULL THEN
    DELETE FROM schema_migrations WHERE version = '002';
  END IF;
END $$;
