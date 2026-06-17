#!/usr/bin/env python3
"""
kristina_cleanup.py — жёсткая чистка таблицы Кристины.
Удаляет: пустые строки, магазины, статьи, не-бизнес.
"""

import os, sys, time
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

SHOP_KW = ["интернет-магазин","купить","доставк","заказать","товар","корзина",
           "бады и витамины","каталог бад","витамины недорого","продукция эвалар",
           "витамакс","магазин","musclebuilding магазин","купить омега","купить now"]
ARTICLE_KW = ["топ-","топ10","топ 10","20+","10 лучш","рейтинг","как выбрать","подборк",
              "обзор лучш","фактов, котор","гид по"]
BAD_STUFF = ["фильм ужас","ужасов","лепра","школа строительств","ремонт квартир",
             "декор","декорирован","собака","шпиц","кошка","кот","щенок","обои",
             "timerman","климат-100","prodoctorov","napopravku","автоматизация",
             "умная теплица","зож медицина"]

def is_garbage(name, cat, link, desc):
    n = name.lower().strip()
    d = (desc or "").lower().strip()
    if not n: return True, "пустая"
    for kw in BAD_STUFF:
        if kw in n or kw in d: return True, f"мусор ({kw})"
    for kw in SHOP_KW:
        if kw in n or kw in d: return True, f"магазин ({kw})"
    for kw in ARTICLE_KW:
        if kw in n or kw in d: return True, f"статья ({kw})"
    lk = link.lower()
    if "prodoctorov.ru" in lk: return True, "prodoctorov"
    if "napopravku.ru" in lk: return True, "napopravku"
    if "vk.com/" in lk:
        # VK-группы про фильмы, стройку и тд
        import re
        desc_vk = d or n
        for kw in ["фильм","ужас","лепра","стройк","ремонт","декор","собак","шпиц","кошк","кот"]:
            if kw in desc_vk: return True, f"VK-мусор ({kw})"
    return False, ""

def main():
    svc = get_service()
    if not svc: print("❌"); return
    r = svc.spreadsheets().values().get(spreadsheetId=SID, range=f"{TAB}!A:R").execute()
    vals = r.get("values", [])
    print(f"До чистки: {len(vals)-1} строк")
    header = vals[0]
    rows = vals[1:]
    kept = [header]
    removed = []
    for i, row in enumerate(rows, 2):
        row = row + [""] * max(0, 18 - len(row))
        v, rsn = is_garbage(row[0], row[1], row[2], row[17])
        if v:
            removed.append((i, (row[0] or "?")[:40], rsn))
        else:
            kept.append(row)
    svc.spreadsheets().values().clear(spreadsheetId=SID, range=f"{TAB}!A:R").execute()
    time.sleep(1)
    svc.spreadsheets().values().update(
        spreadsheetId=SID, range=f"{TAB}!A1:R{len(kept)}",
        valueInputOption="RAW", body={"values": kept}).execute()
    print(f"После: {len(kept)-1} строк, удалено: {len(removed)}")
    for i, name, rsn in removed:
        print(f"  R{i:3d}: [{name:45s}] → {rsn}")

    # Итоги категорий
    from collections import Counter
    cats = Counter()
    for row in kept[1:]:
        c = row[1].strip() if len(row)>1 else "?"
        if "—" in c: pref = c.split("—")[0].strip()
        elif c: pref = c
        else: pref = "?"
        cats[pref] += 1
    print(f"\nКатегории после:")
    for cat, cnt in cats.most_common():
        print(f"  {cat}: {cnt}")

if __name__ == "__main__":
    main()
