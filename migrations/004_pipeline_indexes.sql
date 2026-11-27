-- Индексы под recover() и выборку активных задач обработки
CREATE INDEX IF NOT EXISTS processing_steps_item_idx ON processing_steps (item_id);
CREATE INDEX IF NOT EXISTS processing_steps_active_idx ON processing_steps (status)
  WHERE status IN ('pending','running');
CREATE INDEX IF NOT EXISTS items_active_idx ON items (user_id, status)
  WHERE status IN ('saved','extracting','annotating');
