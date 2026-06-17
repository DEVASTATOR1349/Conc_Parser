#!/usr/bin/env python3
"""
analyze_competitors.py — AI-обогащение конкурентов через OpenRouter.
Заполняет пустые поля: УТП, услуги, цена, слабые стороны, ToV, ЦА,
активность, что заимствовать, валидация.

Модели: deepseek/deepseek-v4-flash (основная), google/gemini-2.5-flash (запасная).

Запуск:
  export OPENROUTER_API_KEY=...
  export SHEET_ID=...
  python3 analyze_competitors.py

Или через обёртку run_enrich_kristina.py — она сама загрузит .env.
"""

import json, os, re, sys, time, traceback
from datetime import datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from sheets import get_service, update_cells

# ─── Конфиг ───
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
SHEET_ID = os.environ.get("SHEET_ID", "")
SHEET_TAB = os.environ.get("SHEET_TAB", "Отчёт по конкурентам")

MODEL_PRIMARY = "deepseek/deepseek-v4-flash"
MODEL_FALLBACK = "google/gemini-2.5-flash"
API_URL = "https://openrouter.ai/api/v1/chat/completions"

# Поля для заполнения (все 9 которые просил Олег)
FIELDS_TO_FILL = {
    "positioning":  "E",  # УТП
    "services":     "F",  # Услуги
    "price":        "G",  # Ценовой сегмент
    "weaknesses":   "I",  # Слабые стороны
    "tov":          "J",  # ToV
    "audience":     "K",  # ЦА
    "activity":     "L",  # Активность
    "borrow":       "O",  # Что позаимствовать
    "validation":   "Q",  # Валидация
}

# Поля, которые не заполняются через AI (заполнены из парсинга)
# D=подписчики, H=сильные(заполнены), N=угроза(заполнена), C=ссылки, A=название, B=категория, R=описание, M=форматы(заполнены), P=выводы


def _ai_req(payload, retries=2):
    """OpenRouter с fallback моделью."""
    for attempt in range(retries):
        model = payload.get("model", MODEL_PRIMARY)
        if attempt > 0:
            model = MODEL_FALLBACK
            payload["model"] = model

        headers = {
            "Authorization": f"Bearer {OPENROUTER_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/DEVASTATOR1349/mark1",
        }
        try:
            resp = requests.post(API_URL, json=payload, headers=headers, timeout=90)
            if resp.status_code == 200:
                return resp
            if resp.status_code in (400, 429, 503):
                msg = resp.json().get("error", {}).get("message", "")
                print(f" [AI:{resp.status_code}] {model}: {msg[:40]}", end=" → ", flush=True)
                continue
            print(f" [AI:{resp.status_code}] {model}", end=" → ", flush=True)
            return None
        except requests.Timeout:
            print(f" [AI] timeout {model}", end=" → ", flush=True)
            continue
        except Exception as e:
            print(f" [AI] err {model}: {e}", end=" → ", flush=True)
            continue
    return None


def _empty(val):
    v = str(val).strip()
    return not v or v in ("—", "-", "0", "", "YES", "NO")


def get_unanalyzed():
    """Все строки с пустыми полями для обогащения."""
    svc = get_service()
    if not svc:
        print("  ❌ Нет credentials (get_service вернул None)")
        return []

    try:
        result = svc.spreadsheets().values().get(
            spreadsheetId=SHEET_ID, range=f"{SHEET_TAB}!A:R"
        ).execute()
        values = result.get("values", [])

        rows = []
        for i, row in enumerate(values[1:], start=2):
            row = row + [""] * max(0, 18 - len(row))

            # Определяем какие поля пустые
            empty_fields = []
            for fname, fcol in FIELDS_TO_FILL.items():
                col_idx = ord(fcol) - ord("A")
                val = row[col_idx] if col_idx < len(row) else ""
                if _empty(val):
                    empty_fields.append(fname)

            if not empty_fields:
                continue  # всё заполнено

            rows.append({
                "row_index": i,
                "name":        row[0].strip() if len(row) > 0 else "",
                "category":    row[1].strip() if len(row) > 1 else "",
                "links":       row[2].strip() if len(row) > 2 else "",
                "subscribers": row[3].strip() if len(row) > 3 else "",
                "positioning": row[4].strip() if len(row) > 4 else "",
                "services":    row[5].strip() if len(row) > 5 else "",
                "price":       row[6].strip() if len(row) > 6 else "",
                "strengths":   row[7].strip() if len(row) > 7 else "",
                "formats":     row[12].strip() if len(row) > 12 else "",
                "threat":      row[13].strip() if len(row) > 13 else "",
                "sources":     row[17].strip() if len(row) > 17 else "",
                "empty_fields": empty_fields,
            })

        return rows
    except Exception as e:
        print(f"  [get_unanalyzed] Ошибка: {e}")
        return []


def ai_analyze(row):
    """Прогнать конкурента через LLM — заполнить пустые поля."""
    name = row["name"]
    source = row["sources"] or name
    links = row["links"] or "—"
    subs = row["subscribers"] or "?"
    category = row["category"] or "?"
    existing_pos = row.get("positioning", "")
    existing_serv = row.get("services", "")
    existing_price = row.get("price", "")

    # Определяем какие поля нужно заполнить
    need = row["empty_fields"]
    need_str = ", ".join(need)

    prompt = f"""Ты — аналитик конкурентов в сфере здоровья, красоты, эстетической медицины и нутрициологии.
Твоя задача — проанализировать конкурента и заполнить ТОЛЬКО те поля, которые пустые.

ДАННЫЕ О КОНКУРЕНТЕ:
- Название: {name}
- Категория: {category}
- Ссылки: {links}
- Подписчики: {subs}
- Описание источника: {source}
- Есть поля: УТП={'✅' if existing_pos else '❌'} | Услуги={'✅' if existing_serv else '❌'} | Цена={'✅' if existing_price else '❌'}

НУЖНО ЗАПОЛНИТЬ ({need_str}):

Заполни ТОЛЬКО пустые поля. Для каждого поля — 1-2 предложения.
Оценивай по названию и категории. Если это VK-паблик с рецептами — не выдумывай услуги, оцени как инфоресурс.

Формат JSON:
{{{{
  "services_specialization": "..." | "",      # услуги/специализация
  "positioning_utp": "..." | "",              # УТП и позиционирование
  "price_segment": "..." | "",                # ценовой сегмент с примерными ценами или "неприменимо (инфоресурс)"
  "weaknesses": ["...", "...", "..."] | [],   # 3 слабые стороны
  "tov_style": "..." | "",                    # ToV: экспертный/ламповый/агрессивный/молодёжный и т.п.
  "target_audience": "..." | "",              # ЦА: пол, возраст, боли
  "activity_frequency": "..." | "",           # частота публикаций
  "borrow": "..." | "",                       # что можно позаимствовать (3-5 идей)
  "validation": "..." | "",                   # валидация: "YES — ..." или "NO — ..." с обоснованием
}}}}

Верни ТОЛЬКО JSON без markdown, без объяснений.
Если данных недостаточно — пиши "—" (длинное тире)."""

    payload = {
        "model": MODEL_PRIMARY,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.5,
        "max_tokens": 1500,
    }

    try:
        resp = _ai_req(payload)
        if resp is None:
            return None

        content = resp.json()["choices"][0]["message"]["content"]
        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', content)
        if json_match:
            content = json_match.group(1)
        return json.loads(content.strip())
    except Exception as e:
        print(f" [AI] парсинг ответа: {e}", flush=True)
        return None


def update_row(svc, result, row_index):
    """Записать только непустые поля результата."""
    if not result:
        return False

    updates = {}
    F = FIELDS_TO_FILL  # { fname -> col }
    mapping = {
        "services_specialization": ("F", "services"),
        "positioning_utp":         ("E", "positioning"),
        "price_segment":           ("G", "price"),
        "weaknesses":              ("I", "weaknesses"),
        "tov_style":               ("J", "tov"),
        "target_audience":         ("K", "audience"),
        "activity_frequency":      ("L", "activity"),
        "borrow":                  ("O", "borrow"),
        "validation":              ("Q", "validation"),
    }

    for res_key, (col, fname) in mapping.items():
        val = result.get(res_key)
        if not val:
            continue
        val_str = str(val).strip()
        if not val_str or val_str in ("[]", ""):
            continue
        if isinstance(val, list):
            val_str = "\n".join(str(x) for x in val if x)

        updates[col] = val_str

    if not updates:
        return True  # ничего не изменилось — это норм

    ok = update_cells(SHEET_ID, SHEET_TAB, row_index, updates)
    return ok


def main():
    print("=" * 60)
    print(f"  AI-ОБОГАЩЕНИЕ КОНКУРЕНТОВ")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  Модель: {MODEL_PRIMARY} → {MODEL_FALLBACK}")
    print("=" * 60)

    if not OPENROUTER_KEY:
        print("\n  ❌ Нет OPENROUTER_API_KEY")
        return

    if not SHEET_ID:
        print("\n  ❌ Нет SHEET_ID (передай через env)")
        return

    print(f"\n  Таблица: {SHEET_ID}")
    print(f"  Вкладка: {SHEET_TAB}", flush=True)

    rows = get_unanalyzed()
    if not rows:
        print("\n  ✅ Все строки уже обогащены!")
        return

    print(f"  Строк для обогащения: {len(rows)}")
    print(f"  Пустые поля в каждой: {len(rows[0]['empty_fields'])}...\n", flush=True)

    svc = get_service()
    if not svc:
        print("  ❌ Не удалось получить Google Sheets сервис")
        return

    success = 0
    fail = 0
    skipped = 0
    start_time = time.time()
    total = len(rows)

    for idx, row in enumerate(rows, 1):
        elapsed = time.time() - start_time
        rate = idx / elapsed if elapsed > 0 else 0
        eta = (total - idx) / rate if rate > 0 else 0

        name = row["name"][:35]
        empty = row["empty_fields"]
        print(f"  [{idx}/{total}] {name:35s} пусто:{len(empty)}",
              end="", flush=True)

        result = ai_analyze(row)

        if result:
            ok = update_row(svc, result, row["row_index"])
            if ok:
                success += 1
                fill_count = sum(1 for k in result if result.get(k))
                print(f" ✅ +{fill_count} полей", flush=True)
            else:
                fail += 1
                print(f" ❌ запись", flush=True)
        else:
            # Пробуем ещё раз
            time.sleep(2)
            result = ai_analyze(row)
            if result:
                ok = update_row(svc, result, row["row_index"])
                if ok:
                    success += 1
                    fill_count = sum(1 for k in result if result.get(k))
                    print(f" ✅ (retry) +{fill_count} полей", flush=True)
                else:
                    fail += 1
                    print(f" ❌ (retry)", flush=True)
            else:
                fail += 1
                print(f" ❌ AI недоступен", flush=True)

        # Сброс каждые 25 строк для стабильности
        if idx % 25 == 0:
            elapsed = time.time() - start_time
            remaining = total - idx
            rate = idx / elapsed if elapsed > 0 else 0
            eta_s = remaining / rate if rate > 0 else 0
            print(f"  ─── Прогресс: {idx}/{total}, +{(success/(idx or 1)*100):.0f}% | "
                  f"E:{elapsed/60:.1f}мин / ост:{eta_s/60:.1f}мин", flush=True)
            time.sleep(0.5)

    elapsed = time.time() - start_time
    print(f"\n{'='*60}")
    print(f"  🎉 ЗАВЕРШЕНО!")
    print(f"  ✅ Успешно: {success}/{total}")
    print(f"  ❌ Ошибок: {fail}/{total}")
    print(f"  ⏱ {elapsed/60:.1f} мин ({elapsed/total:.1f} сек/строка)")
    print("=" * 60)


if __name__ == "__main__":
    main()
