#!/usr/bin/env python3
"""
Анализатор конкурентов для Горгоны.
Читает новые строки из «Отчёт по конкурентам» (где вывод = пусто или "Нужна аналитика от Горгоны")
и генерирует: ToV, сильные/слабые стороны, угрозу, рекомендации, выводы.

Запуск:
  docker exec gorgona python3 /app/gorgona_analyze.py
  
Через n8n:  HTTP GET → http://172.19.0.2:8888/analyze
(apify-parser запускает HTTP-сервер, который проксирует задачу)
"""

import json, os, sys, re, time, uuid
from datetime import datetime

import requests
sys.path.insert(0, "/app/src")
from sheets import _get_service as get_sheets_service

SHEET_ID = "1zVNwBX7e8FIZ-0bP7qU2UTbueXrukoev0NbSCS9EwHQ"
TAB = "Отчёт по конкурентам"

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")

# Модель для анализа — через OpenRouter (Gemini 2.0 Flash)
MODEL = "google/gemini-2.0-flash-001"
API_URL = "https://openrouter.ai/api/v1/chat/completions"


def get_sheets():
    return get_sheets_service()


def get_unanalyzed_rows(service) -> list[dict]:
    """Читаем строки, где вывод пустой или содержит 'Нужна аналитика'."""
    result = service.spreadsheets().values().get(
        spreadsheetId=SHEET_ID,
        range=f"{TAB}!A:P"
    ).execute()
    values = result.get("values", [])
    if not values:
        return []
    
    headers = values[0]
    rows = []
    for i, row in enumerate(values[1:], start=2):
        # Дополняем пустыми строками до 16
        row = row + [""] * (16 - len(row))
        row_data = dict(zip([
            "name", "category", "links", "subscribers",
            "positioning", "services", "price_segment",
            "strengths", "weaknesses", "tov", "audience",
            "activity", "formats", "threat_level",
            "borrow", "conclusion"
        ], row))
        row_data["row_index"] = i
        
        # Проверяем — требует анализа?
        conclusion = row_data.get("conclusion", "").strip()
        if not conclusion or "Нужна аналитика" in conclusion:
            rows.append(row_data)
    
    return rows


def analyze_with_ai(name: str, links: str, positioning: str, services: str) -> dict:
    """Горгона анализирует конкурента через LLM."""
    
    prompt = f"""Ты — аналитик конкурентов в сфере косметологии и эстетической медицины Москвы.

    Данные о конкуренте:
    - Название: {name}
    - Ссылки: {links}
    - Позиционирование: {positioning}
    - Услуги: {services}

    Заполни следующие поля. Отвечай строго в JSON:

    1. tov_style — какой тон общения у этого конкурента (ламповый/агрессивный/экспертный/молодёжный/академичный/смешанный) и почему так решил (1-2 предложения)
    2. target_audience — описание целевой аудитории (пол, возраст, уровень дохода, интересы)
    3. activity — насколько часто публикует контент субъективно (активно/умеренно/редко)
    4. content_formats — какие форматы использует
    5. threat_level — оценка угрозы от 1 до 10, где 10 = прямой опасный конкурент, который оттягивает клиентов (учитывай: пересечение ЦА, качество контента, активность, подписчиков)
    6. borrow — что конкретно можно позаимствовать из контента этого конкурента (3-5 идей/приёмов)
    7. weaknesses — 3 слабые стороны этого конкурента (на основе ограниченных данных о нём)
    8. strengths_enhanced — допиши сильные стороны, которые видны из позиционирования (1-2 предложения)
    9. conclusion — общий вердикт по конкуренту (3-4 предложения): чем опасен, что умеет, как обходить

    Верни ТОЛЬКО JSON без пояснений:
    ```json
    {{
      "tov_style": "...",
      "target_audience": "...",
      "activity": "...",
      "content_formats": "...",
      "threat_level": 5,
      "borrow": "...",
      "weaknesses": ["..", "..", ".."],
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
        "HTTP-Referer": "https://gorgona.local",
    }

    try:
        resp = requests.post(API_URL, json=payload, headers=headers, timeout=60)
        if resp.status_code != 200:
            print(f"    ❌ AI API: {resp.status_code}")
            return None
        
        content = resp.json()["choices"][0]["message"]["content"]
        # Извлекаем JSON из возможного markdown-блока
        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', content)
        if json_match:
            content = json_match.group(1)
        content = content.strip()
        
        return json.loads(content)
    except Exception as e:
        print(f"    ❌ AI error: {e}")
        return None


def update_row(service, row_data: dict, ai_result: dict, row_index: int):
    """Обновляем строку в Google Sheets."""
    if not ai_result:
        return False

    # Обновляем колонки (A=1, P=16)
    updates = {
        8: ai_result.get("strengths_enhanced", ""),   # H = strengths
        9: "\n".join(ai_result.get("weaknesses", [])),  # I = weaknesses
        10: ai_result.get("tov_style", ""),             # J = tov
        11: ai_result.get("target_audience", ""),       # K = audience
        12: ai_result.get("activity", ""),              # L = activity
        13: ai_result.get("content_formats", ""),       # M = formats
        14: str(ai_result.get("threat_level", "—")),    # N = threat_level
        15: ai_result.get("borrow", ""),               # O = borrow
        16: ai_result.get("conclusion", ""),            # P = conclusion
    }

    # Формируем batch update
    requests_body = []
    for col, val in updates.items():
        col_letter = chr(64 + col)  # A=1, B=2, ...
        range_str = f"{TAB}!{col_letter}{row_index}"
        
        if val:
            requests_body.append({
                "range": range_str,
                "values": [[val]]
            })

    if not requests_body:
        return False

    try:
        body = {
            "valueInputOption": "USER_ENTERED",
            "data": requests_body
        }
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=SHEET_ID,
            body=body
        ).execute()
        return True
    except Exception as e:
        print(f"    ❌ Update error: {e}")
        return False


def main():
    print("=" * 60)
    print(f"🧠 ГОРГОНА: Анализ конкурентов")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    if not OPENROUTER_API_KEY:
        print("❌ Нет OPENROUTER_API_KEY")
        return

    service = get_sheets()
    if not service:
        return

    print("\n📋 Читаем непроанализированные строки...")
    rows = get_unanalyzed_rows(service)
    print(f"   Найдено: {len(rows)} строк для анализа")

    if not rows:
        print("\n✅ Все конкуренты уже проанализированы!")
        return

    # Ограничим batch, чтобы не спалить квоту
    batch = rows[:20]
    print(f"\n🔬 Анализируем {len(batch)} конкурентов через AI...\n")

    success = 0
    for row in batch:
        name = row.get("name", "?")
        print(f"   [{success+1}/{len(batch)}] {name[:40]}", end="", flush=True)
        
        ai = analyze_with_ai(
            name=name,
            links=row.get("links", ""),
            positioning=row.get("positioning", ""),
            services=row.get("services", ""),
        )
        
        if ai:
            ok = update_row(service, row, ai, row["row_index"])
            if ok:
                success += 1
                tl = ai.get("threat_level", "?")
                print(f" ✅ угроза={tl}")
            else:
                print(f" ❌ не записалось")
        else:
            print(f" ❌ AI отказал")

    print(f"\n🎉 Проанализировано: {success}/{len(batch)}")
    print("=" * 60)


if __name__ == "__main__":
    main()
