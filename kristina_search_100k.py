#!/usr/bin/env python3
"""
Целевой поиск конкурентов для Кристины Кузнецовой.
Только Instagram + YouTube с фильтром 100 000+ подписчиков.
"""

import os, sys, json, time, re, requests
from datetime import datetime

# ── Конфиг ──
APIFY_TOKEN = os.environ["APIFY_API_TOKEN"]
YT_KEY = os.environ["YOUTUBE_API_KEY"]
SHEET_ID = "1hIsSBIP0f7jXAFQZGhAj_0locMKUdb9JKmWM4kjfSLQ"
SHEET_TAB = "Отчёт по конкурентам"
SERVICE_ACCOUNT = os.environ.get("SERVICE_ACCOUNT_PATH", "/app/service_account.json")
MIN_SUBS = 100_000
MAX_PER_SOURCE = 20  # 10-20 competitors total

# Поисковые запросы
INSTAGRAM_QUERIES = [
    "эндокринолог блогер", "нутрициолог блогер",
    "врач эндокринолог", "БАДы блогер",
    "детский эндокринолог",
    "Зубарева",
    "витамины блогер"
]

YOUTUBE_QUERIES = [
    "эндокринология канал", "нутрициология канал",
    "БАДы обзор добавки", "детское здоровье врач",
    "Зубарева нутрициолог", "Зубарева эндокринолог",
    "Зубарева здоровье", "гормональное здоровье",
    "витамины как выбрать", "щитовидка лечение симптомы"
]

EXCLUDE_KEYWORDS = [
    "песня", "музыка", "игра", "фильм", "кино", "сериал",
    "юмор", "прикол", "рецепт", "кулинария", "путешествия",
    "спорт", "футбол", "хайп", "ремонт", "стройка",
    "дизайн", "еда", "авто", "машина", "животные",
    "собака", "кошка", "lifestyle", "shopping",
    "конкурс красоты", "красоты", "йога", "танец",
    "конкурс", "бесплатные курсы"
]

def is_relevant(name, bio):
    """Проверяет релевантность контенту про эндокринологию/нутрициологию."""
    text = (name + " " + (bio or "")).lower()
    include = ["эндокринолог", "нутрициолог", "БАД", "витамин",
               "гормон", "здоровь", "медицин", "доктор", "врач",
               "зубарев", "добавк", "щитовидк", "женское здоровье",
               "диетолог", "гастроэнтеролог", "педиатр", "спортивное питание"]
    for kw in include:
        if kw.lower() in text:
            return True
    return False

def write_to_sheets(rows, action="overwrite"):
    """Записывает строки в Google Sheets."""
    import gspread
    sa = json.load(open(SERVICE_ACCOUNT))
    gc = gspread.service_account_from_dict(sa)
    sh = gc.open_by_key(SHEET_ID)

    # Ищем или создаём лист
    try:
        ws = sh.worksheet(SHEET_TAB)
    except:
        ws = sh.add_worksheet(title=SHEET_TAB, rows=1000, cols=20)

    headers = [
        "Конкурент (название)", "Категория", "Ссылки (сайт/соцсети)",
        "Подписчики (всего)", "Позиционирование / УТП", "Услуги / специализация",
        "Ценовой сегмент", "Сильные стороны", "Слабые стороны / точки роста",
        "ToV и стиль контента", "ЦА (основной сегмент)", "Активность / частота",
        "Контент-форматы", "Уровень угрозы (1-10)", "Что можно позаимствовать",
        "Общая оценка / выводы", "Валидация", "Описание"
    ]

    # Формируем данные
    data = [headers]
    for r in rows:
        data.append([
            r.get("name", ""),
            r.get("category", ""),
            r.get("links", ""),
            r.get("subscribers", 0),
            r.get("positioning", ""),
            r.get("services", ""),
            r.get("price_segment", ""),
            r.get("strengths", ""),
            r.get("weaknesses", ""),
            r.get("tov", ""),
            r.get("audience", ""),
            r.get("activity", ""),
            r.get("formats", ""),
            r.get("threat_level", 1),
            r.get("borrow", ""),
            r.get("conclusion", ""),
            r.get("validation", ""),
            r.get("description", ""),
        ])

    if action == "overwrite":
        ws.clear()
        ws.update(range_name="A1", values=data)
    else:
        # Append
        existing = ws.get_all_values()
        start_row = len(existing) + 1 if len(existing) > 1 else 2
        ws.update(range_name=f"A{start_row}", values=data[1:])

    return len(data) - 1

def from_instagram():
    """Instagram поиск через Apify."""
    try:
        from apify_client import ApifyClient
    except ImportError:
        print("  [Instagram] apify-client не установлен")
        return []

    client = ApifyClient(token=APIFY_TOKEN)
    found = []
    seen_urls = set()

    for query in INSTAGRAM_QUERIES[:5]:
        print(f"\n  [Instagram] \"{query}\"...", end="", flush=True)
        run = None
        try:
            run = client.actor("apify/instagram-search-scraper").call(
                run_input={
                    "searchType": "user",
                    "search": query,
                    "resultsLimit": 15,
                    "proxy": {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"]},
                },
                wait_duration=45,
            )
        except Exception:
            try:
                run = client.actor("apify/instagram-search-scraper").call(
                    run_input={
                        "searchType": "user",
                        "search": query,
                        "resultsLimit": 15,
                        "proxy": {"useApifyProxy": True},
                    },
                )
            except Exception as e:
                print(f" ❌ {e}")
                time.sleep(1)
                continue
        items = []
        try:
            if isinstance(run, dict):
                ds_id = run.get("defaultDatasetId")
            else:
                ds_id = getattr(run, "default_dataset_id", None)
            if ds_id:
                items = list(client.dataset(ds_id).iterate_items())
        except Exception as e:
            print(f" ❌ dataset: {e}")
            continue

        n = 0
        for item in items:
            username = item.get("username", "")
            full_name = item.get("fullName", "") or username
            followers = item.get("followersCount", 0) or 0
            posts = item.get("postsCount", 0) or 0
            bio = item.get("biography", "") or ""
            category = item.get("categoryName", "") or ""
            is_verified = item.get("isVerified", False)
            is_biz = item.get("isBusinessAccount", False)
            url = f"https://instagram.com/{username}"

            # Фильтры
            if followers < 1000:
                continue
            if url.lower() in seen_urls:
                continue
            if not is_relevant(full_name, bio):
                # Проверка Зубаревой
                if "зубарев" not in (full_name + " " + bio).lower():
                    continue

            seen_urls.add(url.lower())
            n += 1

            threat = min(10, max(1, followers // 10000))
            cat = "Instagram — бизнес" if is_biz else "Instagram — блогер"
            if is_verified:
                cat += " ✓"

            row = {
                "name": full_name,
                "category": cat,
                "links": url,
                "subscribers": followers,
                "positioning": bio[:300] if bio else f"Instagram @{username}",
                "services": category if category else "—",
                "price_segment": "—",
                "strengths": f"Instagram. {followers} подписчиков, {posts} постов" + (f". ✓" if is_verified else ""),
                "weaknesses": "—",
                "tov": bio.split("\n")[0][:150] if bio else "—",
                "audience": f"{category}, массовая аудитория" if followers > 50000 else f"{category}, средняя ниша",
                "activity": f"{posts} постов",
                "formats": "Instagram (посты, stories, reels)" + (f", IGTV" if item.get("igtvVideoCount", 0) > 0 else ""),
                "threat_level": threat,
                "borrow": "—",
                "conclusion": f"@{username} | {followers} подпис." + (f" | {category}" if category else ""),
                "validation": "—",
                "description": bio[:300] if bio else f"Instagram @{username}",
            }
            found.append(row)
            print(f" +{full_name[:30]} ({followers})", end="", flush=True)

            if n >= 10:
                break

        print(f" → +{n}")

    return found

def from_youtube():
    """YouTube поиск через API."""
    found = []
    seen_urls = set()

    for query in YOUTUBE_QUERIES[:7]:
        print(f"\n  [YouTube] \"{query}\"...", end="", flush=True)
        try:
            resp = requests.get(
                "https://youtube.googleapis.com/youtube/v3/search",
                params={"part": "snippet", "q": query, "type": "channel",
                        "maxResults": 25, "relevanceLanguage": "ru", "key": YT_KEY},
                timeout=15,
            )
            if resp.status_code != 200:
                print(f" HTTP {resp.status_code}")
                continue

            n = 0
            for item in resp.json().get("items", []):
                s = item["snippet"]
                title = s.get("title", "").strip()
                cid = item["id"]["channelId"]
                desc = s.get("description", "").strip()
                custom_url = s.get("customUrl", "") or ""
                c_url = f"https://www.youtube.com/channel/{cid}"

                if not is_relevant(title, desc):
                    if "зубарев" not in (title + " " + desc).lower():
                        continue
                if c_url.lower() in seen_urls:
                    continue

                # Stats
                subs = 0
                vids = 0
                views = 0
                try:
                    sr = requests.get(
                        "https://youtube.googleapis.com/youtube/v3/channels",
                        params={"part": "statistics,topicDetails",
                                "id": cid, "key": YT_KEY},
                        timeout=10,
                    )
                    if sr.status_code == 200:
                        si = sr.json().get("items", [])
                        if si:
                            st = si[0].get("statistics", {})
                            subs = int(st.get("subscriberCount", 0))
                            vids = int(st.get("videoCount", 0))
                            views = int(st.get("viewCount", 0))
                except:
                    pass

                if subs < 1000:
                    continue

                seen_urls.add(c_url.lower())
                n += 1
                threat = min(10, max(1, subs // 10000))

                row = {
                    "name": title,
                    "category": "YouTube — канал",
                    "links": c_url,
                    "subscribers": subs,
                    "positioning": desc[:300] if desc else "YouTube-канал",
                    "services": "—",
                    "price_segment": "—",
                    "strengths": f"YouTube. {subs} подписчиков, {vids} видео, {views} просмотров",
                    "weaknesses": "—",
                    "tov": desc.split(".")[0][:150] if desc else "",
                    "audience": "широкая аудитория" if subs > 100000 else "средняя ниша",
                    "activity": f"{vids} видео | {subs} подписчиков",
                    "formats": "YouTube (длинные видео, shorts)",
                    "threat_level": threat,
                    "borrow": "—",
                    "conclusion": f"YouTube: {title} | {subs} подпис. | {vids} видео",
                    "validation": "—",
                    "description": f"{desc[:250]} | YouTube" if desc else f"YouTube-канал {title}",
                }
                found.append(row)
                print(f" +{title[:30]} ({subs})", end="", flush=True)

                if n >= 10:
                    break

            print(f" → +{n}")
            time.sleep(0.3)
        except Exception as e:
            print(f" ❌ {e}")

    return found

def main():
    print("=" * 60)
    print(f"  Кристина Кузнецова — поиск конкурентов (100k+)")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  Источники: instagram, youtube")
    print(f"  MIN_SUBS: {MIN_SUBS}")
    print("=" * 60)

    # Instagram
    ig_rows = from_instagram()
    print(f"\n✅ Instagram: {len(ig_rows)} профилей")

    # YouTube
    yt_rows = from_youtube()
    print(f"\n✅ YouTube: {len(yt_rows)} каналов")

    # Сортировка по подписчикам (убывание)
    all_rows = sorted(ig_rows + yt_rows, key=lambda r: r["subscribers"], reverse=True)

    print(f"\n{'='*60}")
    print(f"  ИТОГО: {len(all_rows)} найденных конкурентов")

    # Фильтр 100k+
    filtered = [r for r in all_rows if r["subscribers"] >= MIN_SUBS]
    print(f"  Из них с 100k+: {len(filtered)}")
    print(f"{'='*60}")

    for r in filtered:
        print(f"  • {r['name'][:40]:40s} | {r['subscribers']:>8} | {r['links'][:40]}")
    if len(all_rows) > len(filtered):
        print(f"\n  🔽 До 100k ({len(all_rows) - len(filtered)} профилей):")
        for r in all_rows:
            if r["subscribers"] < MIN_SUBS:
                print(f"    • {r['name'][:40]:40s} | {r['subscribers']:>8} | {r['links'][:40]}")

    # Запись
    if filtered:
        count = write_to_sheets(filtered, action="overwrite")
        print(f"\n  ✅ Записано в {SHEET_ID}/{SHEET_TAB}: {count} строк (только 100k+)")
    else:
        print(f"\n  ⚠️ Нет конкурентов с 100k+ подписчиков")

    # Отдельно: Зубарева
    print(f"\n{'='*60}")
    print("  🔍 Зубарева (все найденные профили):")
    zubareva = [r for r in all_rows if "зубарев" in r["name"].lower() or "зубарев" in r["description"].lower()]
    if zubareva:
        for r in zubareva:
            print(f"  ✅ {r['name']} | {r['subscribers']} подпис. | {r['links']}")
            print(f"     Источник: {r['category']}")
    else:
        print("  ❌ Не найдена в результатах Instagram + YouTube")

    # Выгружаем JSON
    out = json.dumps({
        "timestamp": datetime.now().isoformat(),
        "total": len(all_rows),
        "over_100k": len(filtered),
        "competitors": [
            {
                "name": r["name"],
                "subscribers": r["subscribers"],
                "category": r["category"],
                "source": "instagram" if "Instagram" in r["category"] else "youtube",
                "link": r["links"]
            }
            for r in filtered
        ],
        "zubareva": [
            {"name": r["name"], "subscribers": r["subscribers"], "link": r["links"], "source": r["category"]}
            for r in zubareva
        ]
    }, ensure_ascii=False, indent=2)
    with open("/root/mark1/logs/kristina_100k_result.json", "w") as f:
        f.write(out)
    print(f"\n  📄 JSON сохранён: /root/mark1/logs/kristina_100k_result.json")

if __name__ == "__main__":
    main()
