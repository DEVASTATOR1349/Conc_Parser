#!/usr/bin/env python3
"""
kristina_final_fill.py — ФИНАЛЬНЫЙ добор конкурентов Кристины.
Читает 330 существующих строк, добавляет через VK + Brave (новые запросы).
Цель: 500-600 строк.

ВАЖНО: ID таблицы хардкодом, НЕ из .env!
"""

import os, sys, time, json, requests
from pathlib import Path
from urllib.parse import urlparse
from collections import Counter

os.environ["PYTHONUNBUFFERED"] = "1"

for env_path in [Path(__file__).parent / ".env"]:
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"): continue
                if "=" not in line: continue
                k, v = line.split("=", 1); os.environ[k.strip()] = v.strip()

sys.path.insert(0, str(Path(__file__).parent / "src"))

# ── ХАРДКОД — НЕ ИЗ .ENV ──
SID = "1hIsSBIP0f7jXAFQZGhAj_0locMKUdb9JKmWM4kjfSLQ"
TAB = "Отчёт по конкурентам"
MAX_TARGET = 600

BRAVE = os.getenv("BRAVE_API_KEY", "")
VK = os.getenv("VK_API_KEY", "")

HEADERS = ["Конкурент (название)","Категория","Ссылки (сайт/соцсети)","Подписчики (всего)",
           "Позиционирование / УТП","Услуги / специализация","Ценовой сегмент",
           "Сильные стороны","Слабые стороны / точки роста","ToV и стиль контента",
           "ЦА (основной сегмент)","Активность / частота","Контент-форматы",
           "Уровень угрозы (1-10)","Что можно позаимствовать","Общая оценка / выводы",
           "Валидация","Описание"]

EXCLUDE = ["песня","музыка","игра","фильм","кино","сериал","юмор","прикол",
           "рецепт","кулинария","путешествия","спорт","футбол","хайп","ремонт",
           "стройка","дизайн","еда","авто","машина","животные","собака","кошка",
           "shopping","новости","новост","лайфстайл","мотивация","психология",
           "обои","скачать","бесплатно","гороскоп","макияж","прическа","одежда","мода"]

def relevant(text):
    t = text.lower()
    return all(kw not in t for kw in EXCLUDE)

def read_existing():
    """Читаем строки из таблицы, возвращаем (все строки как dict, сеты для dedup)."""
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    
    creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not creds_path or not os.path.exists(creds_path):
        print("  [ERR] Нет credentials")
        return [], set(), set()
    
    creds = service_account.Credentials.from_service_account_file(
        creds_path, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    svc = build("sheets", "v4", credentials=creds)
    
    result = svc.spreadsheets().values().get(
        spreadsheetId=SID, range=f"{TAB}!A:R").execute()
    values = result.get("values", [])
    
    existing = []
    ex_links = set()
    ex_names = set()
    
    for row in values[1:]:
        name = row[0].strip().lower() if len(row) > 0 else ""
        link = row[2].strip().lower().rstrip("/") if len(row) > 2 else ""
        if name: ex_names.add(name)
        if link: ex_links.add(link)
    
    return values, ex_links, ex_names

def save_all(svc, rows):
    """Полная перезапись."""
    svc.spreadsheets().values().update(
        spreadsheetId=SID, range=f"{TAB}!A1:R{len(rows)}",
        valueInputOption="RAW", body={"values": rows}).execute()
    print(f"  💾 Сохранено: {len(rows)} строк", flush=True)

# ═══ VK — новые широкие запросы ═══
def collect_vk_new(ex_links, ex_names):
    results = []
    queries = [
        "правильное питание","зож москва","фитнес здоровье",
        "красота и здоровье","нутрициология москва","здоровье москва",
        "спортивное питание","диетология","здоровый образ жизни москва",
        "медицина москва","эндокринология москва",
        "похудение","здоровое питание","женское здоровье",
        "витамины и добавки","биохакинг",
    ]
    
    for q in queries:
        try:
            r = requests.get("https://api.vk.com/method/newsfeed.search",
                params={"q":q,"count":30,"extended":1,"access_token":VK,"v":"5.199"}, timeout=8)
            if r.status_code != 200: continue
            data = r.json()
            if "error" in data: continue
            groups = data.get("response",{}).get("groups",[])
            gids = [str(g["id"]) for g in groups if g.get("id")]
            details = {}
            if gids:
                try:
                    dr = requests.get("https://api.vk.com/method/groups.getById",
                        params={"group_ids":",".join(gids),
                                "fields":"description,members_count,activity,verified,site,counters",
                                "access_token":VK,"v":"5.199"}, timeout=8)
                    if dr.status_code==200:
                        resp = dr.json().get("response",{})
                        dl = resp.get("groups",[]) if isinstance(resp,dict) else []
                        for dg in dl: details[str(dg.get("id"))] = dg
                except: pass
            n = 0
            for g in groups:
                name = g.get("name","").strip()
                if not name: continue
                screen = g.get("screen_name","")
                gid = str(g.get("id",""))
                url = f"https://vk.com/{screen}" if screen else f"https://vk.com/club{g['id']}"
                d = details.get(gid,{})
                members = int(d.get("members_count",0) or (d.get("counters",{}) or {}).get("members",0) or 0)
                if members < 10000: continue  # ≥10K
                if name.lower() in ex_names: continue
                if url.lower().rstrip("/") in ex_links: continue
                if not relevant(name): continue
                ex_links.add(url.lower().rstrip("/"))
                ex_names.add(name.lower())
                n += 1
                results.append([
                    name, "VK — сообщество", url, members,
                    "—","—","—",f"VK-сообщество, {members} подп.",
                    "—","—","—","—","VK (посты, видео, клипы)",
                    min(10, members//30000+1),"—",
                    f"VK: {name}, {members} подп.","—",f"VK-сообщество «{name}»",
                ])
            if n: print(f"    VK +{n} [{q[:25]}]", flush=True)
            time.sleep(0.3)
        except: pass
    return results

# ═══ Brave — новые запросы ═══
def collect_brave_new(ex_links, ex_names):
    results = []
    queries = [
        "здоровое питание блог россия","медицинский блог россия",
        "женское здоровье блог","нутрициология блог россия",
        "фитнес блоггер здоровье","эндокринная система",
        "гормональный фон","диетология онлайн",
        "врач эндокринолог онлайн","женское здоровье после 30",
        "здоровье и красота москва","медицинские услуги москва",
        "нутрициолог обучение","здоровое питание консультации",
        "биохакинг москва","антивозрастная медицина москва",
        "интегративная медицина москва","теледоктор эндокринолог",
        "витамины и добавки москва","гормональное здоровье женщины",
        "медицинский центр красоты","превентивная медицина врач",
        "спортивная нутрициология","эндокринолог онлайн консультация",
    ]
    
    for q in queries:
        try:
            r = requests.get("https://api.search.brave.com/res/v1/web/search",
                params={"q":q,"count":10,"country":"RU"},
                headers={"Accept":"application/json","Accept-Encoding":"gzip",
                         "X-Subscription-Token": BRAVE}, timeout=8)
            if r.status_code != 200: continue
            data = r.json()
            n = 0
            for item in data.get("web",{}).get("results",[]):
                title = (item.get("title","") or "").strip()
                desc = (item.get("description") or "").strip()
                link = (item.get("url","") or "").strip()
                if not title: continue
                if not relevant(title+" "+desc): continue
                if title.lower() in ex_names: continue
                if link.lower().rstrip("/") in ex_links: continue
                ex_links.add(link.lower().rstrip("/"))
                ex_names.add(title.lower())
                n += 1
                is_yt = "youtube.com/channel" in link.lower() or "youtube.com/@" in link.lower()
                cat = "YouTube — канал" if is_yt else "Сайт — прямой конкурент"
                results.append([
                    title[:120], cat, link[:300], 0, desc[:300] or "—",
                    "—","—",f"YouTube: {title[:50]}" if is_yt else f"Сайт: {urlparse(link).netloc}",
                    "—","—","—","—","YouTube" if is_yt else "Сайт",
                    7 if is_yt else 5,"—",title[:200],"—",desc[:300] or title[:200],
                ])
            if n: print(f"    Brave +{n} [{q[:25]}]", flush=True)
            time.sleep(0.3)
        except: pass
    return results


def main():
    print("="*60, flush=True)
    print(f"  КРИСТИНА — финальный добор", flush=True)
    print(f"  {time.strftime('%Y-%m-%d %H:%M')}", flush=True)
    print(f"  Brave: {'✅' if BRAVE else '❌'}", flush=True)
    print(f"  VK:    {'✅' if VK else '❌'}", flush=True)
    print(f"  Целевой ID: {SID}", flush=True)
    print("="*60, flush=True)
    
    # Читаем
    existing_rows, ex_links, ex_names = read_existing()
    total = len(existing_rows) - 1  # минус заголовок
    
    if not existing_rows:
        print("❌ Не удалось прочитать таблицу", flush=True)
        return
    
    print(f"📊 В таблице: {total} строк (+1 заголовок)", flush=True)
    
    # Превращаем существующие строки в список списков
    all_rows = [existing_rows[0]]  # заголовок
    for row in existing_rows[1:]:
        all_rows.append(row)
    
    # ── VK добор ──
    if total < MAX_TARGET:
        print(f"\n🔍 VK (новые запросы)...", flush=True)
        new_vk = collect_vk_new(ex_links, ex_names)
        for row in new_vk:
            all_rows.append(row)
        total = len(all_rows) - 1
        print(f"  VK: +{len(new_vk)} (всего {total})", flush=True)
        
    # ── Brave добор ──
    if total < MAX_TARGET:
        print(f"\n🔍 Brave (новые запросы)...", flush=True)
        new_brave = collect_brave_new(ex_links, ex_names)
        for row in new_brave:
            all_rows.append(row)
        total = len(all_rows) - 1
        print(f"  Brave: +{len(new_brave)} (всего {total})", flush=True)
    
    # ── Сохранение ──
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    
    creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if creds_path and os.path.exists(creds_path):
        creds = service_account.Credentials.from_service_account_file(
            creds_path, scopes=["https://www.googleapis.com/auth/spreadsheets"])
        svc = build("sheets", "v4", credentials=creds)
        
        save_all(svc, all_rows)
    
    # ── Итоги ──
    print(f"\n{'='*60}", flush=True)
    print(f"  🎉 ИТОГО: {total} строк", flush=True)
    print(f"{'='*60}", flush=True)
    
    cats = Counter()
    for row in all_rows[1:]:
        c = row[1] if len(row) > 1 else "?"
        pref = c.split("—")[0].strip() if "—" in c else c.split(" ")[0] if c else "?"
        cats[pref] += 1
    print(f"\n  По категориям:", flush=True)
    for cat, cnt in cats.most_common():
        print(f"    {cat}: {cnt}", flush=True)
       
    
if __name__ == "__main__":
    main()
