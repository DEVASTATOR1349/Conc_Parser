#!/usr/bin/env python3
"""
kristina_cleanup_v2.py — правильная чистка таблицы Кристины.
Удаляет ТОЛЬКО явный мусор, без substring-матчей по описанию.
Проверяет ТОЛЬКО название, категорию, ссылку.
СТРОГИЕ ПРАВИЛА — без ложных срабатываний.
"""

import os, sys, time, re
from pathlib import Path
for p in [Path(__file__).parent / ".env"]:
    if p.exists():
        with open(p) as f:
            for line in f:
                line=line.strip()
                if not line or line.startswith("#"): continue
                if "=" not in line: continue
                k,v=line.split("=",1); os.environ[k.strip()]=v.strip()

sys.path.insert(0, str(Path(__file__).parent / "src"))
from sheets import get_service

SID = "1hIsSBIP0f7jXAFQZGhAj_0locMKUdb9JKmWM4kjfSLQ"
TAB = "Отчёт по конкурентам"

def is_garbage(name, cat, link):
    """True = удалить. Проверяет ТОЛЬКО название и ссылку."""
    n = name.lower().strip()
    lk = link.lower().strip()

    if not n:
        return True, "пустая строка"

    # Пустые ячейки
    if len(n) < 3:
        return True, "слишком короткое название"

    # Интернет-магазины
    SHOP_RX = [
        r"\bкупить\b.*\b(бад|витамин|омега|now)",
        r"\bинтернет-магазин\b",
        r"\bмагазин\b.*\b(витамин|бад|спорт)",
        r"\bкаталог\b.*\b(бад|витамин)",
        r"musclebuilding.*магазин",
    ]
    for rx in SHOP_RX:
        if re.search(rx, n):
            return True, "магазин"

    # Статьи/подборки/рейтинги
    ARTICLE_RX = [
        r"^(топ|рейтинг|10 лучш|20\+|гид)",
        r"\bкак выбрать\b",
        r"\bподборк[аи]\b",
        r"\bобзор\b.*\b(лучш|фитнес|блогер)",
        r"страниц[ау]\s+\d",
        r"стр\..*\d{1,3}",
    ]
    for rx in ARTICLE_RX:
        if re.search(rx, n):
            return True, "статья/подборка"

    # Отзовики — не конкуренты
    if re.search(r"(prodoctorov|napopravku)\.ru", lk):
        return True, "отзовик"
    if re.search(r"отзыв.*(врач|клиник|диетолог)", n) and "клиник" not in n.split("отзыв")[0]:
        return True, "страница отзывов"

    # Явный не-бизнес (целые слова, не substring!)
    NON_BIZ = [
        "фильмы ужасов", "лепра", "ремонт квартир",
        "школа строительства", "декор", "декорирование",
        "собака", "померанский шпиц", "щенок",
        "timerman", "климат-100", "умная теплица",
    ]
    for kw in NON_BIZ:
        if kw in n:
            return True, f"не-бизнес ({kw[:20]})"

    # Рейтинги (начинаются с числа + слово)
    if re.match(r"^\d+\s+(лучш|врач|клиник|нутрициолог|эндокринолог|диетолог|косметолог|специалист|салон|центр)", n):
        # Но не адреса (метро, улица)
        if not re.search(r"(м\.|метро|ул\.|пр\.)", n):
            return True, "рейтинговая подборка"

    # Курсы/обучение (не бизнес Кристины)
    COURSE_RX = [
        r"\bкурс\b.*\b(нутрициолог|диетолог|обучение)",
        r"\bобучение\b.*\b(нутрициолог|диетолог)",
        r"\bдистанционн[ое]?\b.*\b(обучение|курс)",
    ]
    for rx in COURSE_RX:
        if re.search(rx, n):
            return True, "обучение/курс"

    return False, ""

def main():
    svc = get_service()
    if not svc:
        print("❌ Нет сервиса")
        return

    r = svc.spreadsheets().values().get(spreadsheetId=SID, range=f"{TAB}!A:R").execute()
    vals = r.get("values", [])
    print(f"До: {len(vals)-1} строк")

    header = vals[0]
    rows = vals[1:]
    kept = [header]
    removed = []

    for i, row in enumerate(rows, 2):
        row = row + [""] * max(0, 18 - len(row))
        v, rsn = is_garbage(row[0], row[1], row[2])
        if v:
            removed.append((i, row[0][:40], rsn))
        else:
            kept.append(row)

    svc.spreadsheets().values().clear(spreadsheetId=SID, range=f"{TAB}!A:R").execute()
    time.sleep(1)
    svc.spreadsheets().values().update(
        spreadsheetId=SID, range=f"{TAB}!A1:R{len(kept)}",
        valueInputOption="RAW", body={"values": kept}).execute()

    print(f"После: {len(kept)-1} строк, удалено: {len(removed)}")
    for i, name, rsn in removed:
        print(f"  R{i:3d}: [{name:40s}] → {rsn}")

    from collections import Counter
    cats = Counter()
    for row in kept[1:]:
        c = row[1].strip() if len(row) > 1 else "?"
        if "—" in c: pref = c.split("—")[0].strip()
        elif c: pref = c
        else: pref = "?"
        cats[pref] += 1
    print(f"\nКатегории:")
    for cat, cnt in cats.most_common():
        print(f"  {cat}: {cnt}")

if __name__ == "__main__":
    main()
