# Транскрибация видео по ссылке (VK Видео, Rutube) — аудит и план

Фаза 0 по prompt.txt. Код не менялся. Дополнение владельца учтено: YouTube вне объёма
(распознаётся парсером, но идёт по HTML-пути с информационным кодом `platform_disabled`);
прокси/cookies для YouTube не добавляются.

## 0. Важное уточнение топологии (сверено с системой)

prompt.txt считает «боевым сервером» VPS в Москве. Фактически:

- **Домашний ПК** (Ubuntu 26.04, 20 потоков, 31 ГБ RAM, 847 ГБ свободно на NVMe):
  здесь живут `stash-api` (uvicorn), PostgreSQL 18 + pgvector, Ollama (bge-m3),
  faster-whisper large-v3 CPU int8 (`~/stash-transcribe`), SSH-туннель на VPS.
  Исходящий IP — **резидентный** (89.169.90.45), не дата-центр.
- **VPS** (onlinevds.ru, Москва, 1 vCPU / 2 ГБ): только Caddy-релей HTTPS → SSH-туннель.
  Postgres/Ollama/whisper там нет и не планируются.

Следствие: yt-dlp/ffmpeg/whisper будут работать **с домашнего ПК**, сетевые проверки
выполнены оттуда же. Ограничения дата-центровых IP к нам не относятся; резидентный IP
может попасть под троттлинг при агрессивных запросах — отсюда лимиты частоты (раздел 8).

## 1. Аудит

### 1.1 Очередь и воркер
- In-process `asyncio.Queue` (`app/pipeline/queue.py`), `WORKER_CONCURRENCY=2`
  воркер-задачи; FIFO, без персистентности очереди.
- Контекст RLS: `app_conn(user_id)` — пул `stash_app`, `set_config('app.user_id')`
  на соединение, `RESET` при освобождении (db.py).
- Падение/перезапуск: `recover()` при старте сканирует по пользователям
  `status IN (extracting, annotating)` и `saved` с pending/running шагами,
  сбрасывает running→pending и переочередляет. Залипших extracting после краха не остаётся.
- Ретраев нет: постоянная ошибка → терминальный статус шага; ручной повтор через
  `POST /items/{id}/retry-extraction` и `/reprocess`.
- Таймауты: httpx 30 с (ссылки), 300 с (ИИ), 15/30 с (тест соединения).
  **У subprocess whisper таймаута нет** — для видеоветки вводим таймауты сетевых
  операций yt-dlp и watchdog зависшего процесса (не общий лимит по длительности).
- Event loop не блокируется: CPU-извлечение в `asyncio.to_thread`, whisper —
  `asyncio.create_subprocess_exec`.

### 1.2 Статусы и прогресс
- `items.status`: saved / extracting / annotating / done / partial /
  extraction_failed / ai_failed. «Требуют внимания» = partial + extraction_failed +
  ai_failed (фильтр attention). «Не полностью» = partial.
- Шаги: `processing_steps` (extract/annotate × pending/running/ok/skipped/failed,
  started_at/finished_at/error). Причина ошибки извлечения: `items.extracted_error`.
- Клиент узнаёт прогресс **опросом**: лента и детали refetch каждые 3 с, пока есть
  extracting/annotating (сделано 2026-11-25). SSE нет. Подстатуса/процента нет —
  добавляем аддитивно (раздел 3).

### 1.3 Точка входа для видеоветки
`app/pipeline/extract.py::extract_item(row)` для `type=link` вызывает
`extract_link(url)` (httpx + BeautifulSoup, SSRF-гард с проверкой каждого редиректа).
Видеоветка встраивается в `extract_item` до HTML-пути:
`parse_video_url(url)` → платформа включена и флаг оператора on → видео-конвейер;
иначе — существующий HTML без изменений.

### 1.4 faster-whisper (замер выполнен)
- `~/stash-transcribe/transcribe.py`: large-v3, **CPU int8**, `cpu_threads=0` (авто),
  язык авто, `beam_size=5`, `vad_filter=True`. Возвращает сегменты с таймкодами
  (`--json --output`), прогресса нет, модель грузится на каждый вызов (~20–30 с).
- Замер на этом ПК: сгенерировано 114.8 с русской речи (espeak-ng), обработка
  **145 с wall → RTF ≈ 1.26** (включая загрузку модели). Синтетическая речь чище
  реальной — реальный RTF может отличаться; ориентир: 2-часовая лекция ≈ 2.5 часа CPU.
  Субтитры экономят это время полностью. Лимит по длительности НЕ вводится (решение владельца).
- Для видео расширяем аддитивно (раздел 3.5): мульти-файловый режим с одной загрузкой
  модели, прогресс-строки в stdout, поведение одиночного вызова не меняется.

### 1.5 Хранение текста, FTS, эмбеддинги, аннотация
- Извлечённый текст: `items.extracted_text` (text, TOAST). FTS — generated column
  `search_tsv` (russian) по title + original + extracted + annotation + comment
  (миграция 002 добавила title). Транскрипт попадёт в FTS целиком автоматически.
- Эмбеддинги: **уже фрагментные** — `chunk_text(size=1500, overlap=200)` →
  `item_chunks` с вектором bge-m3 на фрагмент (HNSW cosine). Длинные транскрипты
  получают много фрагментов без изменения схемы; лимит входа bge-m3 (8192 токена)
  с запасом соблюдён размером чанка.
- Аннотация: `MAX_AI_CHARS=300_000`, берётся **только начало**. Для многочасовых
  транскриптов предлагаю выборку начало+середина+конец (раздел 3.7).

### 1.6 Настройки
- Per-user: `ai_settings` (provider, base_url, model, api_key_enc Fernet, auto_process).
- Глобально: env (`app/config.py`). Админ: `ADMIN_EMAIL`.
- Новые пользовательские `transcribe_video_links`, `transcribe_language` —
  аддитивные колонки `ai_settings` (миграция 003).

### 1.7 Удаление и экспорт
- Удаление элемента: FK-каскады (chunks, steps, item_tags, item_collections) +
  `rmtree` каталога файлов. Новая таблица транскриптов — ON DELETE CASCADE от items.
- Удаление аккаунта: каскад от users + rmtree каталога пользователя.
- Экспорт JSON/Markdown: `items_dto` + `_strip`/`_markdown` — транскрипт и
  video-метаданные добавляются аддитивно (в JSON — поле transcript при includeExtracted,
  в Markdown — секция с таймкодами опционально).

### 1.8 Деплой и тесты
- Нативный деплой (systemd `stash-api`, `stash-tunnel`), Dockerfile/compose есть,
  но устарели относительно реальной схемы (VPS-релей). README обновим.
- Тесты: юнит без БД (25 шт.), живой E2E `scripts/smoke.py` против развёрнутого API.
  Фикстур БД нет — интеграционные тесты видеоветки пойдут с подменными бинарниками
  против локальной БД с временными пользователями (стиль smoke), без реальных платформ.
- scripts/: smoke.py. Добавим fake_ytdlp.py, update-ytdlp.sh.

### 1.9 Ресурсы
- Домашний ПК: 20 потоков / 31 ГБ RAM / 847 ГБ диск. Сервисы-соседи: PostgreSQL,
  Ollama (эмбеддинги), stash-api, рабочий стол. Whisper уже использует все потоки
  при авто-настройке — для видео ограничим `VIDEO_WHISPER_THREADS` и nice.
- VPS: 1 vCPU / 2 ГБ, только Caddy — видеозадачи туда не идут.

### 1.10 Сеть (проверено с домашнего ПК, yt-dlp 2026.11.23, ffmpeg 8.0.1)
- DNS: rutube.ru ✓ (178.248.233.148 и др.), vk.com ✓ (87.240.132.72 и др.).
- **Rutube**: метаданные без скачивания ✓ на двух публичных роликах
  («Клиповое мышление…», 93 с; «2026 год станет решающим…», 1415 с).
  Субтитров у проверенных роликов нет (`has no subtitles`).
- **VK Видео**: метаданные ✓ без входа на публичном ролике
  (video-219568993_456245989, 48 с). Субтитров нет. Приватные/групповые ролики
  требуют входа — yt-dlp вернёт ошибку логина → код `private_or_login_required`
  (проверка на реальном приватном ролике — в ручном чек-листе).
- Троттлинг с резидентного IP не наблюдался при единичных запросах; вводим
  per-user rate limit иVIDEO_CONCURRENCY=1 как защиту.
- **YouTube не проверялся** (дополнение владельца). Стратегия на будущее —
  раздел «Как включить YouTube позже» в docs/video-transcription.md (фаза 4).
- Флаги yt-dlp сверены с `--help` установленной версии: `--use-extractors`,
  `--ignore-config`, `--no-playlist`, `--socket-timeout`, `--write-subs`,
  `--write-auto-subs`, `--sub-format`, `--sub-langs`, `-x --audio-format` — на месте.

## 2. Архитектура (предложение)

### 2.1 Провайдеры платформ
Общий интерфейс (не предполагает ровно две платформы):

```python
class VideoProvider(Protocol):
    platform: str
    async def metadata(url) -> VideoMeta          # title, channel, duration, lang, is_live, subs tracks
    async def fetch_subtitles(url, track, dest) -> Path
    async def fetch_audio(url, dest) -> Path      # только аудиодорожка
```

Реализации `VkProvider`, `RutubeProvider` — поверх yt-dlp subprocess (общий
runner с безопасным запуском, раздел 2.6). Реестр `PROVIDERS: dict[str, VideoProvider]`;
YouTube-провайдер НЕ пишется, но парсер URL платформу youtube распознаёт.

### 2.2 Разбор URL (структурный, urlparse)
- VK: vk.com/video-XXXX_YYYY, vkvideo.ru/video-…, clips; m.vkvideo.ru → канонизация.
- Rutube: rutube.ru/video/<hash>/, play/embed → канонизация.
- YouTube: watch/youtu.be/shorts/embed/m./music. — распознаётся, но платформа
  выключена → HTML-путь + информационный код `platform_disabled`.
- Плейлисты/каналы: `--no-playlist`, берётся одно видео; если URL не содержит
  видео → причина `unsupported_url`.
- Белый список доменов, запрет userinfo/IP/localhost/приватных/похожих доменов/
  схем кроме http(s)/URL, начинающихся с «-» (раздел 8 prompt).

### 2.3 Конвейер (в стадии extracting для ссылок)
1. `resolving`: метаданные yt-dlp `-J --skip-download` (таймаут сети 20 с, retries 2).
   Быстрые отказы по кодам (раздел 2.5). Длительность НЕ используется для отказа.
2. `captions`: если VIDEO_ALLOW_SUBTITLES и трек есть: ручные на языке материала →
   авто; переводные не берём. Скачивание `--write-subs/--write-auto-subs
   --sub-format vtt/srt --sub-langs <lang> --skip-download` в tmp задачи.
   Парсер VTT/SRT → сегменты {start,end,text}; дедупликация накатывающихся повторов
   авто-субтитров; качество: непусто и покрытие ≥ ~70% длительности, иначе шаг 3.
3. `downloading_audio`: если VIDEO_ALLOW_AUDIO_DOWNLOAD: проверка свободного места
   (VIDEO_MIN_FREE_DISK_MB; при нехватке — отложить задачу и повторить позже, не падать);
   yt-dlp `-f bestaudio -x --audio-format wav --audio-quality 0` → 16 кГц моно WAV
   в tmp задачи (не в ~/stash-files).
4. `transcribing`: существующий faster-whisper через расширенный transcribe.py
   (раздел 2.4). Текст → `items.extracted_text` (существующий слой) → эмбеддинги/FTS/
   аннотация работают без изменений. Сегменты → `video_transcripts`.
5. Fallback: постоянная ошибка видеоветки → существующее HTML-извлечение;
   результат и код причины сохраняются (статус — вопрос чекпоинта, раздел 4).
6. Очистка: tmp задачи удаляется в finally + GC при старте; аудио после распознавания
   не хранится.

### 2.4 Расширение transcribe.py (аддитивное)
- Новый режим: несколько входов за один процесс (одна загрузка модели),
  `--output-dir`, прогресс-строки `PROGRESS {json}` в stdout по ходу сегментов.
- Воркер режет аудио ffmpeg по паузам на куски ~10 мин (перекрытие ~5 с),
  вызывает расширенный режим, **сохраняет результат каждого куска сразу**
  в video_transcripts (status=partial) и обновляет прогресс элемента;
  после перезапуска продолжает с последнего готового куска (resume по chunk_no).
- Дедупликация перехлёста по таймкодам при склейке.
- Одиночный вызов (аудио/видео-файлы пользователя) не меняется.
- Запуск: `start_new_session=True`, `preexec_fn=os.nice(VIDEO_NICE)`,
  `--threads VIDEO_WHISPER_THREADS`; отмена — kill всей группы процессов,
  watchdog на зависание (отсутствие прогресса N минут → перезапуск куска),
  без общего лимита длительности.

### 2.5 Коды ошибок (маппинг stderr/исключений yt-dlp)
unsupported_url, private_or_login_required, age_restricted, region_blocked,
live_stream, not_found, no_speech, platform_blocked («подтвердите, что вы не бот»),
platform_unreachable (DNS/сеть), download_failed, transcription_failed.
`too_long` зарезервирован (только при включённом оператором лимите, по умолчанию 0=выкл).
`platform_disabled` — информационный, не ошибка. Сообщения без URL/токенов/путей.

### 2.6 Безопасный запуск процессов
- subprocess массивом аргументов, без shell; очищенное окружение (allowlist
  PATH/HOME/LANG/TMPDIR, без секретов бэкенда); `--ignore-config --no-playlist
  --socket-timeout 20 --retries 2 --use-extractors vk,rutube` (без generic);
  бинарники из env YTDLP_BIN/FFMPEG_BIN (подмена в тестах); tmp на задачу;
  kill группы при отмене; лимит размера скачанного файла (защита диска, не длительности);
  логи без query-параметров URL; секреты только из env.

### 2.7 Нагрузка на очередь
- Отдельная видео-очередь/воркер с `VIDEO_CONCURRENCY=1` (глобально) и не более
  1 активной видеозадачи на пользователя; обычные 2 воркера не заняты видео.
- Rate limit запросов к платформам на пользователя (например, 6/час) — защита
  резидентного IP от троттлинга.
- VIDEO_WHISPER_THREADS=8 (из 20), VIDEO_NICE=10 — API/Postgres остаются отзывчивыми.

### 2.8 Кэш
Только per-user: повторная ссылка того же пользователя → готовый транскрипт
копируется в новый элемент (по platform+video_id в рамках user_id). Общий кэш
не делаем (RLS проще, данных пользователей нет в общем доступе) — вопрос чекпоинта.

## 3. Данные и контракт (аддитивно)

### 3.1 Миграция 003_video_transcripts.sql (002 занят картинками)
- `video_transcripts`: item_id FK CASCADE, user_id, source
  (captions_manual|captions_auto|asr), language, segments JSONB (TOAST), text,
  model, duration_sec, status (partial|done), chunk_no, updated_at;
  RLS p_self по образцу существующих таблиц + GRANT stash_app; индекс (item_id).
- `items` аддитивно: `video_meta JSONB` (platform, video_id, title, channel,
  duration_sec, source_url), `extract_stage TEXT`, `extract_progress JSONB`
  {current,total,percent}, `extract_error_code TEXT`.
- `ai_settings` аддитивно: `transcribe_video_links BOOL DEFAULT true`,
  `transcribe_language TEXT DEFAULT 'auto'` (auto|ru|en).
- Отдельный файл отката 003_rollback.sql. Идемпотентность IF NOT EXISTS.
- **К боевой БД не применяю** — инструкция в README (роль stash).

### 3.2 API (только аддитивно)
- В ответах элемента необязательные: `video {platform, videoId, title, channel,
  durationSec, sourceUrl}`, `videoProgress {stage, current?, total?, percent?}`,
  `videoError {code, message}`. Существующие поля не меняются.
- `GET /items/{id}/transcript?offset=&limit=` — сегменты с таймкодами, источник,
  язык, статус (пагинация; транскрипты огромные).
- Повтор: существующие `/reprocess`, `/retry-extraction` (владелец + rate limit).
- Настройки: GET/PUT /settings/ai расширяется новыми полями.
- Экспорт JSON/MD включает транскрипт и метаданные; удаление элемента/аккаунта
  удаляет всё новое (каскады + GC tmp).

### 3.3 Аннотация длинных транскриптов
Текущий механизм берёт первые 300k символов. Предлагаю **детерминированную
выборку thirds**: начало + середина + конец (по ~100k) с разделителями. Обоснование:
один вызов модели (стоимость/латентность как сейчас), сохраняет введение,
основную часть и выводы; иерархическое резюме точнее, но требует N вызовов и
новой логики слияния — не оправдано для v1. Вопрос чекпоинта.

### 3.4 Эмбеддинги/поиск
Схема не меняется: фрагментные эмбеддинги уже есть (item_chunks), FTS по всему
транскрипту работает сразу. Опциональная задача на будущее: лимит числа чанков
на элемент (сейчас лимитов нет по решению владельца).

## 4. Вопросы чекпоинта (жду «ок» и решений)

1. **Статус при постоянной ошибке видеоветки с успешным HTML-fallback**:
   предлагаю `done`/`partial` по существующей семантике HTML + информационный
   `videoError.code` (элемент не попадает в «Требуют внимания» из-за видео).
   Если HTML тоже пуст → `extraction_failed` с кодом. Подтверди.
2. **Аннотация**: выборка начало+середина+конец (раздел 3.3) — ок?
3. **Кэш**: только per-user — ок?
4. **Дефолты оператора**: VIDEO_LINKS_ENABLED=false, VIDEO_PLATFORMS=vk,rutube,
   VIDEO_ALLOW_SUBTITLES=true, VIDEO_ALLOW_AUDIO_DOWNLOAD=true,
   VIDEO_CONCURRENCY=1, VIDEO_WHISPER_THREADS=8, VIDEO_NICE=10,
   VIDEO_MIN_FREE_DISK_MB=2048, VIDEO_MAX_DURATION_MIN=0,
   VIDEO_DAILY_MINUTES_PER_USER=0 — ок?
5. **Пользовательская настройка** transcribe_video_links по умолчанию true
   (когда оператор включил функцию) — ок?
6. **Нарезка ASR**: куски ~10 мин по паузам, перехлёст ~5 с, дедуп по таймкодам,
   сохранение каждого куска сразу, resume после перезапуска — ок?
7. Миграция **003** (002 занят) — к сведению.

## 5. Фазы реализации (после «ок»)

1. Миграция 003 + откат, модели/настройки/поля контракта, флаги (функция выключена).
2. Парсер URL, провайдеры VK/Rutube, метаданные, субтитры, коды ошибок,
   безопасный запуск (подменный yt-dlp).
3. Аудио + ASR: загрузка, нарезка, расширенный transcribe.py, прогресс, куски,
   resume, приоритет/потоки, отдельная очередь, отмена.
4. Интеграция в extracting, fallback на HTML, platform_disabled для YouTube,
   индексация, очистка, экспорт, удаление, docs/video-transcription.md
   (+ раздел «Как включить YouTube позже»), README, скрипт обновления yt-dlp.
5. Фронтенд (раздел 12 prompt): типы/Zod, карточка с длительностью и подстатусом,
   ошибки с подсказками (platform_disabled — нейтральная подсказка без красного),
   сворачиваемый транскрипт с пагинацией и таймкодами, настройки, locales ru/en,
   мобильное (StyleSheet, 44dp, 360–480dp, fontScale 1.3).

После каждой фазы — коммит и краткий отчёт. Тесты: подменный yt-dlp
(scripts/fake_ytdlp.py, все сценарии раздела 10 prompt, кроме YouTube-прокси),
подменный whisper, реальное маленькое аудио с ffmpeg; проверки валидатора,
парсеров субтитров, нарезки/склейки, конвейера, RLS, нагрузки, экспорта, удаления.
Ручной чек-лист — только VK Видео и Rutube.

## 6. Известные ограничения и риски (для отчёта)

- CPU RTF ≈ 1.26: многочасовые видео считаются часами; субтитры — основной
  быстрый путь; nice/threads защищают отзывчивость, но не ускоряют ASR.
- YouTube из РФ недоступен без обхода — вне объёма; включение позже требует
  зарубежного прокси/туннеля и отдельного провайдера, результат не гарантирован.
- yt-dlp нужно регулярно обновлять (платформы меняют API); скрипт обновления
  и закреплённая версия в README.
- Правила платформ: скачивание аудио для личной транскрибации — серая зона;
  оператор сам решает, что включать (флаги по умолчанию выключены до чек-листа).
- Резидентный IP: агрессивная частота запросов может привести к троттлингу —
  rate limit и VIDEO_CONCURRENCY=1.
- Приватность: аудио и текст обрабатываются локально; во внешние сервисы уходит
  только текст для аннотации (как сейчас). Зафиксируем в docs.
