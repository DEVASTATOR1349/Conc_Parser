#!/usr/bin/env python3
"""
search_competitors.py — Поиск конкурентов косметологии в Москве.
Полностью самодостаточный модуль.

Источники (опционально, по наличию ключей):
  - Brave Search API (brave.com/search/api)
  - YouTube Data API v3
  - Apify Instagram Profile Scraper

Результаты:
  - Вывод в консоль
  - Google Sheets (если настроен сервисный аккаунт)

Запуск:
  export BRAVE_API_KEY=...
  python3 search_competitors.py
"""

import html
import os
import re
import sys
import time
from datetime import datetime
from urllib.parse import urlparse

import requests

# ── Подключаем свой sheets (рядом в src/) ──
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
from sheets import get_existing, write_results

# ── Конфигурация из .env / переменных окружения ──
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "")
YT_KEY = os.getenv("YOUTUBE_API_KEY", "")
APIFY_TOKEN = os.getenv("APIFY_API_TOKEN", "")

# Таблица конкурентов (НОМОС КЛИНИК)
SHEET_ID = os.getenv("SHEET_ID", "1zVNwBX7e8FIZ-0bP7qU2UTbueXrukoev0NbSCS9EwHQ")
SHEET_TAB = os.getenv("SHEET_TAB", "Отчёт по конкурентам")

# ── Фильтры ──

# Слова-маркеры, что результат НЕ про косметологию
BAD_KEYWORDS = [
    "песня", "музыка", "игра", "фильм", "кино", "сериал",
    "юмор", "прикол", "рецепт", "кулинария", "путешествия",
    "мск 24", "новости", "спорт", "футбол",
    "собчак", "бородина", "ургант", "иноагент",
    "хайп", "ремонт", "стройка", "дизайн интерьера",
    "еда", "вкусно", "готовим",
    "авто", "машина", "drift", "тачки",
    "халил", "halil", "hair transplant", "istanbul",
    "животные", "собака", "кошка",
    "lifestyle", "shopping", "макияж",
    "оратор", "успех", "бизнес молодость",
]

# Слова-маркеры, что результат ПРО косметологию
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

BRAVE_QUERIES = [
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
    "дерматолог косметолог Москва отзывы",
]

YT_QUERIES = [
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

# ── Утилиты ──


def fmt_subs(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    elif n >= 1_000:
        return f"{n // 1000}K"
    return str(n)


def is_relevant(title, desc):
    """Проверка, что результат релевантен косметологии."""
    text = f"{title} {desc}".lower()
    for kw in BAD_KEYWORDS:
        if kw.lower() in text:
            return False
    return any(w in text for w in RELEVANT_WORDS)


def classify_domain(domain):
    """Определить категою конкурента по домену."""
    if "instagram" in domain:
        return "Прямой — косметолог/блогер"
    if any(d in domain for d in ("prodoctorov", "otzyv", "napopravku")):
        return "Отзовик — профиль клиники"
    return "Прямой — клиника/косметолог"


def dedup(items, key="name"):
    seen = {}
    for item in items:
        k = item[key].lower()
        if k not in seen:
            seen[k] = item
    return list(seen.values())


# ═══════════════════════════════════════════════════
#  ИСТОЧНИКИ
# ═══════════════════════════════════════════════════


def from_brave(existing_links, existing_names):
    """Поиск через Brave Search API."""
    if not BRAVE_API_KEY:
        print("  [Brave] Нет BRAVE_API_KEY — пропускаем")
        return []

    found = []
    for query in BRAVE_QUERIES:
        print(f"  [Brave] {query}", end="", flush=True)
        try:
            resp = requests.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": 10, "country": "RU", "search_lang": "ru"},
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip",
                    "X-Subscription-Token": BRAVE_API_KEY,
                },
                timeout=15,
            )
            if resp.status_code != 200:
                print(f" -> {resp.status_code}")
                time.sleep(0.5)
                continue

            data = resp.json()
            n, results = 0, []
            for grp in ("web", "news"):
                for item in data.get(grp, {}).get("results", []):
                    results.append(item)

            for item in results:
                title = html.unescape(item.get("title", "").strip())
                desc = html.unescape(
                    item.get("description") or item.get("snippet") or ""
                ).strip()
                link = item.get("url", "")

                if not title or not is_relevant(title, desc):
                    continue
                if title.lower() in existing_names:
                    continue
                if link.lower().rstrip("/") in existing_links:
                    continue

                domain = urlparse(link).netloc.lower()

                found.append({
                    "name": title,
                    "category": classify_domain(domain),
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
                })

                existing_links.add(link.lower().rstrip("/"))
                existing_names.add(title.lower())
                n += 1

            print(f" +{n}")
            time.sleep(0.3)
        except Exception as e:
            print(f" ERR: {e}")

    return dedup(found)


def from_youtube(existing_links, existing_names):
    """Поиск через YouTube Data API v3."""
    if not YT_KEY:
        print("  [YouTube] Нет YOUTUBE_API_KEY — пропускаем")
        return []

    found = []
    for query in YT_QUERIES:
        print(f"  [YouTube] {query}", end="", flush=True)
        try:
            resp = requests.get(
                "https://youtube.googleapis.com/youtube/v3/search",
                params={
                    "part": "snippet",
                    "q": query,
                    "type": "channel",
                    "maxResults": 10,
                    "relevanceLanguage": "ru",
                    "key": YT_KEY,
                },
                timeout=15,
            )
            if resp.status_code != 200:
                print(f" -> {resp.status_code}")
                continue

            n = 0
            for item in resp.json().get("items", []):
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

                # Подписчики и видео (доп. запрос)
                stats = {}
                try:
                    sr = requests.get(
                        "https://youtube.googleapis.com/youtube/v3/channels",
                        params={"part": "statistics", "id": cid, "key": YT_KEY},
                        timeout=10,
                    )
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
                    "conclusion": f"Найден YouTube: {query}",
                })

                existing_links.add(c_url.lower())
                existing_names.add(title.lower())
                n += 1

            print(f" +{n}")
            time.sleep(0.4)
        except Exception as e:
            print(f" ERR: {e}")

    return dedup(found)


def from_instagram_apify(existing_names):
    """Поиск через Apify Instagram Profile Scraper (RESIDENTIAL proxy)."""
    if not APIFY_TOKEN:
        print("  [Instagram] Нет APIFY_TOKEN — пропускаем")
        return []

    print("  [Instagram] Apify Search...")
    try:
        from apify_client import ApifyClient
    except ImportError:
        print("    apify-client не установлен — пропускаем")
        return []

    client = ApifyClient(token=APIFY_TOKEN)
    found = []

    for query in ("косметология Москва", "пластический хирург Москва"):
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
                })
                existing_names.add(full_name.lower())

        except Exception as e:
            print(f"    ERR: {e}")

    return dedup(found)


# ═══════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════


def main():
    print("=" * 60)
    print(f"  ПОИСК КОНКУРЕНТОВ")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    # 1. Читаем существующие записи (если есть Sheets)
    print("\n1. Существующие записи...")
    existing_links, existing_names = set(), set()
    if SHEET_ID:
        try:
            existing_links, existing_names = get_existing(SHEET_ID, SHEET_TAB)
            print(f"   Ссылок: {len(existing_links)}, Названий: {len(existing_names)}")
        except Exception as e:
            print(f"   Нет доступа к Sheets или ошибка: {e}")
            print("   Продолжаем без чтения дублей")
    else:
        print("   SHEET_ID не задан — дубли не фильтруем")

    # 2. Brave Search
    print("\n2. Brave Search...")
    brave_res = from_brave(existing_links, existing_names)

    # 3. YouTube
    print("\n3. YouTube API...")
    yt_res = from_youtube(existing_links, existing_names)

    # 4. Instagram (Apify)
    print("\n4. Instagram (Apify)...")
    ig_res = from_instagram_apify(existing_names)

    # 5. Итого
    all_new = brave_res + yt_res + ig_res
    print(f"\n{'=' * 60}")
    print(f"  ИТОГО: {len(brave_res)} Brave + {len(yt_res)} YT + {len(ig_res)} IG = {len(all_new)}")
    print(f"{'=' * 60}")

    # 6. Запись в Google Sheets
    if all_new and SHEET_ID:
        print("\n5. Запись в Google Sheets...")
        written = write_results(SHEET_ID, SHEET_TAB, all_new)
        print(f"\n  Записано строк: {written}")
    elif all_new:
        print("\n  (dry-run) Sheets не настроен — данные только в stdout")
    else:
        print("\n  ✅ Новых конкурентов не найдено")

    # 7. Вывод топ-15
    if all_new:
        print("\n  Топ найденных:")
        for item in all_new[:15]:
            subs = item.get("subscribers", "")
            links = item.get("links", "")
            print(f"    • {item['name'][:36]:36s} | {subs:12s} | {links[:40]}")

    print()


if __name__ == "__main__":
    main()
