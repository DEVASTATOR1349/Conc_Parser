# Марк1 — Парсер конкурентов косметологии

Полностью самодостаточный сервис для сбора и анализа конкурентов-клиник косметологии в Москве.

## Возможности

- **Brave Search API** — поиск клиник по ключевым запросам (сайты, отзывы)
- **YouTube Data API** — поиск каналов клиник/косметологов
- **Apify Instagram Scraper** — поиск Instagram-профилей
- **Google Sheets** — запись результатов (опционально)
- **HTTP-сервер** — для n8n или внешних вызовов
- **Docker** — изолированный контейнер

## Быстрый старт

```bash
# 1. Скопировать .env
cp .env.example .env
# Заполнить ключи (хотя бы BRAVE_API_KEY)

# 2. Запустить поиск
docker compose run --rm mark1 python3 /app/search_competitors.py

# 3. Или HTTP-сервер для n8n
docker compose up -d
```

## Переменные окружения

| Переменная | Для чего | Обязательно |
|---|---|---|
| `BRAVE_API_KEY` | Brave Search (2000 запросов/мес, бесплатно) | Да (если без него — только YouTube) |
| `YOUTUBE_API_KEY` | YouTube Data API | Нет |
| `APIFY_API_TOKEN` | Instagram через Apify | Нет |
| `OPENROUTER_API_KEY` | AI-анализ (ToV, выводы) | Нет |
| `GOOGLE_CREDENTIALS_JSON` | Google Sheets (JSON сервисного аккаунта) | Нет |
| `SHEET_ID` | ID таблицы для записи | Нет (без неё — dry-run) |

## Команды

```bash
# Поиск конкурентов
docker compose run --rm mark1 python3 /app/search_competitors.py

# AI-анализ (дозаполняет творческие поля)
docker compose run --rm mark1 python3 /app/analyze_competitors.py

# HTTP-сервер (порт 8888)
docker compose up -d
# POST /competitors/search
# POST /competitors/analyze
# GET  /competitors/status
# GET  /health
```

## API эндпоинты (HTTP-сервер)

```bash
curl -X POST http://localhost:8888/competitors/search
# → {"success": true, "total": 15, "report": "..."}

curl -X POST http://localhost:8888/competitors/analyze
# → {"success": true, "total": 10, "report": "..."}
```

## Лицензия

MIT
