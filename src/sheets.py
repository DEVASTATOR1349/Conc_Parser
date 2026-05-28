"""
Работа с Google Sheets (самостоятельно, без привязки к apify-parser).
Использует googleapiclient с сервисным аккаунтом.
"""

import json
import os
from typing import Optional

from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def get_credentials():
    """Получить credentials из GOOGLE_CREDENTIALS_JSON или GOOGLE_APPLICATION_CREDENTIALS."""
    creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
    if creds_json:
        creds_dict = json.loads(creds_json)
        return service_account.Credentials.from_service_account_info(creds_dict, scopes=SCOPES)

    creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if creds_path and os.path.exists(creds_path):
        return service_account.Credentials.from_service_account_file(creds_path, scopes=SCOPES)

    return None


def get_service():
    """Получить Sheets API сервис."""
    creds = get_credentials()
    if not creds:
        print("  [sheets] Нет GOOGLE_CREDENTIALS_JSON или GOOGLE_APPLICATION_CREDENTIALS")
        return None
    service = build("sheets", "v4", credentials=creds)
    return service


def get_existing(spreadsheet_id: str, tab: str) -> tuple[set, set]:
    """Прочитать (ссылки, названия) первой колонки таблицы."""
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
        for row in values[1:]:
            if len(row) > 0:
                names.add(row[0].strip().lower())
            if len(row) > 2:
                import re
                for link in re.split(r'[,;\s]+', row[2].strip()):
                    link = link.strip().rstrip("/")
                    if link:
                        links.add(link.lower())
        return links, names
    except Exception as e:
        print(f"  [sheets] Ошибка чтения: {e}")
        return set(), set()


def write_results(spreadsheet_id: str, tab: str, rows: list[dict]) -> int:
    """Записать строки в таблицу."""
    if not rows:
        return 0

    service = get_service()
    if not service:
        print("  [sheets] Нет доступа — запись невозможна")
        return 0

    fmap = {
        "name": 0, "category": 1, "links": 2, "subscribers": 3,
        "positioning": 4, "services": 5, "price_segment": 6,
        "strengths": 7, "weaknesses": 8, "tov": 9, "audience": 10,
        "activity": 11, "formats": 12, "threat_level": 13,
        "borrow": 14, "conclusion": 15,
    }

    values = []
    for row in rows:
        vals = [""] * 16
        for key, col in fmap.items():
            if key in row:
                vals[col] = str(row[key])
        values.append(vals)

    total = 0
    import time
    for i in range(0, len(values), 10):
        batch = values[i:i + 10]
        try:
            service.spreadsheets().values().append(
                spreadsheetId=spreadsheet_id,
                range=f"{tab}!A:P",
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": batch}
            ).execute()
            total += len(batch)
            print(f"  [sheets] Записано: {len(batch)} (всего {total})")
        except Exception as e:
            print(f"  [sheets] Ошибка записи: {e}")
            break
        time.sleep(0.3)
    return total


def update_cells(spreadsheet_id: str, tab: str, row_index: int, updates: dict) -> bool:
    """Обновить конкретные ячейки строки. updates: {col_letter: value}."""
    service = get_service()
    if not service:
        return False

    data = []
    for col_letter, value in updates.items():
        if value:
            data.append({
                "range": f"{tab}!{col_letter}{row_index}",
                "values": [[str(value)]],
            })

    if not data:
        return False

    try:
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"valueInputOption": "USER_ENTERED", "data": data}
        ).execute()
        return True
    except Exception as e:
        print(f"  [sheets] Ошибка обновления: {e}")
        return False
