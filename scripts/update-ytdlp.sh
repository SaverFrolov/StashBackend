#!/usr/bin/env bash
# Обновление yt-dlp в venv бэкенда. Платформы меняют вёрстку/форматы —
# без регулярного обновления скачивание начнёт падать с download_failed.
set -euo pipefail
cd "$(dirname "$0")/.."

BEFORE=$(.venv/bin/yt-dlp --version 2>/dev/null || echo "не установлен")
for IDX in https://mirrors.aliyun.com/pypi/simple/ \
           https://pypi.tuna.tsinghua.edu.cn/simple/ \
           https://pypi.org/simple/; do
  echo "=== индекс: $IDX"
  if .venv/bin/pip install --index-url "$IDX" --upgrade yt-dlp; then break; fi
done
echo "yt-dlp: $BEFORE → $(.venv/bin/yt-dlp --version)"

# Тесты не ходят в сеть (подменный yt-dlp), проверяют целостность кода
.venv/bin/python -m pytest tests/ -q

# Живая проверка метаданных на заведомо публичном ролике
.venv/bin/yt-dlp --ignore-config --no-playlist --skip-download \
  --print "OK: %(title)s | %(duration)s s" \
  "https://rutube.ru/video/e26775f62715053e39161c7d7d619961/"
