#!/usr/bin/env python3
"""
analyze_competitors.py — AI-обогащение конкурентов через OpenRouter.
Заполняет пустые поля: УТП, услуги, цена, слабые стороны, ToV, ЦА,
активность, что заимствовать, валидация.

Перед обогащением: AI-префильтр (дешёвый) — мусор пропускается.
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
from collections import Counter

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from sheets import get_service, update_cells

# ─── Конфиг ───
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
FALLBACK_KEY = os.environ.get("OPENROUTER_FALLBACK_KEY", "")
SHEET_ID = os.environ.get("SHEET_ID", "")
SHEET_TAB = os.environ.get("SHEET_TAB", "Отчёт по конкурентам")

MODEL_PRIMARY = "deepseek/deepseek-v4-flash"
MODEL_FALLBACK = "deepseek/deepseek-v4-flash"
API_URL = "https://openrouter.ai/api/v1/chat/completions"

# Поля для заполнения (все 9)
FIELDS_TO_FILL = {
    "positioning":  "E",
    "services":     "F",
    "price":        "G",
    "weaknesses":   "I",
    "tov":          "J",
    "audience":     "K",
    "activity":     "L",
    "borrow":       "O",
    "validation":   "Q",
}

# Стоп-слова на уровне названия (regex, без substring-ловушек)
# Проверяются БЕСПЛАТНО до AI
STOP_WORDS = re.compile(
    r'''
    \bинтернет-магазин\b
    |\bкупить\b.*\b(бад|витамин|now|омега)\b
    |\bмагазин\b.*\b(бад|витамин|спорт)\b
    |\bкаталог\b.*\b(бад|витамин)\b
    |^(топ|\d+\s+(лучш|врач|клиник|нутрициолог|эндокринолог|салон|специалист))
    |\bрейтинг\b
    |\bкак выбрать\b
    |\bподборк[аи]\b
    |\bкурс\b.*\b(нутрициолог|диетолог|обучение)\b
    |\bобучени[ея]\b.*\b(нутрициолог|диетолог)\b
    |prodoctorov\.ru|napopravku\.ru
    |фильмы ужасов|лепра|стройк|декор|собак|шпиц
    |timerman|климат-100
    ''',
    re.VERBOSE | re.IGNORECASE
)


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
                print(f" [AI:{resp.status_code}] {model}: {msg[:30]}", end=" → ", flush=True)
                continue
            print(f" [AI:{resp.status_code}] {model}", end=" → ", flush=True)
            return None
        except requests.Timeout:
            print(f" [AI] timeout {model}", end=" → ", flush=True)
            continue
        except:
            print(f" [AI] err {model}", end=" → ", flush=True)
            continue
    return None


def _empty(val):
    v = str(val).strip()
    return not v or v in ("—", "-", "0", "", "YES", "NO")


def _reject_by_name(name, link):
    """Бесплатная проверка: стоп-слова в названии."""
    if not name:
        return False
    if STOP_WORDS.search(name):
        return True
    if link and STOP_WORDS.search(link):
        return True
    return False


def _ai_prefilter(name, cat, desc):
    """Дешёвый AI-префильтр: YES/NO за 10 токенов.
    YES = живой конкурент (клиника, врач, блогер по здоровью)
    NO = мусор (магазин, статья, курс, фильм, стройка и т.п.)
    """
    prompt = f"""Определи, является ли это бизнесом/проектом в сфере ЗДОРОВЬЯ, НУТРИЦИОЛОГИИ, ДИЕТОЛОГИИ, ЭНДОКРИНОЛОГИИ или ПРЕВЕНТИВНОЙ МЕДИЦИНЫ.
НЕЛЬЗЯ: магазины, статьи, рейтинги, курсы обучения, развлечения, стройка, декор, животные, фильмы.

Название: {name}
Категория: {cat}
Описание: {desc}

Ответ: YES или NO."""

    payload = {
        "model": MODEL_PRIMARY,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": 5,
    }
    try:
        resp = _ai_req(payload)
        if resp is None:
            return "UNKNOWN"
        ans = resp.json()["choices"][0]["message"]["content"].strip().upper()
        if "YES" in ans:
            return "YES"
        if "NO" in ans:
            return "NO"
        return "UNKNOWN"
    except:
        return "UNKNOWN"


def get_unanalyzed():
    """Все строки с пустыми полями для обогащения.
    Без AI-префильтра (слишком долго). Только regex.
    """
    svc = get_service()
    if not svc:
        print("  ❌ Нет credentials")
        return []

    result = svc.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range=f"{SHEET_TAB}!A:R"
    ).execute()
    values = result.get("values", [])

    rows = []
    regex_fail = 0

    for i, row in enumerate(values[1:], start=2):
        row = row + [""] * max(0, 18 - len(row))

        name = row[0].strip() if row[0] else ""
        link = row[2].strip() if row[2] else ""
        cat  = row[1].strip() if row[1] else ""
        desc = row[17].strip() if row[17] else ""

        # ─── Regex-фильтр (бесплатно) ───
        if _reject_by_name(name, link):
            update_cells(SHEET_ID, SHEET_TAB, i, {"Q": "🗑️ FAIL (стоп-слово)"})
            regex_fail += 1
            continue

        # ─── Определяем пустые поля ───
        empty_fields = []
        for fname, fcol in FIELDS_TO_FILL.items():
            col_idx = ord(fcol) - ord("A")
            val = row[col_idx] if col_idx < len(row) else ""
            if _empty(val):
                empty_fields.append(fname)

        if not empty_fields:
            continue

        rows.append({
            "row_index": i,
            "name": name, "category": cat, "links": link,
            "subscribers": row[3].strip() if len(row)>3 else "",
            "positioning": row[4].strip() if len(row)>4 else "",
            "services": row[5].strip() if len(row)>5 else "",
            "price": row[6].strip() if len(row)>6 else "",
            "strengths": row[7].strip() if len(row)>7 else "",
            "formats": row[12].strip() if len(row)>12 else "",
            "threat": row[13].strip() if len(row)>13 else "",
            "sources": desc,
            "empty_fields": empty_fields,
        })

    if regex_fail:
        print(f"  Regex-фильтр: отсеяно {regex_fail} строк (стоп-слова)")
    return rows


def ai_analyze(row):
    """Прогнать конкурента через LLM — заполнить пустые поля.
    Промпт теперь УНИВЕРСАЛЬНЫЙ — не заточен под косметологию НОМОСА."""
    name = row["name"]
    source = row["sources"] or name
    links = row["links"] or "—"
    subs = row["subscribers"] or "?"
    category = row["category"] or "?"
    existing_pos = row.get("positioning", "")
    existing_serv = row.get("services", "")
    existing_price = row.get("price", "")

    need = row["empty_fields"]
    need_str = ", ".join(need)

    prompt = f"""Ты — аналитик рынка здоровья и красоты.
Проанализируй конкурента и заполни пустые поля.

ДАННЫЕ:
- Название: {name}
- Категория: {category}
- Ссылки: {links}
- Подписчики: {subs}
- Описание: {source}

Уже заполнено: УТП={'✅' if existing_pos else '❌'} | Услуги={'✅' if existing_serv else '❌'} | Цена={'✅' if existing_price else '❌'}

Заполни ТОЛЬКО: {need_str}

Формат JSON (верни ТОЛЬКО JSON, без ```, без пояснений):
{{{{
  "services_specialization": "...",
  "positioning_utp": "...",
  "price_segment": "...",
  "weaknesses": ["...","..."],
  "tov_style": "...",
  "target_audience": "...",
  "activity_frequency": "...",
  "borrow": "...",
  "validation": "YES — ..." | "NO — ..."
}}}}

Если инфы нет — ставь "—". Не выдумывай."""

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
        print(f" [AI] парсинг: {e}", flush=True)
        return None


def update_row(svc, result, row_index):
    """Записать результат. Если validation=NO — пишем только Q."""
    if not result:
        return False

    updates = {}
    mapping = {
        "services_specialization": "F",
        "positioning_utp":         "E",
        "price_segment":           "G",
        "weaknesses":              "I",
        "tov_style":               "J",
        "target_audience":         "K",
        "activity_frequency":      "L",
        "borrow":                  "O",
        "validation":              "Q",
    }

    validation = result.get("validation", "")
    is_no = validation.upper().startswith("NO") if validation else False

    for res_key, col in mapping.items():
        val = result.get(res_key)
        if not val:
            continue
        val_str = str(val).strip()
        if not val_str or val_str in ("[]", ""):
            continue
        if isinstance(val, list):
            val_str = "\n".join(str(x) for x in val if x)

        # Если AI сказал NO — пишем ТОЛЬКО Q
        if is_no and col != "Q":
            continue

        updates[col] = val_str

    if not updates:
        return True

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
        print("\n  ❌ Нет SHEET_ID")
        return

    print(f"\n  Таблица: {SHEET_ID}")
    print(f"  Вкладка: {SHEET_TAB}", flush=True)

    rows = get_unanalyzed()
    if not rows:
        print("\n  ✅ Все строки обогащены!")
        return

    print(f"\n  Строк для обогащения: {len(rows)}")
    if rows:
        print(f"  В среднем пустых: {len(rows[0]['empty_fields'])} полей\n", flush=True)

    svc = get_service()
    if not svc:
        print("  ❌ Нет Google Sheets сервиса")
        return

    success = 0
    fail = 0
    start_time = time.time()
    total = len(rows)

    for idx, row in enumerate(rows, 1):
        name = row["name"][:35]
        empty = row["empty_fields"]
        print(f"  [{idx}/{total}] {name:35s} пусто:{len(empty)}", end="", flush=True)

        # Основная попытка
        result = ai_analyze(row)
        if not result:
            time.sleep(2)
            result = ai_analyze(row)  # retry

        if result:
            ok = update_row(svc, result, row["row_index"])
            if ok:
                success += 1
                fill = sum(1 for k in result if result.get(k))
                print(f" ✅ +{fill} полей", flush=True)
            else:
                fail += 1
                print(f" ❌ запись", flush=True)
        else:
            fail += 1
            print(f" ❌ AI недоступен", flush=True)

        if idx % 25 == 0:
            elapsed = time.time() - start_time
            rem = total - idx
            rate = idx / elapsed if elapsed > 0 else 0
            eta_s = rem / rate if rate > 0 else 0
            print(f"  ─── {idx}/{total}, {(success/(idx or 1)*100):.0f}% | "
                  f"{elapsed/60:.1f}мин / ост:{eta_s/60:.1f}мин", flush=True)
            time.sleep(0.5)

    elapsed = time.time() - start_time
    print(f"\n{'='*60}")
    print(f"  ✅ Успешно: {success}/{total}")
    print(f"  ❌ Ошибок: {fail}/{total}")
    print(f"  ⏱ {elapsed/60:.1f} мин ({elapsed/total:.1f} сек/стр)")
    print("=" * 60)


if __name__ == "__main__":
    main()
