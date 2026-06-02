#!/usr/bin/env python3
"""
search_competitors.py — Мульти-клиентский поиск конкурентов.

Приоритет: СОЦСЕТИ > САЙТЫ
  1. Instagram (Apify)
  2. TikTok (Apify)
  3. YouTube Data API v3
  4. VK API
  5. Brave Search (сайты — второй план)

Колонки (18):
  A: Конкурент (название)     B: Категория     C: Ссылки (сайт/соцсети)
  D: Подписчики (всего)       E: Позиционирование/УТП  F: Услуги/специализация
  G: Ценовой сегмент          H: Сильные стороны       I: Слабые стороны
  J: ToV и стиль контента     K: ЦА (основной сегмент) L: Активность/частота
  M: Контент-форматы          N: Уровень угрозы (1-10) O: Что можно позаимствовать
  P: Общая оценка/выводы      Q: Валидация             R: Описание

Использование:
  python3 search_competitors.py --client nomos
  python3 search_competitors.py --client nomos --dry-run
"""

import argparse, html, json, os, re, sys, time
from datetime import datetime
from urllib.parse import urlparse

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
from sheets import get_existing, write_results, ensure_headers
from fact_check import verify_competitor
from client_loader import load_client, list_clients

# ── Токены из .env ──
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "")
YT_KEY = os.getenv("YOUTUBE_API_KEY", "")
VK_KEY = os.getenv("VK_API_KEY", "")
APIFY_TOKEN = os.getenv("APIFY_API_TOKEN", "")
OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "")

# ── Настройки ──
VALIDATE_BATCH = os.getenv("VALIDATE_BATCH", "true").lower() == "true"
VALIDATE_BATCH_SIZE = int(os.getenv("VALIDATE_BATCH_SIZE", "25"))
MAX_PER_SOURCE = int(os.getenv("MAX_PER_SOURCE", "15"))  # макс с одного источника
SOCIAL_FIRST = os.getenv("SOCIAL_FIRST", "true").lower() == "true"  # соцсети в приоритете


# ═══════════════════════════════════════════════════
#  Утилиты
# ═══════════════════════════════════════════════════

def fmt_subs(n):
    if not n or n == 0:
        return 0
    if isinstance(n, str):
        try: n = int(n)
        except: return 0
    return n

def classify_source(domain, source_name=""):
    """Определить категорию по домену."""
    if "instagram" in domain:
        return "Instagram — блог/бизнес-аккаунт"
    if "tiktok" in domain:
        return "TikTok — блог/бизнес-аккаунт"
    if "youtube" in domain or "youtu.be" in domain:
        return "YouTube — канал"
    if "vk.com" in domain or "vk.ru" in domain:
        return "VK — сообщество"
    if source_name in ("Instagram", "TikTok", "YouTube", "VK"):
        return f"{source_name} — профиль"
    return "Сайт — прямой конкурент"

def is_relevant(title, desc, cfg):
    include = cfg.get("include_keywords", [])
    exclude = cfg.get("exclude_keywords", [])
    text = f"{title} {desc}".lower()
    for kw in exclude:
        if kw.lower() in text:
            return False
    if not include:
        return True
    return any(w in text for w in include)

def dedup(items, key="name"):
    seen = {}
    for item in items:
        k = item.get(key, "").lower().strip()
        if k not in seen:
            seen[k] = item
    return list(seen.values())

def empty_row():
    """Пустая строка со всеми колонками."""
    return {
        "name": "", "category": "", "links": "", "subscribers": 0,
        "positioning": "", "services": "", "price_segment": "",
        "strengths": "", "weaknesses": "", "tov": "", "audience": "",
        "activity": "", "formats": "", "threat_level": "",
        "borrow": "", "conclusion": "", "validation": "", "description": "",
    }


# ═══════════════════════════════════════════════════
#  ИСТОЧНИК 1 — Instagram (Apify)  ← ПРИОРИТЕТ
# ═══════════════════════════════════════════════════

def from_instagram(cfg, existing_links, existing_names):
    """Поиск Instagram-профилей через Apify Instagram Search Scraper."""
    if not APIFY_TOKEN:
        print("  [Instagram] Нет APIFY_TOKEN — пропускаем")
        return []

    queries = cfg.get("queries_instagram", [])
    if not queries:
        # Генерируем из Brave-запросов
        base = cfg.get("title", "").split("–")[0].strip()
        queries = [f"{base} инстаграм", f"{base} блог", f"{base} услуги"]
        if not queries[0].strip():
            return []

    try:
        from apify_client import ApifyClient
    except ImportError:
        print("  [Instagram] apify-client не установлен")
        return []

    client = ApifyClient(token=APIFY_TOKEN)
    found = []

    for query in queries[:3]:  # не больше 3 запросов — экономим токены
        print(f"  [Instagram] \"{query[:50]}\"", end="", flush=True)
        try:
            run = client.actor("apify/instagram-search-scraper").call(
                run_input={
                    "searchType": "user",
                    "search": query,
                    "resultsLimit": 10,
                    "proxy": {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"]},
                },
                wait_duration=30,
            )
            # Fallback если wait_duration не поддерживается
        except TypeError:
            run = client.actor("apify/instagram-search-scraper").call(
                run_input={
                    "searchType": "user",
                    "search": query,
                    "resultsLimit": 10,
                    "proxy": {"useApifyProxy": True},
                },
            )

        try:
            dataset = client.dataset(run["defaultDatasetId"])
            items = list(dataset.iterate_items())
        except Exception:
            print(" ERR")
            time.sleep(0.5)
            continue

        n = 0
        for item in items:
            username = item.get("username", "")
            full_name = item.get("fullName", "") or username
            followers = item.get("followersCount", 0) or 0
            posts = item.get("postsCount", 0) or 0
            bio = item.get("biography", "") or ""
            is_biz = item.get("isBusinessAccount", False)
            url = f"https://instagram.com/{username}"

            if followers < 50:
                continue
            if full_name.lower().strip() in existing_names:
                continue
            if url.lower().rstrip("/") in existing_links:
                continue
            if not is_relevant(full_name, bio, cfg):
                continue

            cat = "Instagram — бизнес" if is_biz else "Instagram — блогер"
            row = empty_row()
            row.update({
                "name": full_name,
                "category": cat,
                "links": url,
                "subscribers": followers,
                "positioning": bio[:300] if bio else f"Instagram-профиль @{username}",
                "services": "—",
                "price_segment": "—",
                "strengths": f"Instagram. {followers} подписчиков, {posts} постов. {bio[:150]}"[:300],
                "weaknesses": "—",
                "tov": "—",
                "audience": "—",
                "activity": f"~{posts} постов" if posts else "—",
                "formats": "Instagram (посты, stories, reels)",
                "threat_level": min(10, (followers // 5000) + 1) if followers > 1000 else 1,
                "borrow": "—",
                "conclusion": f"Найден Instagram: {query}",
                "validation": "—",
                "description": bio[:300] if bio else "",
            })
            found.append(row)
            existing_links.add(url.lower().rstrip("/"))
            existing_names.add(full_name.lower().strip())
            n += 1
            if n >= MAX_PER_SOURCE:
                break

        print(f" +{n}")
        time.sleep(0.5)

    return found


# ═══════════════════════════════════════════════════
#  ИСТОЧНИК 2 — TikTok (Apify)  ← ПРИОРИТЕТ
# ═══════════════════════════════════════════════════

def from_tiktok(cfg, existing_links, existing_names):
    """Поиск TikTok-профилей через Apify TikTok Scraper."""
    if not APIFY_TOKEN:
        print("  [TikTok] Нет APIFY_TOKEN — пропускаем")
        return []

    queries = cfg.get("queries_tiktok", [])
    if not queries:
        base = cfg.get("title", "").split("–")[0].strip()
        niche = ""
        desc = cfg.get("description", "")
        if "косметолог" in desc.lower(): niche = "косметология"
        elif "недвижимост" in desc.lower(): niche = "недвижимость лондон"
        elif "золот" in desc.lower(): niche = "золото инвестиции"
        queries = [f"{base}", niche] if niche else [base]
        queries = [q for q in queries if q.strip()]
        if not queries:
            return []

    try:
        from apify_client import ApifyClient
    except ImportError:
        print("  [TikTok] apify-client не установлен")
        return []

    client = ApifyClient(token=APIFY_TOKEN)
    found = []

    for query in queries[:2]:
        print(f"  [TikTok] \"{query[:50]}\"", end="", flush=True)
        try:
            run = client.actor("apify/tiktok-scraper").call(
                run_input={
                    "searchQueries": [query],
                    "maxResults": 10,
                    "searchSection": "",
                    "proxyConfig": {"useApifyProxy": True},
                },
            )
        except Exception as e:
            print(f" ERR: {e}")
            time.sleep(0.5)
            continue

        try:
            dataset = client.dataset(run["defaultDatasetId"])
            items = list(dataset.iterate_items())
        except Exception:
            print(" ERR")
            time.sleep(0.5)
            continue

        n = 0
        for item in items:
            # TikTok scraper возвращает разные форматы
            author = item.get("authorMeta", {}) or item.get("author", {}) or {}
            name = author.get("nickName") or author.get("name") or item.get("authorName", "")
            username = author.get("name") or item.get("authorUsername", "")
            followers = int(author.get("fans", 0) or item.get("followerCount", 0) or 0)
            videos = int(author.get("video", 0) or item.get("videoCount", 0) or 0)
            bio = author.get("signature", "") or item.get("description", "") or ""
            url = f"https://tiktok.com/@{username}" if username else ""

            if not name or followers < 50:
                continue
            if name.lower().strip() in existing_names:
                continue
            if url and url.lower().rstrip("/") in existing_links:
                continue
            if not is_relevant(name, bio, cfg):
                continue

            row = empty_row()
            row.update({
                "name": name,
                "category": "TikTok — профиль",
                "links": url,
                "subscribers": followers,
                "positioning": bio[:300] if bio else f"TikTok @{username}",
                "services": "—",
                "price_segment": "—",
                "strengths": f"TikTok. {followers} подписчиков, {videos} видео. {bio[:150]}"[:300],
                "weaknesses": "—",
                "tov": "—",
                "audience": "—",
                "activity": f"~{videos} видео" if videos else "—",
                "formats": "TikTok (короткие видео)",
                "threat_level": min(10, (followers // 5000) + 1) if followers > 1000 else 1,
                "borrow": "—",
                "conclusion": f"Найден TikTok: {query}",
                "validation": "—",
                "description": bio[:300] if bio else "",
            })
            found.append(row)
            existing_links.add(url.lower().rstrip("/"))
            existing_names.add(name.lower().strip())
            n += 1
            if n >= MAX_PER_SOURCE:
                break

        print(f" +{n}")
        time.sleep(0.5)

    return found


# ═══════════════════════════════════════════════════
#  ИСТОЧНИК 3 — YouTube  ← ПРИОРИТЕТ
# ═══════════════════════════════════════════════════

def from_youtube(cfg, existing_links, existing_names):
    """Поиск YouTube-каналов."""
    if not YT_KEY:
        print("  [YouTube] Нет YOUTUBE_API_KEY — пропускаем")
        return []

    queries = cfg.get("queries_youtube", [])
    if not queries:
        return []

    found = []
    for query in queries[:5]:
        print(f"  [YouTube] \"{query[:50]}\"", end="", flush=True)
        try:
            resp = requests.get(
                "https://youtube.googleapis.com/youtube/v3/search",
                params={"part": "snippet", "q": query, "type": "channel",
                        "maxResults": 8, "relevanceLanguage": "ru", "key": YT_KEY},
                timeout=15,
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

                if not is_relevant(title, desc, cfg): continue
                if title.lower() in existing_names: continue
                if c_url.lower() in existing_links: continue

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
                            stats = {"subs": int(st.get("subscriberCount", 0)),
                                     "videos": int(st.get("videoCount", 0))}
                except: pass

                subs = stats.get("subs", 0)
                vids = stats.get("videos", 0)
                row = empty_row()
                row.update({
                    "name": title,
                    "category": "YouTube — канал",
                    "links": c_url,
                    "subscribers": subs,
                    "positioning": desc[:300] if desc else "YouTube-канал",
                    "services": "—",
                    "price_segment": "—",
                    "strengths": f"YouTube. {subs} подписчиков, {vids} видео. {desc[:150]}"[:300],
                    "weaknesses": "—",
                    "tov": "—",
                    "audience": "—",
                    "activity": f"~{vids} видео" if vids else "—",
                    "formats": "YouTube (длинные видео, shorts)",
                    "threat_level": min(10, (subs // 5000) + 1) if subs > 1000 else 1,
                    "borrow": "—",
                    "conclusion": f"Найден YouTube: {query}",
                    "validation": "—",
                    "description": desc[:300] if desc else "",
                })
                found.append(row)
                existing_links.add(c_url.lower())
                existing_names.add(title.lower())
                n += 1
                if n >= MAX_PER_SOURCE: break

            print(f" +{n}")
            time.sleep(0.4)
        except Exception as e:
            print(f" ERR: {e}")

    return found


# ═══════════════════════════════════════════════════
#  ИСТОЧНИК 4 — VK
# ═══════════════════════════════════════════════════

def from_vk(cfg, existing_links, existing_names):
    """Поиск VK-сообществ."""
    if not VK_KEY:
        print("  [VK] Нет VK_API_KEY — пропускаем")
        return []

    queries = cfg.get("queries_vk", [])
    if not queries:
        return []

    found = []
    for query in queries[:4]:
        print(f"  [VK] \"{query[:50]}\"", end="", flush=True)
        try:
            resp = requests.get(
                "https://api.vk.com/method/newsfeed.search",
                params={"q": query, "count": 15, "extended": 1,
                        "access_token": VK_KEY, "v": "5.199"},
                timeout=15,
            )
            if resp.status_code != 200:
                print(f" HTTP {resp.status_code}")
                time.sleep(0.5)
                continue

            data = resp.json()
            if "error" in data:
                print(f" VK: {data['error'].get('error_msg', '?')}")
                time.sleep(0.5)
                continue

            groups = data.get("response", {}).get("groups", [])
            n = 0
            for g in groups:
                name = g.get("name", "").strip()
                screen_name = g.get("screen_name", "")
                url = f"https://vk.com/{screen_name}" if screen_name else f"https://vk.com/club{g['id']}"

                if not name: continue
                if not is_relevant(name, "", cfg): continue
                if name.lower() in existing_names: continue
                if url.lower().rstrip("/") in existing_links: continue

                row = empty_row()
                row.update({
                    "name": name,
                    "category": "VK — сообщество",
                    "links": url,
                    "subscribers": 0,
                    "positioning": "VK-сообщество",
                    "services": "—",
                    "price_segment": "—",
                    "strengths": f"VK-сообщество. Найдено: {query}"[:300],
                    "weaknesses": "—",
                    "tov": "—",
                    "audience": "—",
                    "activity": "—",
                    "formats": "VK (посты, видео, статьи)",
                    "threat_level": 3,
                    "borrow": "—",
                    "conclusion": f"Найден VK: {query}",
                    "validation": "—",
                    "description": "",
                })
                found.append(row)
                existing_links.add(url.lower().rstrip("/"))
                existing_names.add(name.lower())
                n += 1
                if n >= MAX_PER_SOURCE: break

            print(f" +{n}")
            time.sleep(0.5)
        except Exception as e:
            print(f" ERR: {e}")

    return found


# ═══════════════════════════════════════════════════
#  ИСТОЧНИК 5 — Brave Search (сайты — второй план)
# ═══════════════════════════════════════════════════

def from_brave(cfg, existing_links, existing_names):
    """Поиск через Brave Search API."""
    if not BRAVE_API_KEY:
        print("  [Brave] Нет BRAVE_API_KEY — пропускаем")
        return []

    queries = cfg.get("queries_brave", [])
    if not queries:
        return []

    found = []
    for query in queries[:6]:  # ограничиваем — сайты второй план
        print(f"  [Brave] \"{query[:60]}\"", end="", flush=True)
        try:
            resp = requests.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": 8, "country": "RU", "search_lang": "ru"},
                headers={"Accept": "application/json", "Accept-Encoding": "gzip",
                         "X-Subscription-Token": BRAVE_API_KEY},
                timeout=15,
            )
            if resp.status_code != 200:
                print(f" HTTP {resp.status_code}")
                time.sleep(0.5)
                continue

            data = resp.json()
            n = 0
            results = []
            for grp in ("web", "news"):
                for item in data.get(grp, {}).get("results", []):
                    results.append(item)

            for item in results:
                title = html.unescape(item.get("title", "").strip())
                desc = html.unescape(item.get("description") or item.get("snippet") or "").strip()
                link = item.get("url", "")

                if not title or not is_relevant(title, desc, cfg): continue
                if title.lower() in existing_names: continue
                if link.lower().rstrip("/") in existing_links: continue

                domain = urlparse(link).netloc.lower()
                row = empty_row()
                row.update({
                    "name": title,
                    "category": classify_source(domain, "Сайт"),
                    "links": link,
                    "subscribers": 0,
                    "positioning": desc[:300] if desc else "—",
                    "services": "—",
                    "price_segment": "—",
                    "strengths": f"Сайт. {desc[:250]}"[:300],
                    "weaknesses": "—",
                    "tov": "—",
                    "audience": "—",
                    "activity": "—",
                    "formats": "Сайт (лендинг / каталог услуг)",
                    "threat_level": 5,
                    "borrow": "—",
                    "conclusion": f"Найден Brave: {query}",
                    "validation": "—",
                    "description": desc[:300] if desc else "",
                })
                found.append(row)
                existing_links.add(link.lower().rstrip("/"))
                existing_names.add(title.lower())
                n += 1
                if n >= MAX_PER_SOURCE: break

            print(f" +{n}")
            time.sleep(0.3)
        except Exception as e:
            print(f" ERR: {e}")

    return found


# ═══════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Марк1 — Поиск конкурентов (соцсети > сайты)")
    parser.add_argument("--client", "-c", help="Имя профиля клиента")
    parser.add_argument("--dry-run", action="store_true", help="Без записи в Sheets")
    parser.add_argument("--list-clients", action="store_true", help="Список клиентов")
    parser.add_argument("--sheet-id", help="ID таблицы (переопределяет профиль)")
    parser.add_argument("--sheet-tab", default="тест3", help="Название листа (по умолчанию тест3)")
    parser.add_argument("--sources", help="Источники через запятую: instagram,tiktok,youtube,vk,brave")
    args = parser.parse_args()

    if args.list_clients:
        for c in list_clients():
            print(f"  • {c}")
        return

    # ── Загрузка профиля ──
    if args.client:
        cfg = load_client(args.client)
        if "error" in cfg:
            print(f"❌ {cfg['error']}")
            sys.exit(1)
        sheet_id = args.sheet_id or cfg.get("sheet_id") or os.getenv("SHEET_ID", "")
        sheet_tab = args.sheet_tab or cfg.get("sheet_tab") or os.getenv("SHEET_TAB", "тест3")
        client_title = cfg.get("title") or cfg["name"]
        print(f"\n📋 Клиент: {client_title}")
        print(f"   Sheets: {sheet_id}/{sheet_tab}")
    else:
        cfg = {}
        sheet_id = args.sheet_id or os.getenv("SHEET_ID", "1zVNwBX7e8FIZ-0bP7qU2UTbueXrukoev0NbSCS9EwHQ")
        sheet_tab = args.sheet_tab or "тест3"
        client_title = "Конкуренты (legacy)"
        print(f"\n📋 Legacy mode")

    # ── Фильтр источников ──
    if args.sources:
        enabled = [s.strip() for s in args.sources.split(",")]
    else:
        # По умолчанию — соцсети приоритет
        enabled = cfg.get("sources", ["instagram", "tiktok", "youtube", "vk", "brave"])

    print("=" * 60)
    print(f"  {client_title}")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  Источники: {', '.join(enabled)}")
    print(f"  dry-run={args.dry_run}")
    print("=" * 60)

    # 1. Существующие записи
    existing_links, existing_names = set(), set()
    if sheet_id and not args.dry_run:
        try:
            existing_links, existing_names = get_existing(sheet_id, sheet_tab)
            print(f"\n1. Существующих: {len(existing_names)} названий, {len(existing_links)} ссылок")
            ensure_headers(sheet_id, sheet_tab)
        except Exception as e:
            print(f"\n1. Предупреждение Sheets: {e}")

    # 2-6. Сбор (соцсети первые!)
    all_new = []
    step = 2

    source_funcs = {
        "instagram": ("Instagram 📸", lambda: from_instagram(cfg, existing_links, existing_names)),
        "tiktok": ("TikTok 🎵", lambda: from_tiktok(cfg, existing_links, existing_names)),
        "youtube": ("YouTube ▶️", lambda: from_youtube(cfg, existing_links, existing_names)),
        "vk": ("VK 💬", lambda: from_vk(cfg, existing_links, existing_names)),
        "brave": ("Brave (сайты) 🌐", lambda: from_brave(cfg, existing_links, existing_names)),
    }

    for skey in enabled:
        if skey not in source_funcs:
            continue
        sname, sfunc = source_funcs[skey]
        print(f"\n{step}. {sname}...")
        results = sfunc()
        all_new.extend(results)
        print(f"   → {len(results)} новых")
        step += 1

    # Дубли
    total_unique = dedup(all_new)

    # Валидация батчем
    if VALIDATE_BATCH and OPENROUTER_KEY and total_unique:
        from validate_batch import validate_candidates
        print(f"\n{step}. Батч-валидация ({len(total_unique)} кандидатов)...")
        relevant_all, rejected_all = [], []
        for i in range(0, len(total_unique), VALIDATE_BATCH_SIZE):
            batch = total_unique[i:i + VALIDATE_BATCH_SIZE]
            rel, rej = validate_candidates(batch, cfg)
            relevant_all.extend(rel)
            rejected_all.extend(rej)
            print(f"   [{i+1}-{min(i+VALIDATE_BATCH_SIZE, len(total_unique))}] ✅{len(rel)} ❌{len(rej)}")
        total_unique = dedup(relevant_all)
        print(f"\n{'='*60}")
        print(f"  ПОСЛЕ ВАЛИДАЦИИ: {len(total_unique)} релевантных (отсеяно {len(rejected_all)})")
        print(f"{'='*60}")
        if rejected_all:
            print(f"  Отсеяны: {', '.join(r['name'][:40] for r in rejected_all[:10])}")
        step += 1
    else:
        print(f"\n{'='*60}")
        print(f"  ИТОГО: {len(total_unique)} новых")
        print(f"{'='*60}")

    # Запись
    if total_unique and sheet_id and not args.dry_run:
        print(f"\n{step}. Запись в Google Sheets...")
        written = write_results(sheet_id, sheet_tab, total_unique)
        print(f"  ✅ Записано: {written}")
    elif total_unique and args.dry_run:
        print(f"\n  (dry-run) Запись пропущена")
    else:
        print(f"\n  {'✅ Новых не найдено' if not total_unique else '⚠️ Нет SHEET_ID'}")

    # Вывод первых результатов
    if total_unique:
        print(f"\n  Первые 5:")
        for item in total_unique[:5]:
            name = item.get("name", "?")[:50]
            subs = item.get("subscribers", 0)
            cat = item.get("category", "")[:30]
            print(f"    • {name}  [{subs}]  {cat}")


if __name__ == "__main__":
    main()
