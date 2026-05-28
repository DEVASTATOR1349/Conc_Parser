#!/usr/bin/env python3
"""
v2: Поиск конкурентов для НОМОС КЛИНИК.
Использует YouTube API, Apify Instagram, Brave Search API.

Запуск: python3 search_competitors.py
Авто:   через crontab в apify-parser или n8n

Переменные окружения (.env):
  YOUTUBE_API_KEY=
  APIFY_API_TOKEN=
  BRAVE_API_KEY=
"""

import html, json, os, re, sys, time
from datetime import datetime
from urllib.parse import urlparse

import requests

# Google Sheets клиент (опционально, если запуск не из apify-parser)
try:
    sys.path.insert(0, "/app/src")
    from sheets import _get_service as get_sheets_service
except ImportError:
    get_sheets_service = None

YT_KEY = os.getenv("YOUTUBE_API_KEY", "")
APIFY_TOKEN = os.getenv("APIFY_API_TOKEN", "")
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "")

COMPETITORS_SHEET_ID = "1zVNwBX7e8FIZ-0bP7qU2UTbueXrukoev0NbSCS9EwHQ"
REPORT_TAB = "Отчёт по конкурентам"

# ── Фильтры ──

BAD_KEYWORDS = [
    "песня", "музыка", "игра", "фильм", "кино", "сериал",
    "юмор", "прикол", "рецепт", "кулинария", "путешествия",
    "мск 24", "новости", "спорт", "футбол",
    "собчак", "бородина", "ургант", "иноагент",
    "хайп", "ремонт", "стройка", "дизайн интерьера",
    "еда", "вкусно", "готовим",
    "авто", "машина", "drift", "тачки",
    "халил", "halil", "hair transplant turkey",
    "istanbul", "instambul", "турция пересадка",
    "животные", "собака", "кошка",
    "lifestyle блог", "shopping", "макияж уроки",
    "оратор", "успех", "бизнес молодость",
]

RELEVANT_WORDS = [
    "клиник", "косметолог", "дерматолог", "трихолог",
    "пластический", "эстетический", "медицин",
    "лазерн", "инъекци", "ботокс", "филлер",
    "омоложен", "лифтинг", "нитив", "пилинг",
    "биоревитализаци", "акне",
    "пересадк", "волос", "хирург",
    "доктор", "dr.", "врач",
    "москв", "moscow",
]

# ── Поисковые запросы ──

YT_SEARCH_QUERIES = [
    "косметология Москва клиника",
    "лазерная косметология Москва",
    "пластическая хирургия Москва",
    "инъекционная косметология Москва",
    "трихология Москва",
    "дерматолог косметолог Москва",
    "аппаратная косметология Москва",
    "нити лицо Москва клиника",
    "омоложение лица Москва",
    "лечение акне Москва",
    "эстетическая медицина Москва",
    "бьюти клиника Москва",
    "косметолог москва отзывы",
]

BRAVE_SEARCH_QUERIES = [
    "косметология Москва клиника сайт",
    "пластическая хирургия Москва клиника",
    "лазерная эпиляция Москва клиника отзывы",
    "инъекционная косметология Москва цены",
    "аппаратная косметология Москва центр",
    "трихология Москва центр лечения волос",
    "омоложение лица Москва клиника",
    "нити лицо Москва косметолог",
    "ботокс филлеры Москва клиника",
    "эстетическая медицина Москва рейтинг",
]

# ── Вспомогательные функции ──


def get_sheets():
    if get_sheets_service:
        return get_sheets_service()
    return None


def get_existing(service):
    """Возвращаем (ссылки, названия) из таблицы — чтобы не дублировать."""
    try:
        result = service.spreadsheets().values().get(
            spreadsheetId=COMPETITORS_SHEET_ID,
            range=f"{REPORT_TAB}!A:C"
        ).execute()
        values = result.get("values", [])
        links = set()
        names = set()
        for row in values[1:]:
            if len(row) > 0:
                names.add(row[0].strip().lower())
            if len(row) > 2:
                for link in re.split(r'[,;\s]+', row[2].strip()):
                    link = link.strip().rstrip('/')
                    if link:
                        links.add(link.lower())
        return links, names
    except Exception as e:
        print(f"  Ошибка чтения: {e}")
        return set(), set()


def is_relevant(title, desc):
    text = f"{title} {desc}".lower()
    for kw in BAD_KEYWORDS:
        if kw.lower() in text:
            return False
    return any(w in text for w in RELEVANT_WORDS)


def fmt_subs(n):
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    elif n >= 1_000:
        return f"{n//1000}K"
    return str(n)


def dedup(items, key="name"):
    seen = {}
    for item in items:
        k = item[key].lower()
        if k not in seen:
            seen[k] = item
    return list(seen.values())


# ── Источники поиска ──


def search_brave(existing_links, existing_names):
    """Поиск через Brave Search API (бесплатно: 2000 запросов/мес)."""
    if not BRAVE_API_KEY:
        print("  НЕТ BRAVE_API_KEY")
        return []

    found = []
    for query in BRAVE_SEARCH_QUERIES:
        print(f"  Brave: {query}", end="", flush=True)
        try:
            url = (f"https://api.search.brave.com/res/v1/web/search"
                   f"?q={requests.utils.quote(query)}"
                   f"&count=10"
                   f"&country=RU"
                   f"&search_lang=ru")
            headers = {
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "X-Subscription-Token": BRAVE_API_KEY,
            }
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code != 200:
                print(f" -> {resp.status_code}")
                time.sleep(0.5)
                continue

            data = resp.json()
            results = []
            for group in ("web", "news"):
                for item in data.get(group, {}).get("results", []):
                    results.append(item)

            n = 0
            for item in results:
                title = html.unescape(item.get("title", "").strip())
                desc = html.unescape(
                    item.get("description", "")
                    or item.get("snippet", "")
                ).strip()
                link = item.get("url", "")

                if not title or not is_relevant(title, desc):
                    continue
                if title.lower() in existing_names:
                    continue
                if link.lower().rstrip("/") in existing_links:
                    continue

                domain = urlparse(link).netloc.lower()
                cat = "Прямой — клиника/косметолог"
                if "instagram" in domain:
                    cat = "Прямой — косметолог/блогер"
                elif any(d in domain for d in ("prodoctorov", "otzyv", "napopravku")):
                    cat = "Отзовик — профиль клиники"

                found.append({
                    "name": title,
                    "category": cat,
                    "links": link,
                    "subscribers": "—",
                    "positioning": desc[:300] if desc else "Клиника косметологии",
                    "services": "Косметологические услуги",
                    "price_segment": "—",
                    "strengths": f"Brave Search. {desc[:250]}".strip()[:300],
                    "weaknesses": "—",
                    "tov": "—",
                    "audience": "Женщины 25–50",
                    "activity": "—",
                    "formats": "—",
                    "threat_level": "—",
                    "borrow": "—",
                    "conclusion": f"Найден Brave Search: {query}",
                    "source": "brave",
                })

                existing_links.add(link.lower().rstrip("/"))
                existing_names.add(title.lower())
                n += 1

            print(f" +{n}")
            time.sleep(0.3)
        except Exception as e:
            print(f" ERR: {e}")

    return dedup(found)


def search_youtube(service, existing_links, existing_names):
    if not YT_KEY:
        print("  НЕТ YOUTUBE_API_KEY")
        return []

    found = []
    for query in YT_SEARCH_QUERIES:
        print(f"  YT: {query}", end="", flush=True)
        try:
            url = (f"https://youtube.googleapis.com/youtube/v3/search"
                   f"?part=snippet"
                   f"&q={requests.utils.quote(query)}"
                   f"&type=channel"
                   f"&maxResults=10"
                   f"&relevanceLanguage=ru"
                   f"&key={YT_KEY}")
            resp = requests.get(url, timeout=15)
            if resp.status_code != 200:
                print(f" -> {resp.status_code}")
                continue

            items = resp.json().get("items", [])
            n = 0
            for item in items:
                s = item["snippet"]
                title = s.get("title", "").strip()
                cid = item["id"]["channelId"]
                desc = s.get("description", "").strip()

                if not is_relevant(title, desc):
                    continue
                if title.lower() in existing_names:
                    continue

                c_url = f"https://www.youtube.com/channel/{cid}"
                if c_url.lower() in existing_links:
                    continue

                # Статистика канала
                stats = {}
                if YT_KEY:
                    try:
                        surl = (f"https://youtube.googleapis.com/youtube/v3/channels"
                                f"?part=statistics&id={cid}&key={YT_KEY}")
                        sr = requests.get(surl, timeout=10)
                        if sr.status_code == 200:
                            si = sr.json().get("items", [])
                            if si:
                                st = si[0].get("statistics", {})
                                stats = {
                                    "subs": int(st.get("subscriberCount", 0)),
                                    "videos": int(st.get("videoCount", 0)),
                                }
                    except Exception:
                        pass

                ss = fmt_subs(stats.get("subs", 0)) if stats else "?"
                vc = stats.get("videos", 0) if stats else 0
                act = f"~{vc} видео" if vc else "—"
                strong = f"YouTube-канал"
                if ss != "?":
                    strong += f", {ss} подписчиков"
                strong += f". {desc[:200]}"

                found.append({
                    "name": title,
                    "category": "Прямой — клиника/косметолог",
                    "links": c_url,
                    "subscribers": f"{ss} (YouTube)" if ss != "?" else "—",
                    "positioning": desc[:250] if desc else "YouTube-канал по косметологии",
                    "services": "Косметологические услуги",
                    "price_segment": "—",
                    "strengths": strong.strip()[:300],
                    "weaknesses": "—",
                    "tov": "—",
                    "audience": "Женщины 25–50",
                    "activity": act,
                    "formats": "YouTube",
                    "threat_level": "—",
                    "borrow": "—",
                    "conclusion": f"Найден по запросу: {query}",
                    "source": "youtube",
                })

                existing_links.add(c_url.lower())
                existing_names.add(title.lower())
                n += 1

            print(f" +{n}")
            time.sleep(0.4)
        except Exception as e:
            print(f" ERR: {e}")

    return dedup(found)


def search_instagram_apify(existing_names):
    if not APIFY_TOKEN:
        print("  НЕТ APIFY_TOKEN")
        return []

    print("  IG: запускаем Apify Search...")
    from apify_client import ApifyClient
    client = ApifyClient(token=APIFY_TOKEN)

    queries = ["косметология Москва", "пластический хирург Москва"]

    found = []
    for query in queries:
        print(f"    Поиск: {query}")
        try:
            run = client.actor("apify/instagram-search-scraper").call(
                run_input={
                    "searchType": "user",
                    "search": query,
                    "resultsLimit": 10,
                    "proxy": {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"]},
                },
                wait_sec=60,
            )
            dataset = client.dataset(run["defaultDatasetId"])
            items = dataset.list_items().items

            for item in items:
                username = item.get("username", "")
                full_name = item.get("fullName", "") or username
                followers = item.get("followerCount", 0) or 0
                posts = item.get("postCount", 0) or 0
                bio = item.get("biography", "") or ""
                is_biz = item.get("isBusinessAccount", False)

                if followers < 100 or not is_biz:
                    continue
                if full_name.lower() in existing_names:
                    continue
                if not is_relevant(full_name, bio):
                    continue

                url = f"https://www.instagram.com/{username}/"
                ss = fmt_subs(followers)
                act = f"~{posts} постов" if posts else "—"

                found.append({
                    "name": f"{full_name} (@{username})",
                    "category": "Прямой — косметолог/блогер",
                    "links": url,
                    "subscribers": f"{ss} (IG)",
                    "positioning": bio[:250] if bio else "Instagram-блогер",
                    "services": "Косметология",
                    "price_segment": "—",
                    "strengths": f"Instagram-блогер, {ss} подписчиков. {bio[:200]}".strip()[:300],
                    "weaknesses": "—",
                    "tov": "—",
                    "audience": "Женщины 20–45",
                    "activity": act,
                    "formats": "Instagram (Reels, Stories)",
                    "threat_level": "—",
                    "borrow": "—",
                    "conclusion": "Найден через Apify Instagram.",
                    "source": "instagram",
                })
                existing_names.add(full_name.lower())

            print(f"    Найдено: {len(found)}")
        except Exception as e:
            print(f"    ERR: {e}")

    return dedup(found)


# ── Запись в Google Sheets ──


def write_results(service, rows):
    if not rows:
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
    for i in range(0, len(values), 10):
        batch = values[i:i + 10]
        try:
            service.spreadsheets().values().append(
                spreadsheetId=COMPETITORS_SHEET_ID,
                range=f"{REPORT_TAB}!A:P",
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": batch}
            ).execute()
            total += len(batch)
            print(f"  Записано: {len(batch)} (всего {total})")
        except Exception as e:
            print(f"  Ошибка записи: {e}")
            break
        time.sleep(0.3)
    return total


# ── Main ──


def main():
    print("=" * 60)
    print(f"ПОИСК КОНКУРЕНТОВ v2 + Brave Search")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    service = get_sheets()
    if not service:
        print("⚠️ Без Google Sheets (режим dry-run, найденные не сохраним)")

    el, en = set(), set()
    if service:
        print("\n1. Существующие записи...")
        el, en = get_existing(service)
        print(f"   Ссылок: {len(el)}, Названий: {len(en)}")

    print("\n2. Brave Search...")
    br = search_brave(el, en)

    print("\n3. YouTube API...")
    yt = search_youtube(service, el, en) if service else search_brave(el, en)

    print("\n4. Instagram (Apify)...")
    ig = search_instagram_apify(en)

    all_new = br + yt + ig
    print(f"\nИТОГО: {len(br)} Brave + {len(yt)} YT + {len(ig)} IG = {len(all_new)}")

    if all_new and service:
        written = write_results(service, all_new)
        print(f"\nЗаписано: {written}")
    elif all_new:
        print(f"\n(dry-run) Найдено: {len(all_new)}, не записано (нет Sheets)")

    if all_new:
        print("\nТоп:")
        for item in all_new[:10]:
            s = item.get("subscribers", "")
            src = item.get("source", "")
            print(f"  [{src}] {item['name'][:36]:36s} | {s:12s} | {item['links'][:40]}")

    print("=" * 60)


if __name__ == "__main__":
    main()
