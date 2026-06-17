# Марк 1 — Конкурентный анализ и обогащение

**Автоматизированный пайплайн сбора и AI-обогащения конкурентов для клиентов (клиники, нутрициологи, косметология).**

## Архитектура

```
┌─────────────────┐     ┌─────────────────┐     ┌────────────────┐
│   Источники      │     │   Парсер         │     │   Обогащение    │
│  ┌───────────┐   │     │                   │     │   (DeepSeek V4  │
│  │ Brave Search│  │     │ search_competitors│    │    Flash)        │
│  ├───────────┤   │     │       .py         │     │                  │
│  │ VK API     │  │ ──→ │                   │ ──→ │ analyze_        │
│  ├───────────┤   │     │ 9 источников      │     │ competitors.py  │
│  │ Apify      │  │     │ (Brave, VK, Apify, │    │                  │
│  │ (Inst/TT)  │  │     │  YouTube по Brave)│     │ 9 полей через AI │
│  ├───────────┤   │     └────────┬──────────┘     └────────┬───────┘
│  │ YouTube*  │  │              │                          │
│  └───────────┘   │              ▼                          ▼
└─────────────────┘     ┌───────────────────────────────────────┐
            │            │         Google Sheets (A:R)            │
            │            │  555+ конкурентов с enrich-полями     │
            │            └───────────────────────────────────────┘
            │
         *YouTube Data API заблокирован на VPS → обход через Brave Search
```

## Быстрый старт

```bash
# 1. Клонировать
git clone git@github.com:DEVASTATOR1349/Conc_Parser.git
cd Conc_Parser

# 2. Настроить .env
cp .env.example .env
# Заполнить: OPENROUTER_API_KEY, BRAVE_API_KEY, VK_API_KEY,
# APIFY_API_TOKEN, GOOGLE_APPLICATION_CREDENTIALS

# 3. Поднять контейнер (API на порту 18888)
docker compose up -d

# 4. Ручной парсинг
python3 search_competitors.py --client kristina_kuznetsova

# 5. Обогащение для Кристины
python3 run_enrich_kristina.py

# 6. Обогащение для НОМОС
SHEET_ID="1zVNwBX7e8FIZ-0bP7qU2UTbueXrukoev0NbSCS9EwHQ" \
  python3 analyze_competitors.py
```

## Клиенты

| Клиент | Таблица | Статус |
|--------|---------|--------|
| **НОМОС КЛИНИК** | `1zVNwBX7e8FIZ-0bP7qU2UTbueXrukoev0NbSCS9EwHQ` | ✅ 196 конкурентов |
| **Кристина Кузнецова** | `1hIsSBIP0f7jXAFQZGhAj_0locMKUdb9JKmWM4kjfSLQ` | ✅ 555 конкурентов |

## Сбор данных (search_competitors.py)

Собирает конкурентов из **9+ источников** с фильтрацией по подписчикам (tier):

- **Tier 1** (≥100K) — топ-конкуренты
- **Tier 2** (≥50K) — средние
- **Tier 3** (≥10K) — нишевые

### Источники
| Источник | Скорость | Данные |
|----------|----------|--------|
| **Brave Search** | ⚡ Быстро | Сайты, YouTube-каналы |
| **VK API** | ⚡ Быстро | Сообщества, группы |
| **Apify (Instagram)** | 🐢 Медленно | Instagram-профили |
| **Apify (TikTok)** | 🐢 Медленно | TikTok-профили |
| **YouTube** (через Brave) | ⚡ | Каналы |

## Обогащение (analyze_competitors.py)

AI-анализ через **DeepSeek V4 Flash** (основная) → **Gemini 2.5 Flash** (запасная) через OpenRouter.

### Какие поля заполняет (9 полей):
| Колонка | Поле | Описание |
|---------|------|----------|
| **E** | Позиционирование / УТП | УТП и позиционирование конкурента |
| **F** | Услуги / специализация | Перечень услуг |
| **G** | Ценовой сегмент | С примерными ценами |
| **I** | Слабые стороны / точки роста | 3 слабые стороны |
| **J** | ToV и стиль контента | Тон общения |
| **K** | ЦА (основной сегмент) | Пол, возраст, боли |
| **L** | Активность / частота | Частота постинга |
| **O** | Что можно позаимствовать | 3-5 конкретных идей |
| **Q** | Валидация | YES/NO с обоснованием |

## Файлы

| Файл | Назначение |
|------|------------|
| `analyze_competitors.py` | **Основной скрипт обогащения** (DeepSeek V4 Flash) |
| `search_competitors.py` | **Парсер конкурентов** (все источники) |
| `run_enrich_kristina.py` | **Обёртка обогащения Кристины** (хардкод ID) |
| `kristina_final_fill.py` | **Добор конкурентов** VK + Brave до 555 |
| `src/sheets.py` | Работа с Google Sheets |
| `src/fact_check.py` | Факт-чекинг конкурентов |
| `src/validate_batch.py` | Батч-валидация |
| `clients/*.md` | Конфиги запросов по клиенту |
| `api_server.py` | HTTP API (Flask) на порту 8888 |
| `docker-compose.yml` | Docker-сборка контейнера |

## API (порт 18888)

```bash
# Health check
curl http://localhost:18888/health

# Список клиентов
curl http://localhost:18888/api/clients

# Запустить парсинг для клиента
curl -X POST http://localhost:18888/api/parse -d '{"client":"kristina_kuznetsova"}'

# Запустить обогащение
curl -X POST http://localhost:18888/api/enrich -d '{"sheet_id":"1hIs..."}'
```

## Кроны

Контейнер `mark1-parser` запущен и обслуживает API-запросы.
Для автоматического парсинга добавить в crontab:

```cron
# Ежедневно в 6:00 — парсить всех клиентов
0 6 * * * cd /root/mark1 && docker exec mark1-parser python3 /app/search_competitors.py --all
```

---

## Чейнджлог

### v4.1 (17.06.2026)
- ✅ DeepSeek V4 Flash — основная модель обогащения
- ✅ Кристина: 555 конкурентов (417 сайтов, 120 VK, 11 Instagram, 7 TikTok)
- ✅ НОМОС: 196 чистых строк (восстановлен из бекапа)
- ✅ Заполняет все 9 полей: УТП, услуги, цена, слабые, ToV, ЦА, активность, заимствования, валидация
- ✅ Контейнер mark1-parser пересобран и запущен
- ✅ Защита от записи не в свою таблицу (хардкод ID в run_enrich)

### v4.0 (раньше)
- НОМОС очищен от мусора через AI-верификацию
- Базовая архитектура парсинга
