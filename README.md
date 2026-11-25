# StashBackend

Бэкенд личного «входящего ящика» Stash: FastAPI + PostgreSQL (pgvector, RLS).
Контракт API совместим с `RestApi` из `StashFrontend/packages/api-client`.

## Возможности

- Регистрация/вход (Bearer-сессии 30 дней), API-токены для браузерного расширения.
- Захват: текст, ссылки, файлы (PDF/DOCX/XLSX/аудио/видео) — файлы приходят
  data-URL в JSON и хранятся в `~/stash-files/<user_id>/<item_id>/`.
- Асинхронная обработка: FIFO-очередь, 2 параллельных задачи (`WORKER_CONCURRENCY`).
  Извлечение текста (pypdf, docx-zip, openpyxl, HTML для ссылок, faster-whisper
  для аудио/видео) → эмбеддинги Ollama bge-m3 → аннотация и теги через
  OpenAI-совместимый API (по умолчанию airouter, `deepseek/deepseek-v4.1-flash`).
- Поиск: «ключевые слова» (русский FTS), «смысл» (pgvector cosine),
  «умный» (гибрид, RRF). Похожие материалы — по вектору.
- Коллекции, теги (переименование/объединение), экспорт JSON/Markdown.
- Раздача файлов по HMAC-подписанным ссылкам с поддержкой Range (перемотка видео).
- RLS: роль `stash_app` видит только строки своего пользователя (`app.user_id`).

## Запуск (нативно)

```bash
cd ~/StashBackend
cp .env.example .env   # заполнить DSN и SECRET_KEY
.venv/bin/pip install --index-url https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --env-file .env
```

Перед первым запуском применить миграцию (от роли-владельца `stash`):

```bash
psql "postgresql://stash:PASSWORD@localhost:5432/stash" -f migrations/001_ai_original_annotation.sql
```

Тесты: `.venv/bin/python -m pytest tests/ -q`

## Деплой на stashapp.ru (без белого IP)

1. Установить cloudflared (`~/cloudflared.deb`), создать туннель в дашборде
   Cloudflare (Zero Trust → Networks → Tunnels), привязать домен `stashapp.ru`.
2. Public hostname туннеля: `stashapp.ru → http://localhost:8000`.
3. Собрать фронтенд (`pnpm --filter @inbox/web build`) и указать
   `FRONTEND_DIST=~/StashFrontend/apps/web/dist` — API и SPA на одном домене.
4. `PUBLIC_BASE_URL=https://stashapp.ru` в `.env`.
5. Либо полностью в контейнерах: `docker compose up -d` (см. docker-compose.yml,
   нужен токен туннеля в `CLOUDFLARED_TUNNEL_TOKEN`).

## Структура

- `app/routers/` — эндпоинты (auth, items, files, search, collections, tags,
  settings, tokens, export).
- `app/pipeline/` — очередь и воркер обработки, извлечение текста, ИИ.
- `app/db.py` — пулы asyncpg: вход через `stash_login`, роли `stash_auth` (вход)
  и `stash_app` (RLS-контекст) переключаются при подключении.
- `app/security.py` — PBKDF2-пароли, Fernet-шифрование ключей, подпись ссылок.
- `migrations/` — аддитивные миграции к развёрнутой `~/schema.sql`.

## Известные ограничения

- Загрузка файлов — base64 в JSON (контракт фронта): очень большие файлы
  упираются в память браузера и сервера. Для мульти-ГБ нужен multipart-эндпоинт.
- Сброс пароля — заглушка (SMTP появится позже, решение от 2026-11-23).
- Если эмбеддинги недоступны, материал сохраняется, но не участвует
  в поиске «по смыслу» (видно в логах воркера).
