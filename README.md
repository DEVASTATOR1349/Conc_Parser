# Марк1 — Parser конкурентов косметологии

Автоматический сбор и анализ конкурентов (клиники косметологии) для **НОМОС КЛИНИК**.

## Архитектура

```
n8n (Schedule Trigger)
  │
  ▼ POST /competitors/search
apify-parser ──▶ YouTube API ──▶ Google Sheets
  │                                    │
  ▼ POST /competitors/analyze          │
apify-parser ──▶ AI (OpenRouter) ──────┘
                заполняет: ToV, угроза,
                выводы, рекомендации
```

## Компоненты

| Файл | Назначение |
|---|---|
| `search_competitors.py` | Поиск клиник через YouTube API + Apify Instagram |
| `analyze_competitors.py` | AI-анализ через OpenRouter (Gemini 2.0 Flash) |
| `http_server.py` | HTTP-сервер :8888 для n8n |
| `sheets.py` | Google Sheets клиент (сервисный аккаунт) |
| `n8n_workflow_daily.json` | Workflow для n8n (ежедневный запуск) |

## Переменные окружения (.env)

```
# YouTube API
YOUTUBE_API_KEY=your_youtube_api_key

# Apify
APIFY_API_TOKEN=your_apify_token
APIFY_API_TOKEN_BACKUP=backup_token

# AI / OpenRouter
OPENROUTER_API_KEY=your_openrouter_key

# Google Sheets (сервисный аккаунт)
GOOGLE_APPLICATION_CREDENTIALS=path_to_service_account.json
```

## Запуск

```bash
# Поиск конкурентов
python3 search_competitors.py

# AI-анализ
python3 analyze_competitors.py

# HTTP-сервер для n8n
python3 http_server.py 8888
```
