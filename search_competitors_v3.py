#!/usr/bin/env python3
"""
search_competitors_v3.py — Парсер конкурентов с приоритетом соцсетей.

Распределение (целевое):
  Instagram: 30-35%   TikTok: 15-20%   YouTube: 10-15%
  VK:       15-20%    Сайты: 10-15%

Новое в v3:
- YouTube через SOCKS5 (порт 1080)
- Многопоточный сбор соцсетей
- Увеличенные лимиты для Instagram/TikTok
- Параллельные запросы

Запуск:
  python3 search_competitors_v3.py --client kristina  # один клиент
  python3 search_competitors_v3.py --all               # все клиенты
"""

import concurrent.futures as cf
import json, os, re, sys, time
from datetime import datetime
from pathlib import Path

import requests, socks, socket

# ── SOCKS5 для YouTube ──
SOCKS5_HOST = "127.0.0.1"
SOCKS5_PORT = 1080

# ── Загрузка .env ──
for env_path in [".env", "../.env", "/app/.env"]:
    p = Path(__file__).parent / env_path if not env_path.startswith("/") else Path(env_path)
    if p.exists():
        with open(p) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if not os.getenv(k.strip()):
                    os.environ[k.strip()] = v.strip()

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
from sheets import get_existing, write_results, ensure_headers, get_service, update_cells
from client_loader import load_client, list_clients

# ── Токены ──
BRAVE_KEY = os.getenv("BRAVE_API_KEY", "")
YT_KEY = os.getenv("YOUTUBE_API_KEY", "")
VK_KEY = os.getenv("VK_API_KEY", "")
APIFY_TOKEN = os.getenv("APIFY_API_TOKEN", "")
OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "")

SHEET_ID = os.getenv("SHEET_ID", "")
SHEET_TAB = os.getenv("SHEET_TAB", "Отчёт по конкурентам")


# ═══════════════════════════════════
#  Утилиты
# ═══════════════════════════════════

def _yt_fetch(url, **params):
    """YouTube API через SOCKS5."""
    import requests as r2
    session = r2.Session()
    session.proxies = {
        "http": f"socks5h://{SOCKS5_HOST}:{SOCKS5_PORT}",
        "https": f"socks5h://{SOCKS5_HOST}:{SOCKS5_PORT}",
    }
    return session.get(url, params=params, timeout=15)


def _empty_row():
    return {
        "name": "", "category": "", "links": "", "subscribers": 0,
        "positioning": "", "services": "", "price_segment": "",
        "strengths": "", "weaknesses": "", "tov": "",
        "audience": "", "activity": "", "formats": "",
        "threat_level": "", "borrow": "", "conclusion": "",
        "validation": "", "description": "",
    }


def is_relevant(title, desc, cfg):
    exclude = cfg.get("exclude_keywords", [])
    text = f"{title} {desc}".lower()
    return not any(kw.lower().strip() in text for kw in exclude)


def dedup(items, key="name"):
    seen = {}
    for item in items:
        k = item.get(key, "").lower().strip()
        if k not in seen:
            seen[k] = item
    return list(seen.values())


# ═══════════════════════════════════
#  YOUTUBE (через SOCKS5)
# ═══════════════════════════════════

def search_youtube(cfg, existing_links, existing_names, tier=None):
    if not YT_KEY:
        print("  [YouTube] ❌ Нет YOUTUBE_API_KEY")
        return []

    queries = cfg.get("queries_youtube", [])
    base = cfg.get("title", "").split("–")[0].strip()
    if not queries:
        queries = [
            f"{base}", f"{base} врач", f"{base} консультация",
            f"{base} блог", f"{base} канал", f"{base} советы",
            f"{base} клиника", f"{base} москва",
        ]
    queries = [q for q in queries if q.strip()]

    found = []
    max_results = 50  # максимум
    max_queries = 12

    for query in queries[:max_queries]:
        print(f"  [YouTube] \"{query[:40]}\"", end="", flush=True)
        try:
            resp = _yt_fetch(
                "https://www.googleapis.com/youtube/v3/search",
                part="snippet", q=query, type="channel",
                maxResults=max_results, relevanceLanguage="ru",
                key=YT_KEY,
            )
            if resp.status_code != 200:
                print(f" HTTP {resp.status_code}")
                time.sleep(0.3)
                continue

            n = 0
            for item in resp.json().get("items", []):
                s = item["snippet"]
                title = s.get("title", "").strip()
                cid = item["id"]["channelId"]
                desc = s.get("description", "").strip()
                c_url = f"https://www.youtube.com/channel/{cid}"
                if not title or not is_relevant(title, desc, cfg):
                    continue
                if title.lower() in existing_names or c_url.lower() in existing_links:
                    continue

                # Статистика
                subs = 0
                try:
                    sr = _yt_fetch(
                        "https://www.googleapis.com/youtube/v3/channels",
                        part="statistics", id=cid, key=YT_KEY,
                    )
                    if sr.status_code == 200:
                        si = sr.json().get("items", [])
                        if si:
                            subs = int(si[0].get("statistics", {}).get("subscriberCount", 0))
                except:
                    pass

                if tier:
                    t_min = {"1": 100_000, "2": 50_000, "3": 10_000}.get(str(tier), 0)
                    t_max = {"1": 999_999_999, "2": 99_999, "3": 49_999}.get(str(tier), 999_999_999)
                    if subs < t_min or subs > t_max:
                        continue

                threat = min(10, subs // 5000 + 1) if subs > 1000 else 1

                row = _empty_row()
                row.update({
                    "name": title, "category": "YouTube — канал",
                    "_source": "youtube", "links": c_url,
                    "subscribers": subs,
                    "positioning": desc[:300] if desc else "YouTube-канал",
                    "strengths": f"YouTube. {subs} подписчиков",
                    "formats": "YouTube (длинные видео, shorts, live)",
                    "threat_level": threat,
                    "description": desc[:300],
                    "validation": "—",
                    "services": "—", "price_segment": "—",
                    "weaknesses": "—", "tov": "—",
                    "audience": "—", "activity": "—",
                    "borrow": "—", "conclusion": f"YT: {title} | {subs} подпис.",
                })
                found.append(row)
                existing_links.add(c_url.lower())
                existing_names.add(title.lower())
                n += 1
                if n >= 10:
                    break
            print(f" +{n}")
        except Exception as e:
            print(f" ERR: {e}")
        time.sleep(0.5)
    return found


# ═══════════════════════════════════
#  VK API (через GET)
# ═══════════════════════════════════

def search_vk(cfg, existing_links, existing_names, tier=None):
    if not VK_KEY:
        print("  [VK] ❌ Нет VK_API_KEY")
        return []

    queries = cfg.get("queries_vk", [])
    base = cfg.get("title", "")
    if not queries:
        queries = [
            f"{base}", f"{base} консультация", f"{base} врач",
            f"{base} здоровье", f"{base} питание", f"{base} блог",
            f"{base} москва",
        ]
    queries = [q for q in queries if q.strip()]

    found = []
    for query in queries[:10]:
        print(f"  [VK] \"{query[:40]}\"", end="", flush=True)
        try:
            resp = requests.get(
                "https://api.vk.com/method/groups.search",
                params={
                    "q": query, "type": "group,page",
                    "count": 30, "access_token": VK_KEY, "v": "5.199",
                }, timeout=15)
            if resp.status_code != 200:
                print(f" HTTP {resp.status_code}")
                continue

            items = resp.json().get("response", {}).get("items", [])
            n = 0
            for item in items:
                name = item.get("name", "").strip()
                gid = item.get("id", 0)
                desc = item.get("description", "") or ""
                members = item.get("members_count", 0) or 0
                url = f"https://vk.com/public{gid}" if gid else f"https://vk.com/{item.get('screen_name','')}"
                if not name or not is_relevant(name, desc, cfg):
                    continue
                if name.lower() in existing_names or url.lower() in existing_links:
                    continue
                if tier:
                    t_min = {"1": 100_000, "2": 50_000, "3": 10_000}.get(str(tier), 0)
                    t_max = {"1": 999_999_999, "2": 99_999, "3": 49_999}.get(str(tier), 999_999_999)
                    if members < t_min or members > t_max:
                        continue
                if members < 100:
                    continue

                threat = min(10, members // 5000 + 1) if members > 1000 else 1
                row = _empty_row()
                row.update({
                    "name": name, "category": "VK — сообщество",
                    "_source": "vk", "links": url,
                    "subscribers": members,
                    "positioning": desc[:300] if desc else f"VK-сообщество {name}",
                    "strengths": f"VK. {members} участников",
                    "formats": "VK (посты, видео, stories)",
                    "threat_level": threat,
                    "description": desc[:300],
                    "validation": "—",
                    "services": "—", "price_segment": "—",
                    "weaknesses": "—", "tov": "—",
                    "audience": "—", "activity": "—",
                    "borrow": "—", "conclusion": f"VK: {name} | {members} уч.",
                })
                found.append(row)
                existing_links.add(url.lower())
                existing_names.add(name.lower())
                n += 1
                if n >= 15:
                    break
            print(f" +{n}")
        except Exception as e:
            print(f" ERR: {e}")
    return found


# ═══════════════════════════════════
#  BRAVE SEARCH (сайты)
# ═══════════════════════════════════

def search_brave(cfg, existing_links, existing_names, tier=None):
    if not BRAVE_KEY:
        print("  [Brave] ❌ Нет BRAVE_API_KEY")
        return []

    queries = cfg.get("queries_brave", [])
    base = cfg.get("title", "")
    if not queries:
        queries = [
            f"{base} сайт", f"{base} консультация", f"{base} клиника",
            f"{base} врач москва", f"{base} блог", f"лучшие {base}",
        ]
    queries = [q for q in queries if q.strip()]

    found = []
    for query in queries[:12]:
        print(f"  [Brave] \"{query[:40]}\"", end="", flush=True)
        try:
            resp = requests.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": 10, "country": "RU", "search_lang": "ru"},
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip",
                    "X-Subscription-Token": BRAVE_KEY,
                }, timeout=10)
            if resp.status_code != 200:
                print(f" HTTP {resp.status_code}")
                continue

            n = 0
            for item in resp.json().get("web", {}).get("results", []):
                title = item.get("title", "").strip()
                desc = item.get("description", "").strip()
                url = item.get("url", "").strip()
                if not title or not url or not is_relevant(title, desc, cfg):
                    continue
                # Фильтр: агрегаторы/отзовики
                if any(d in url.lower() for d in ("prodoctorov.ru", "napopravku.ru", "docdoc.ru", "zoon.ru")):
                    continue
                if title.lower() in existing_names or url.lower() in existing_links:
                    continue
                if re.search(r"топ[\s-]?\d|рейтинг|10 лучш|подборк|как выбрать", title.lower()):
                    continue

                row = _empty_row()
                row.update({
                    "name": title, "category": "Сайт — прямой конкурент",
                    "_source": "brave", "links": url,
                    "subscribers": 0,
                    "positioning": desc[:300],
                    "strengths": "Найден через Brave Search",
                    "formats": "Сайт (статьи, услуги, личный блог)",
                    "threat_level": 3,
                    "description": desc[:300],
                    "validation": "—",
                    "services": "—", "price_segment": "—",
                    "weaknesses": "—", "tov": "—",
                    "audience": "—", "activity": "—",
                    "borrow": "—", "conclusion": f"Сайт: {title}",
                })
                found.append(row)
                existing_links.add(url.lower())
                existing_names.add(title.lower())
                n += 1
                if n >= 8:
                    break
            print(f" +{n}")
        except Exception as e:
            print(f" ERR: {e}")
    return found


# ═══════════════════════════════════
#  INSTAGRAM (Apify)
# ═══════════════════════════════════

def search_instagram(cfg, existing_links, existing_names, tier=None):
    if not APIFY_TOKEN:
        print("  [Instagram] ❌ Нет APIFY_TOKEN")
        return []

    queries = cfg.get("queries_instagram", [])
    base = cfg.get("title", "")
    if not queries:
        queries = [f"{base}", f"{base} врач", f"{base} блог", f"{base} здоровье"]
    queries = [q for q in queries if q.strip()]

    try:
        from apify_client import ApifyClient
    except ImportError:
        print("  [Instagram] apify-client не установлен")
        return []

    client = ApifyClient(token=APIFY_TOKEN)
    found = []
    max_per_q = 20

    for query in queries[:8]:
        print(f"  [Instagram] \"{query[:40]}\"", end="", flush=True)
        try:
            run = client.actor("apify/instagram-search-scraper").call(
                run_input={
                    "searchType": "user", "search": query,
                    "resultsLimit": max_per_q,
                    "proxy": {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"]},
                },
            )
        except:
            try:
                run = client.actor("apify/instagram-search-scraper").call(
                    run_input={
                        "searchType": "user", "search": query,
                        "resultsLimit": max_per_q,
                        "proxy": {"useApifyProxy": True},
                    },
                )
            except Exception as e:
                print(f" ERR: {e}")
                continue

        try:
            ds_id = getattr(run, "default_dataset_id", None)
            items = list(client.dataset(ds_id).iterate_items())
        except Exception as e:
            print(f" ERR: {e}")
            continue

        n = 0
        for item in items:
            username = item.get("username", "")
            full_name = item.get("fullName", "") or username
            followers = item.get("followersCount", 0) or 0
            bio = item.get("biography", "") or ""
            url = f"https://instagram.com/{username}"
            if not full_name or followers < 50:
                continue
            if full_name.lower() in existing_names or url.lower() in existing_links:
                continue
            if not is_relevant(full_name, bio, cfg):
                continue

            threat = min(10, followers // 5000 + 1) if followers > 1000 else 2
            row = _empty_row()
            row.update({
                "name": full_name, "category": "Instagram — профиль",
                "_source": "instagram", "links": url,
                "subscribers": followers,
                "positioning": bio[:300] if bio else f"Instagram @{username}",
                "strengths": f"Instagram. {followers} подписчиков",
                "formats": "Instagram (посты, stories, reels)",
                "threat_level": threat,
                "description": bio[:300],
                "validation": "—",
                "services": "—", "price_segment": "—",
                "weaknesses": "—", "tov": "—",
                "audience": "—", "activity": "—",
                "borrow": "—", "conclusion": f"@{username} | {followers} подп.",
            })
            found.append(row)
            existing_links.add(url.lower())
            existing_names.add(full_name.lower())
            n += 1
            if n >= 10:
                break
        print(f" +{n}")
        time.sleep(1)
    return found


# ═══════════════════════════════════
#  TIKTOK (Apify)
# ═══════════════════════════════════

def search_tiktok(cfg, existing_links, existing_names, tier=None):
    if not APIFY_TOKEN:
        print("  [TikTok] ❌ Нет APIFY_TOKEN")
        return []

    queries = cfg.get("queries_tiktok", [])
    base = cfg.get("title", "")
    if not queries:
        queries = [f"{base}", f"{base} врач", f"{base} здоровье"]
    queries = [q for q in queries if q.strip()]

    try:
        from apify_client import ApifyClient
    except ImportError:
        print("  [TikTok] apify-client не установлен")
        return []

    client = ApifyClient(token=APIFY_TOKEN)
    found = []
    seen = set()
    max_per_q = 20

    for query in queries[:6]:
        print(f"  [TikTok] \"{query[:40]}\"", end="", flush=True)
        try:
            run = client.actor("clockworks/tiktok-scraper").call(
                run_input={
                    "searchQueries": [query],
                    "maxProfilesPerQuery": max_per_q,
                    "searchSection": "",
                    "proxyCountryCode": "None",
                },
            )
        except Exception as e:
            print(f" ERR: {e}")
            continue

        try:
            ds_id = getattr(run, "default_dataset_id", None)
            items = list(client.dataset(ds_id).iterate_items())
        except:
            continue

        n = 0
        for item in items:
            author = item.get("authorMeta", {})
            if not author:
                continue
            username = author.get("name", "")
            name = author.get("nickName", "") or username
            bio = author.get("signature", "") or ""
            followers = int(author.get("fans", 0) or 0)
            url = author.get("profileUrl", "") or f"https://tiktok.com/@{username}"
            if not name or username.lower() in seen:
                continue
            if name.lower() in existing_names or url.lower() in existing_links:
                continue
            if not is_relevant(name, bio, cfg):
                continue
            if followers < 100:
                continue

            threat = min(10, followers // 5000 + 1) if followers > 1000 else 2
            row = _empty_row()
            row.update({
                "name": name, "category": "TikTok — профиль",
                "_source": "tiktok", "links": url,
                "subscribers": followers,
                "positioning": bio[:300] if bio else f"TikTok @{username}",
                "strengths": f"TikTok. {followers} подписчиков",
                "formats": "TikTok (короткие видео, live)",
                "threat_level": threat,
                "description": bio[:300],
                "validation": "—",
                "services": "—", "price_segment": "—",
                "weaknesses": "—", "tov": "—",
                "audience": "—", "activity": "—",
                "borrow": "—", "conclusion": f"@{username} | {followers} подп.",
            })
            found.append(row)
            existing_links.add(url.lower())
            existing_names.add(name.lower())
            seen.add(username.lower())
            n += 1
            if n >= 10:
                break
        print(f" +{n}")
        time.sleep(1)
    return found


# ═══════════════════════════════════
#  MAIN
# ═══════════════════════════════════

def search_all(cfg, tier=None):
    """Параллельный сбор из всех источников."""
    svc = get_service()
    sid = os.getenv("SHEET_ID", SHEET_ID)
    tab = os.getenv("SHEET_TAB", SHEET_TAB)

    existing_links, existing_names = get_existing(sid, tab)

    print(f"\n{'='*50}")
    print(f"  🔍 ПОИСК: {cfg.get('title', '?')}")
    print(f"  Уже в таблице: {len(existing_names)} конкурентов")
    print(f"{'='*50}\n")

    results = {}

    # Соцсети параллельно
    with cf.ThreadPoolExecutor(max_workers=3) as pool:
        futures = {}
        for name, fn in [
            ("youtube", search_youtube),
            ("vk", search_vk),
            ("instagram", search_instagram),
            ("tiktok", search_tiktok),
        ]:
            futures[pool.submit(fn, cfg, existing_links, existing_names, tier)] = name

        for f in cf.as_completed(futures):
            name = futures[f]
            try:
                results[name] = f.result()
                print(f"  [{name}] ✅ {len(results[name])} результатов")
            except Exception as e:
                print(f"  [{name}] ❌ {e}")
                results[name] = []

    # Сайты (Brave)
    print()
    results["brave"] = search_brave(cfg, existing_links, existing_names, tier)
    print(f"  [brave] ✅ {len(results['brave'])} результатов")

    # Сборка
    all_rows = []
    # Приоритет: Instagram → TikTok → YouTube → VK → Сайты
    for src in ["instagram", "tiktok", "youtube", "vk", "brave"]:
        all_rows.extend(results.get(src, []))

    all_rows = dedup(all_rows)

    # Статистика
    cats = {}
    for r in all_rows:
        src = r.get("_source", "?")
        cats[src] = cats.get(src, 0) + 1

    print(f"\n  📊 Собрано: {len(all_rows)}")
    pct = {k: f"{v}/{len(all_rows)*100//max(len(all_rows),1)}" for k, v in cats.items()}
    for src, cnt in sorted(cats.items(), key=lambda x: -x[1]):
        pct_val = cnt * 100 // max(len(all_rows), 1)
        print(f"    {src:12s}: {cnt:3d} ({pct_val}%)")

    if all_rows:
        print(f"\n  📝 Пишем в таблицу...")
        n_written = write_results(sid, tab, all_rows)
        print(f"  ✅ Записано: {n_written}/{len(all_rows)}")

    return all_rows


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--client", "-c", default=None, help="Имя клиента")
    parser.add_argument("--all", action="store_true", help="Все клиенты")
    parser.add_argument("--tier", "-t", choices=["1", "2", "3"], default=None)
    args = parser.parse_args()

    if args.all:
        clients = list_clients()
        if not clients:
            print("❌ Нет клиентов в clients/")
            sys.exit(1)
    elif args.client:
        cfg = load_client(args.client)
        if not cfg:
            print(f"❌ Клиент {args.client} не найден")
            sys.exit(1)
        clients = [cfg]
    else:
        clients = list_clients()
        if not clients:
            print("❌ Нет клиентов")
            sys.exit(1)

    total = 0
    for cfg in clients:
        total += len(search_all(cfg, args.tier) or [])

    print(f"\n🎉 Всего собрано: {total} конкурентов")
