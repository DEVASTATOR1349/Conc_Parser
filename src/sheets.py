"""
sheets.py — Работа с Google Sheets (автономно, без apify-parser).

Колонки (A→R):
  A: Конкурент (название)
  B: Категория
  C: Ссылки (сайт/соцсети)
  D: Подписчики (всего)        ← ЧИСЛО, RAW
  E: Позиционирование / УТП
  F: Услуги / специализация
  G: Ценовой сегмент
  H: Сильные стороны
  I: Слабые стороны / точки роста
  J: ToV и стиль контента
  K: ЦА (основной сегмент)
  L: Активность / частота
  M: Контент-форматы
  N: Уровень угрозы (1-10)     ← ЧИСЛО, RAW
  O: Что можно позаимствовать
  P: Общая оценка / выводы
  Q: Валидация
  R: Описание (от LLM)
"""

import json
import os
from typing import Optional

from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Маппинг: ключ → номер колонки (0-based)
COLUMN_MAP = {
    "name": 0,         # A
    "category": 1,     # B
    "links": 2,        # C
    "subscribers": 3,  # D — число
    "positioning": 4,  # E
    "services": 5,     # F
    "price_segment": 6,# G
    "strengths": 7,    # H
    "weaknesses": 8,   # I
    "tov": 9,          # J
    "audience": 10,    # K
    "activity": 11,    # L
    "formats": 12,     # M
    "threat_level": 13,# N — число
    "borrow": 14,      # O
    "conclusion": 15,  # P
    "validation": 16,  # Q
    "description": 17, # R
}
TOTAL_COLS = len(COLUMN_MAP)  # 18

NUMERIC_COLS = {3, 13}  # D (subscribers), N (threat_level)


def get_credentials():
    creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
    if creds_json:
        return service_account.Credentials.from_service_account_info(
            json.loads(creds_json), scopes=SCOPES
        )
    creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if creds_path and os.path.exists(creds_path):
        return service_account.Credentials.from_service_account_file(creds_path, scopes=SCOPES)
    return None


def get_service():
    creds = get_credentials()
    if not creds:
        print("  [sheets] Нет credentials")
        return None
    return build("sheets", "v4", credentials=creds)


def get_existing(spreadsheet_id: str, tab: str) -> tuple[set, set]:
    """Вернуть (множество ссылок, множество названий) из таблицы."""
    service = get_service()
    if not service:
        return set(), set()
    try:
        result = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=f"{tab}!A:C"
        ).execute()
        values = result.get("values", [])
        links, names = set(), set()
        import re
        for row in values[1:]:
            if len(row) > 0:
                names.add(row[0].strip().lower())
            if len(row) > 2:
                for link in re.split(r'[,;\s]+', row[2].strip()):
                    link = link.strip().rstrip("/")
                    if link:
                        links.add(link.lower())
        return links, names
    except Exception as e:
        print(f"  [sheets] Ошибка чтения существующих: {e}")
        return set(), set()


def write_results(spreadsheet_id: str, tab: str, rows: list[dict], dry_run: bool = False) -> int:
    """
    Записать строки в таблицу.
    Числовые поля (subscribers, threat_level) пишутся как числа через RAW.
    """
    if not rows:
        return 0
    if dry_run:
        for row in rows:
            print(f"  [dry] {row.get('name', '?')[:40]}")
        return len(rows)

    service = get_service()
    if not service:
        print("  [sheets] Нет доступа — запись невозможна")
        return 0

    values = []
    for row in rows:
        vals = [""] * TOTAL_COLS
        for key, col in COLUMN_MAP.items():
            val = row.get(key)
            if val is None:
                continue
            if col in NUMERIC_COLS:
                try:
                    vals[col] = int(val)
                except (ValueError, TypeError):
                    vals[col] = val  # fallback
            else:
                vals[col] = str(val) if val else ""
        values.append(vals)

    try:
        service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range=f"{tab}!A:R",
            valueInputOption="RAW",       # ← RAW чтобы числа не оборачивались в апострофы
            insertDataOption="INSERT_ROWS",
            body={"values": values},
        ).execute()
        print(f"  [sheets] Записано: {len(values)} строк")
        return len(values)
    except Exception as e:
        print(f"  [sheets] Ошибка записи: {e}")
        return 0


def ensure_headers(spreadsheet_id: str, tab: str):
    """Убедиться что заголовки в строке 1 корректные. Создаёт лист если его нет."""
    service = get_service()
    if not service:
        return

    HEADERS = [
        "Конкурент (название)", "Категория", "Ссылки (сайт/соцсети)",
        "Подписчики (всего)", "Позиционирование / УТП", "Услуги / специализация",
        "Ценовой сегмент", "Сильные стороны", "Слабые стороны / точки роста",
        "ToV и стиль контента", "ЦА (основной сегмент)", "Активность / частота",
        "Контент-форматы", "Уровень угрозы (1-10)", "Что можно позаимствовать",
        "Общая оценка / выводы", "Валидация", "Описание",
    ]

    try:
        # Сначала пытаемся прочитать — существует ли лист
        service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id, range=f"{tab}!A1:A1"
        ).execute()
    except Exception:
        # Лист не существует — создаём
        try:
            service.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"requests": [{"addSheet": {"properties": {"title": tab}}}]},
            ).execute()
            print(f"  [sheets] Создан новый лист: {tab}")
        except Exception as e:
            print(f"  [sheets] Не удалось создать лист {tab}: {e}")
            return

    try:
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"{tab}!A1:R1",
            valueInputOption="RAW",
            body={"values": [HEADERS]},
        ).execute()
        print(f"  [sheets] Заголовки обновлены в {tab}")
    except Exception as e:
        print(f"  [sheets] Ошибка заголовков: {e}")

def update_cells(spreadsheet_id: str, tab: str, row_index: int, updates: dict) -> bool:
    """Update specific cells in a row (без затирания остальных).
    updates: {"Q": "value", "E": "value"}
    """
    service = get_service()
    if not service or not updates:
        return bool(service)

    col_map = {"A":0,"B":1,"C":2,"D":3,"E":4,"F":5,"G":6,"H":7,"I":8,"J":9,"K":10,"L":11,"M":12,"N":13,"O":14,"P":15,"Q":16,"R":17}

    # Сортируем колонки по позиции и пишем каждую отдельно
    for col_letter, val in updates.items():
        col_l = col_letter.strip().upper()
        idx = col_map.get(col_l)
        if idx is None:
            continue
        try:
            service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=f"{tab}!{col_l}{row_index}",
                valueInputOption="RAW",
                body={"values": [[val]]},
            ).execute()
        except Exception as e:
            print(f"  [sheets] row {row_index} col {col_l}: {e}")
            return False

    return True
