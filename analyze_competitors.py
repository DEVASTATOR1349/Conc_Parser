#!/usr/bin/env python3
"""
analyze_competitors.py — AI-анализ конкурентов через OpenRouter (Gemini 2.0 Flash).
Полностью самодостаточный, не зависит от apify-parser.

Читает непроанализированные строки из Google Sheets,
прогоняет через LLM и заполняет: ToV, угрозу, выводы, рекомендации.

Запуск:
  export OPENROUTER_API_KEY=...
  python3 analyze_competitors.py
"""

import json
import os
import re
import sys
import time
from datetime import datetime

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
from sheets import get_service, get_existing, update_cells

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
SHEET_ID = os.getenv("SHEET_ID", "1zVNwBX7e8FIZ-0bP7qU2UTbueXrukoev0NbSCS9EwHQ")
SHEET_TAB = os.getenv("SHEET_TAB", "Отчёт по конкурентам")

MODEL = "google/gemini-2.0-flash-001"
API_URL = "https://openrouter.ai/api/v1/chat/completions"


def get_unanalyzed():
    """Читаем строки, где вывод пустой или 'Нужна аналитика'."""
    service = get_service()
    if not service:
        return []

    try:
        result = service.spreadsheets().values().get(
            spreadsheetId=SHEET_ID,
            range=f"{SHEET_TAB}!A:P"
        ).execute()
        values = result.get("values", [])

        rows = []
        col_map = {
            "name": 0, "category": 1, "links": 2, "subscribers": 3,
            "positioning": 4, "services": 5, "price_segment": 6,
            "strengths": 7, "weaknesses": 8, "tov": 9, "audience": 10,
            "activity": 11, "formats": 12, "threat_level": 13,
            "borrow": 14, "conclusion": 15,
        }

        for i, row in enumerate(values[1:], start=2):
            row = row + [""] * (16 - len(row))
            rd = {"row_index": i}
            for key, col in col_map.items():
                rd[key] = row[col] if col < len(row) else ""
            rd["row_index"] = i

            conclusion = rd.get("conclusion", "").strip()
            if not conclusion or "Нужна аналитика" in conclusion:
                rows.append(rd)

        return rows
    except Exception as e:
        print(f"  [analyze] Ошибка чтения: {e}")
        return []


def ai_analyze(name, links, positioning, services):
    """Прогнать конкурента через LLM."""

    prompt = f"""Ты — аналитик конкурентов в косметологии. Проанализируй данные:

- Название: {name}
- Ссылки: {links}
- Позиционирование: {positioning}
- Услуги: {services}

Заполни JSON:
1. tov_style — тон общения (ламповый/агрессивный/экспертный/молодёжный/академичный) + почему (1-2 предложения)
2. target_audience — пол, возраст, доход, интересы
3. threat_level — угроза 1-10 (10 = прямой опасный конкурент)
4. borrow — 3-5 идей что позаимствовать из контента
5. weaknesses — 3 слабые стороны
6. strengths_enhanced — 1-2 предложения сильных сторон
7. conclusion — общий вердикт (3-4 предложения: чем опасен, что умеет, как обходить)

Верни ТОЛЬКО JSON:
```json
{{
  "tov_style": "...",
  "target_audience": "...",
  "threat_level": 5,
  "borrow": "...",
  "weaknesses": ["...", "...", "..."],
  "strengths_enhanced": "...",
  "conclusion": "..."
}}
```"""

    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
        "max_tokens": 1000,
    }

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/DEVASTATOR1349/Conc_Parser",
    }

    try:
        resp = requests.post(API_URL, json=payload, headers=headers, timeout=60)
        if resp.status_code != 200:
            print(f"  [AI] API: {resp.status_code}")
            return None

        content = resp.json()["choices"][0]["message"]["content"]
        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', content)
        if json_match:
            content = json_match.group(1)
        return json.loads(content.strip())
    except Exception as e:
        print(f"  [AI] error: {e}")
        return None


def update_row(service, ai_result, row_index):
    """Обновить строку в Google Sheets."""
    if not ai_result:
        return False

    # Колонки: H=сильные, I=слабые, J=ToV, K=ЦА, L=активность, M=форматы, N=угроза, O=позаимствовать, P=вывод
    updates = {}
    if ai_result.get("strengths_enhanced"):
        updates["H"] = ai_result["strengths_enhanced"]
    if ai_result.get("weaknesses"):
        updates["I"] = "\n".join(ai_result["weaknesses"])
    if ai_result.get("tov_style"):
        updates["J"] = ai_result["tov_style"]
    if ai_result.get("target_audience"):
        updates["K"] = ai_result["target_audience"]
    if ai_result.get("threat_level"):
        updates["N"] = str(ai_result["threat_level"])
    if ai_result.get("borrow"):
        updates["O"] = ai_result["borrow"]
    if ai_result.get("conclusion"):
        updates["P"] = ai_result["conclusion"]

    return update_cells(SHEET_ID, SHEET_TAB, row_index, updates)


def main():
    print("=" * 60)
    print(f"  AI-АНАЛИЗ КОНКУРЕНТОВ")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    if not OPENROUTER_API_KEY:
        print("\n  ❌ Нет OPENROUTER_API_KEY")
        return

    print("\n1. Чтение непроанализированных строк...")
    rows = get_unanalyzed()
    print(f"   Найдено: {len(rows)}")

    if not rows:
        print("\n  ✅ Все конкуренты уже проанализированы!")
        return

    # Ограничиваем batch
    batch = rows[:20]
    print(f"\n2. Анализ {len(batch)} конкурентов через AI...\n")

    service = None
    try:
        service = get_service()
    except Exception:
        pass

    success = 0
    for i, row in enumerate(batch, 1):
        name = row.get("name", "?")
        print(f"   [{i}/{len(batch)}] {name[:40]}", end="", flush=True)

        ai = ai_analyze(
            name=name,
            links=row.get("links", ""),
            positioning=row.get("positioning", ""),
            services=row.get("services", ""),
        )

        if ai:
            if service:
                ok = update_row(service, ai, row["row_index"])
                if ok:
                    success += 1
                    tl = ai.get("threat_level", "?")
                    print(f" ✅ угроза={tl}")
                else:
                    print(f" ❌ запись не удалась")
            else:
                success += 1
                tl = ai.get("threat_level", "?")
                print(f" ✅ угроза={tl} (dry-run)")
        else:
            print(f" ❌ AI не ответил")

    print(f"\n  Проанализировано: {success}/{len(batch)}")
    print("=" * 60)


if __name__ == "__main__":
    main()
