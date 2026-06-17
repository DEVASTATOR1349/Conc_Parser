#!/usr/bin/env python3
"""
search_competitors.py — Мульти-клиентский поиск конкурентов.
v2 — тиражированный поиск с поддержкой subscriber-tier и больших объёмов.

Приоритет: СОЦСЕТИ > САЙТЫ
  Instagram (Apify) → TikTok (Apify) → YouTube Data API → VK API → Brave Search (сайты)

Tier-режим (--subscriber-tier):
  1 = ≥ 100K подписчиков
  2 = ≥ 50K подписчиков
  3 = ≥ 10K подписчиков
  none = без фильтра

Колонки (18):
  A: Конкурент     B: Категория     C: Ссылки
  D: Подписчики    E: УТП           F: Услуги
  G: Ценовой       H: Сильные       I: Слабые
  J: ToV           K: ЦА            L: Активность
  M: Форматы       N: Угроза        O: Заимствования
  P: Выводы        Q: Валидация     R: Описание
"""

import argparse, html, json, os, re, sys, time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
from sheets import get_existing, write_results, ensure_headers
from fact_check import verify_competitor
from client_loader import load_client, list_clients
from post_validate import validate_and_repair

# ── Загрузка .env если есть ──
script_dir = os.path.dirname(os.path.abspath(__file__))
for env_path in [f"{script_dir}/.env", f"{script_dir}/../.env", "/app/.env"]:
    p = Path(env_path)
    if p.exists():
        with open(p) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip()
                # Не перезаписываем уже установленные переменные
                if not os.getenv(k):
                    os.environ[k] = v

# ── Токены ──
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "")
YT_KEY = os.getenv("YOUTUBE_API_KEY", "")
VK_KEY = os.getenv("VK_API_KEY", "")
APIFY_TOKEN = os.getenv("APIFY_API_TOKEN", "")
OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "")

# ── Настройки ──
VALIDATE_BATCH = os.getenv("VALIDATE_BATCH", "true").lower() == "true"
VALIDATE_BATCH_SIZE = int(os.getenv("VALIDATE_BATCH_SIZE", "25"))


# ═══════════════════════════════════════════════════
#  Утилиты
# ═══════════════════════════════════════════════════

def fmt_subs(n):
    if not n or n == 0:
        return 0
    if isinstance(n, str):
        try:
            n = int(n)
        except:
            return 0
    return n


def classify_source(domain, source_name=""):
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
    exclude = cfg.get("exclude_keywords", [])
    text = f"{title} {desc}".lower()
    for kw in exclude:
        if kw.lower().strip() in text:
            return False
    return True


def dedup(items, key="name"):
    seen = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        k = item.get(key, "").lower().strip()
        if k not in seen:
            seen[k] = item
    return list(seen.values())


def empty_row():
    return {
        "name": "",
        "category": "",
        "links": "",
        "subscribers": 0,
        "positioning": "",
        "services": "",
        "price_segment": "",
        "strengths": "",
        "weaknesses": "",
        "tov": "",
        "audience": "",
        "activity": "",
        "formats": "",
        "threat_level": "",
        "borrow": "",
        "conclusion": "",
        "validation": "",
        "description": "",
    }


def filter_by_tier(rows, tier):
    """
    tier=1: ≥100K, tier=2: ≥50K, tier=3: ≥10K, tier=0/"": без фильтра
    """
    if not rows or not tier:
        return rows
    tier_map = {"1": 100_000, "2": 50_000, "3": 10_000}
    min_subs = tier_map.get(str(tier), 0)
    if not min_subs:
        return rows
    # Определяем верхнюю планку для текущего прохода
    if str(tier) == "1":
        max_subs = 999_999_999
    elif str(tier) == "2":
        max_subs = 99_999
    elif str(tier) == "3":
        max_subs = 49_999
    else:
        return rows

    filtered = []
    for r in rows:
        subs = int(r.get("subscribers", 0) or 0)
        if min_subs <= subs <= max_subs:
            filtered.append(r)
    return filtered


# ═══════════════════════════════════════════════════
#  ИСТОЧНИК 1 — Instagram (Apify)
# ═══════════════════════════════════════════════════

def from_instagram(cfg, existing_links, existing_names, tier=None):
    if not APIFY_TOKEN:
        print("  [Instagram] Нет APIFY_TOKEN — пропускаем")
        return []

    queries = cfg.get("queries_instagram", [])
    if not queries:
        base = cfg.get("title", "").split("–")[0].strip()
        queries = [f"{base} инстаграм", f"{base} блог", f"{base} врач"]
        if not queries[0].strip():
            return []

    try:
        from apify_client import ApifyClient
    except ImportError:
        print("  [Instagram] apify-client не установлен")
        return []

    client = ApifyClient(token=APIFY_TOKEN)
    found = []

    # В tier-режиме берём больше результатов
    results_limit = 20
    max_per_source = 20
    if tier:
        results_limit = 25
        max_per_source = 25

    for query in queries[:8]:  # Больше запросов для массового сбора
        print(f"  [Instagram] \"{query[:50]}\"", end="", flush=True)
        try:
            run = client.actor("apify/instagram-search-scraper").call(
                run_input={
                    "searchType": "user",
                    "search": query,
                    "resultsLimit": results_limit,
                    "proxy": {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"]},
                },
            )
        except Exception:
            try:
                run = client.actor("apify/instagram-search-scraper").call(
                    run_input={
                        "searchType": "user",
                        "search": query,
                        "resultsLimit": results_limit,
                        "proxy": {"useApifyProxy": True},
                    },
                )
            except Exception as e:
                print(f" ERR: {e}")
                time.sleep(1)
                continue

        try:
            ds_id = getattr(run, "default_dataset_id", None)
            dataset = client.dataset(ds_id)
            items = list(dataset.iterate_items())
        except Exception as e:
            print(f" ERR: {e}")
            time.sleep(1)
            continue

        n = 0
        for item in items:
            username = item.get("username", "")
            full_name = item.get("fullName", "") or username
            followers = item.get("followersCount", 0) or 0
            posts = item.get("postsCount", 0) or 0
            bio = item.get("biography", "") or ""
            is_biz = item.get("isBusinessAccount", False)
            is_verified = item.get("isVerified", False)
            external_url = item.get("externalUrl", "") or ""
            category_name = item.get("categoryName", "") or ""
            igtv = item.get("igtvVideoCount", 0) or 0
            highlights = item.get("highlightsCount", 0) or 0
            url = f"https://instagram.com/{username}"

            # Tier-фильтр
            if tier:
                t_min = {"1": 100_000, "2": 50_000, "3": 10_000}.get(str(tier), 0)
                t_max = {"1": 999_999_999, "2": 99_999, "3": 49_999}.get(str(tier), 999_999_999)
                if followers < t_min or followers > t_max:
                    continue

            if followers < 50:
                continue
            if full_name.lower().strip() in existing_names:
                continue
            if url.lower().rstrip("/") in existing_links:
                continue
            if not is_relevant(full_name, bio, cfg):
                continue

            cat = "Instagram — бизнес" if is_biz else "Instagram — блогер"
            if is_verified:
                cat += " ✓"

            services = category_name if category_name else "—"
            if external_url:
                services += f" | Сайт: {external_url}" if services != "—" else f"Сайт: {external_url}"

            price_lower = (bio + " " + full_name).lower()
            if any(w in price_lower for w in ["премиум", "люкс", "vip", "элит", "премиаль", "premium", "luxury"]):
                price = "премиум"
            elif any(w in price_lower for w in ["доступ", "эконом", "бюджет", "недорог", "акци", "скидк", "affordable", "budget"]):
                price = "ниже среднего"
            elif any(w in price_lower for w in ["средн", "medium", "стандарт"]):
                price = "средний"
            else:
                price = "—"

            strengths_parts = [f"Instagram. {followers} подписчиков, {posts} постов"]
            if is_verified:
                strengths_parts.append("✓ верифицирован")
            if category_name:
                strengths_parts.append(category_name)
            if bio:
                strengths_parts.append(bio[:100])
            strengths = ". ".join(strengths_parts)[:300]

            weaknesses = "—"

            tov = ""
            if bio:
                first_line = (bio or "").split(chr(10))[0].strip()
                if len(first_line) > 10:
                    tov = first_line[:150]

            audience_parts = []
            if category_name:
                audience_parts.append(category_name)
            if followers > 50000:
                audience_parts.append("массовая аудитория")
            elif followers > 5000:
                audience_parts.append("средняя ниша")
            else:
                audience_parts.append("нишевая аудитория")
            audience = ", ".join(audience_parts) if audience_parts else "—"

            act_parts = [f"{posts} постов"]
            if igtv:
                act_parts.append(f"{igtv} IGTV")
            if highlights:
                act_parts.append(f"{highlights} highlights")
            activity = ", ".join(act_parts)

            fmts = ["Instagram (посты", "stories", "reels"]
            if igtv:
                fmts.append("IGTV")
            if external_url:
                fmts.append(f"сайт: {external_url}")
            formats = ", ".join(fmts) + ")"

            borrow = "—"
            conclusion = f"@{username}"
            if followers:
                conclusion += f" | {followers} подпис."
            if category_name:
                conclusion += f" | {category_name}"
            if posts:
                conclusion += f" | {posts} постов"
            conclusion = conclusion[:300]

            description = bio[:300] if bio else f"Instagram @{username}"
            if category_name:
                description += f" [{category_name}]"
            description = description[:300]

            row = empty_row()
            row.update({
                "name": full_name,
                "category": cat,
                "_source": "instagram",
                "links": url,
                "subscribers": followers,
                "positioning": bio[:300] if bio else f"Instagram-профиль @{username}",
                "services": services[:200],
                "price_segment": price,
                "strengths": strengths,
                "weaknesses": weaknesses[:200],
                "tov": tov[:150],
                "audience": audience[:150],
                "activity": activity[:150],
                "formats": formats[:200],
                "threat_level": min(10, (followers // 5000) + 1) if followers > 1000 else 1,
                "borrow": borrow[:200],
                "conclusion": conclusion,
                "validation": "—",
                "description": description,
            })
            found.append(row)
            existing_links.add(url.lower().rstrip("/"))
            existing_names.add(full_name.lower().strip())
            n += 1
            if n >= max_per_source:
                break

        print(f" +{n}")
        time.sleep(1)

    return found


# ═══════════════════════════════════════════════════
#  ИСТОЧНИК 2 — TikTok (Apify)
# ═══════════════════════════════════════════════════

def from_tiktok(cfg, existing_links, existing_names, tier=None):
    if not APIFY_TOKEN:
        print("  [TikTok] Нет APIFY_TOKEN — пропускаем")
        return []

    queries = cfg.get("queries_tiktok", [])
    if not queries:
        base = cfg.get("title", "").split("–")[0].strip()
        niche = ""
        desc = cfg.get("description", "")
        if "недвижимост" in desc.lower():
            niche = "недвижимость лондон"
        elif "золот" in desc.lower():
            niche = "золото инвестиции"
        elif "эндокринолог" in desc.lower() or "нутрициолог" in desc.lower():
            niche = "здоровье врач"
        elif "клиник" in desc.lower() or "косметолог" in desc.lower():
            niche = "косметология клиника"
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
    seen_profiles = set()
    found = []

    max_per_source = 20
    if tier:
        max_per_source = 25

    for query in queries[:8]:
        print(f"  [TikTok] \"{query[:50]}\"", end="", flush=True)
        try:
            run = client.actor("clockworks/tiktok-scraper").call(
                run_input={
                    "searchQueries": [query],
                    "maxProfilesPerQuery": max_per_source,
                    "searchSection": "",
                    "proxyCountryCode": "None",
                },
            )
        except Exception as e:
            print(f" ERR: {e}")
            time.sleep(1)
            continue

        try:
            ds_id = getattr(run, "default_dataset_id", None)
            if ds_id is None:
                print(" ERR: no datasetId")
                continue
            dataset = client.dataset(ds_id)
            items = list(dataset.iterate_items())
        except Exception as e:
            print(f" ERR: {e}")
            time.sleep(1)
            continue

        n = 0
        for item in items:
            author = item.get("authorMeta", {})
            if not author:
                continue

            username = author.get("name", "")
            name = author.get("nickName", "") or username
            bio = author.get("signature", "") or ""
            verified = author.get("verified", False)
            profile_url = author.get("profileUrl", "")
            follower_count = int(author.get("fans", 0) or author.get("followers", 0) or 0)

            if not name or not username:
                continue
            if username.lower() in seen_profiles:
                continue
            if name.lower().strip() in existing_names:
                continue

            # Tier-фильтр
            if tier:
                t_min = {"1": 100_000, "2": 50_000, "3": 10_000}.get(str(tier), 0)
                t_max = {"1": 999_999_999, "2": 99_999, "3": 49_999}.get(str(tier), 999_999_999)
                if follower_count < t_min or follower_count > t_max:
                    continue

            link = profile_url.rstrip("/") if profile_url else f"https://tiktok.com/@{username}"
            if link.lower() in existing_links:
                continue
            if not is_relevant(name, bio, cfg):
                continue

            seen_profiles.add(username.lower())

            # Threat level
            if follower_count > 100000:
                threat = min(10, 9)
            elif follower_count > 10000:
                threat = min(10, follower_count // 5000 + 2)
            elif follower_count > 1000:
                threat = 3
            elif follower_count > 100:
                threat = 2
            else:
                threat = 1

            row = empty_row()
            row.update({
                "name": name,
                "category": "TikTok — профиль" + (" ✓" if verified else ""),
                "_source": "tiktok",
                "links": link,
                "subscribers": follower_count,
                "positioning": bio[:300] if bio else f"TikTok @{username}",
                "services": "—",
                "price_segment": "—",
                "strengths": f"TikTok @{username}, {follower_count} подписчиков" + (" ✓" if verified else ""),
                "weaknesses": "—",
                "tov": "—",
                "audience": "—",
                "activity": "—",
                "formats": "TikTok (короткие видео)",
                "threat_level": threat,
                "borrow": "—",
                "conclusion": f"TikTok @{username}, {follower_count} подпис.",
                "validation": "—",
                "description": f"TikTok @{username}. {bio[:200]}",
            })
            found.append(row)
            existing_links.add(link.lower())
            existing_names.add(name.lower().strip())
            n += 1
            if n >= max_per_source:
                break

        print(f" +{n}")
        time.sleep(1)

    return found


# ═══════════════════════════════════════════════════
#  ИСТОЧНИК 3 — YouTube
# ═══════════════════════════════════════════════════

def from_youtube(cfg, existing_links, existing_names, tier=None):
    if not YT_KEY:
        print("  [YouTube] Нет YOUTUBE_API_KEY — пропускаем")
        return []

    queries = cfg.get("queries_youtube", [])
    if not queries:
        return []

    found = []

    # В tier-режиме больше запросов и больше результатов
    max_q = 20 if tier else 10
    max_results = 50 if tier else 25

    for query in queries[:max_q]:
        print(f"  [YouTube] \"{query[:50]}\"", end="", flush=True)
        try:
            resp = requests.get(
                "https://youtube.googleapis.com/youtube/v3/search",
                params={
                    "part": "snippet",
                    "q": query,
                    "type": "channel",
                    "maxResults": max_results,
                    "relevanceLanguage": "ru",
                    "key": YT_KEY,
                },
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
                custom_url = s.get("customUrl", "") or ""
                c_url = f"https://www.youtube.com/channel/{cid}"

                if not is_relevant(title, desc, cfg):
                    continue
                if title.lower() in existing_names:
                    continue
                if c_url.lower() in existing_links:
                    continue

                # Статистика
                subs = 0
                try:
                    sr = requests.get(
                        "https://youtube.googleapis.com/youtube/v3/channels",
                        params={
                            "part": "statistics,snippet,brandingSettings",
                            "id": cid,
                            "key": YT_KEY,
                        },
                        timeout=10,
                    )
                    if sr.status_code == 200:
                        si = sr.json().get("items", [])
                        if si:
                            st = si[0].get("statistics", {})
                            subs = int(st.get("subscriberCount", 0))
                            s2 = si[0].get("snippet", {}) or {}
                            if not desc:
                                desc = s2.get("description", "") or ""
                except:
                    pass

                # Tier-фильтр
                if tier:
                    t_min = {"1": 100_000, "2": 50_000, "3": 10_000}.get(str(tier), 0)
                    t_max = {"1": 999_999_999, "2": 99_999, "3": 49_999}.get(str(tier), 999_999_999)
                    if subs < t_min or subs > t_max:
                        continue

                # threat level
                if subs > 100000:
                    threat = min(10, 9)
                elif subs > 10000:
                    threat = min(10, subs // 5000 + 2)
                elif subs > 1000:
                    threat = 3
                elif subs > 100:
                    threat = 2
                else:
                    threat = 1

                row = empty_row()
                row.update({
                    "name": title,
                    "category": "YouTube — канал",
                    "_source": "youtube",
                    "links": c_url,
                    "subscribers": subs,
                    "positioning": desc[:300] if desc else "YouTube-канал",
                    "services": "—",
                    "price_segment": "—",
                    "strengths": f"YouTube. {subs} подписчиков",
                    "weaknesses": "—",
                    "tov": "—",
                    "audience": "—",
                    "activity": "—",
                    "formats": "YouTube (длинные видео, shorts)",
                    "threat_level": threat,
                    "borrow": "—",
                    "conclusion": f"YouTube: {title}, {subs} подпис.",
                    "validation": "—",
                    "description": desc[:300] if desc else title,
                })
                found.append(row)
                existing_links.add(c_url.lower())
                existing_names.add(title.lower())
                n += 1

            print(f" +{n}")
            time.sleep(0.4)
        except Exception as e:
            print(f" ERR: {e}")

    return found


# ═══════════════════════════════════════════════════
#  ИСТОЧНИК 4 — VK
# ═══════════════════════════════════════════════════

def from_vk(cfg, existing_links, existing_names, tier=None):
    if not VK_KEY:
        print("  [VK] Нет VK_API_KEY — пропускаем")
        return []

    queries = cfg.get("queries_vk", [])
    if not queries:
        return []

    found = []
    max_q = 15 if tier else 10
    max_per_source = 20 if tier else 15

    for query in queries[:max_q]:
        print(f"  [VK] \"{query[:50]}\"", end="", flush=True)
        try:
            resp = requests.get(
                "https://api.vk.com/method/newsfeed.search",
                params={
                    "q": query, "count": 30, "extended": 1,
                    "access_token": VK_KEY, "v": "5.199",
                },
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
            group_ids = [str(g["id"]) for g in groups if g.get("id")]
            group_details = {}
            if group_ids:
                try:
                    dresp = requests.get(
                        "https://api.vk.com/method/groups.getById",
                        params={
                            "group_ids": ",".join(group_ids),
                            "fields": "description,members_count,activity,status,contacts,verified,site,counters",
                            "access_token": VK_KEY, "v": "5.199",
                        },
                        timeout=12,
                    )
                    ddata = dresp.json()
                    for dg in ddata.get("response", {}).get("groups", []):
                        gid = dg.get("id")
                        if gid:
                            group_details[str(gid)] = dg
                except Exception:
                    pass

            n = 0
            for g in groups:
                name = g.get("name", "").strip()
                screen_name = g.get("screen_name", "")
                gid = str(g.get("id", ""))
                url = f"https://vk.com/{screen_name}" if screen_name else f"https://vk.com/club{g['id']}"

                if not name:
                    continue
                if not is_relevant(name, "", cfg):
                    continue

                d = group_details.get(gid, {})
                mc_raw = d.get("members_count", 0)
                members = int(mc_raw) if mc_raw else 0
                if members == 0:
                    cnt = (d.get("counters", {}) or {}).get("members", 0)
                    if cnt:
                        members = int(cnt)

                # Tier-фильтр
                if tier:
                    t_min = {"1": 100_000, "2": 50_000, "3": 10_000}.get(str(tier), 0)
                    t_max = {"1": 999_999_999, "2": 99_999, "3": 49_999}.get(str(tier), 999_999_999)
                    if members < t_min or members > t_max:
                        continue

                if name.lower() in existing_names:
                    continue
                if url.lower().rstrip("/") in existing_links:
                    continue

                threat = 1
                if members > 100000:
                    threat = min(10, 9)
                elif members > 10000:
                    threat = min(10, members // 5000 + 2)
                elif members > 1000:
                    threat = min(10, members // 2000 + 2)
                elif members > 100:
                    threat = 2

                row = empty_row()
                row.update({
                    "name": name,
                    "category": "VK — сообщество",
                    "_source": "vk",
                    "links": url,
                    "subscribers": members,
                    "positioning": "—",
                    "services": "—",
                    "price_segment": "—",
                    "strengths": f"VK-сообщество. {members} подписчиков.",
                    "weaknesses": "—",
                    "tov": "—",
                    "audience": "—",
                    "activity": "—",
                    "formats": "VK (посты, видео, клипы)",
                    "threat_level": threat,
                    "borrow": "—",
                    "conclusion": f"VK: {name}, {members} подпис.",
                    "validation": "—",
                    "description": f"VK-сообщество «{name}»",
                })
                found.append(row)
                existing_links.add(url.lower().rstrip("/"))
                existing_names.add(name.lower())
                n += 1
                if n >= max_per_source:
                    break

            print(f" +{n}")
            time.sleep(0.5)
        except Exception as e:
            print(f" ERR: {e}")

    return found


# ═══════════════════════════════════════════════════
#  ИСТОЧНИК 5 — Brave Search (сайты)
# ═══════════════════════════════════════════════════

def from_brave(cfg, existing_links, existing_names, tier=None):
    if not BRAVE_API_KEY:
        print("  [Brave] Нет BRAVE_API_KEY — пропускаем")
        return []

    queries = cfg.get("queries_brave", [])
    if not queries:
        return []

    found = []
    max_q = 25 if tier else 10
    max_per_source = 20 if tier else 10

    for query in queries[:max_q]:
        print(f"  [Brave] \"{query[:60]}\"", end="", flush=True)
        try:
            resp = requests.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": 10, "country": "RU", "search_lang": "ru"},
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip",
                    "X-Subscription-Token": BRAVE_API_KEY,
                },
                timeout=10,
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
                if not isinstance(item, dict):
                    continue
                title = html.unescape(item.get("title", "").strip())
                desc = html.unescape(item.get("description") or item.get("snippet") or "").strip()
                link = item.get("url", "")
                extra_snippets = item.get("extra_snippets", []) or []
                extra_text = " ".join(extra_snippets)[:200] if extra_snippets else ""

                if not title or not is_relevant(title, desc, cfg):
                    continue
                if title.lower() in existing_names:
                    continue
                if link.lower().rstrip("/") in existing_links:
                    continue

                domain = urlparse(link).netloc.lower()
                full_text = (title + " " + desc + " " + extra_text).lower()

                # threat
                if any(kw in domain for kw in ["prodoctorov", "yell", "zoon", "2gis", "yandex", "google", "otzovik"]):
                    threat = 2
                elif any(kw in full_text for kw in ["клиник", "медицинск", "clinic", "hospital"]):
                    threat = 8
                elif any(kw in full_text for kw in ["косметолог", "cosmetology", "beauty", "салон"]):
                    threat = 6
                else:
                    threat = 4

                row = empty_row()
                row.update({
                    "name": title[:120],
                    "category": classify_source(domain, "Сайт"),
                    "_source": "brave",
                    "links": link[:300],
                    "subscribers": 0,
                    "positioning": desc[:300] if desc else "—",
                    "services": "—",
                    "price_segment": "—",
                    "strengths": f"Сайт {domain}" if desc else f"Сайт {domain}",
                    "weaknesses": "—",
                    "tov": "—",
                    "audience": "—",
                    "activity": "—",
                    "formats": "Сайт (лендинг / каталог)",
                    "threat_level": threat,
                    "borrow": "—",
                    "conclusion": title[:200],
                    "validation": "—",
                    "description": desc[:300] if desc else title[:200],
                })
                found.append(row)
                existing_links.add(link.lower().rstrip("/"))
                existing_names.add(title.lower())
                n += 1
                if n >= max_per_source:
                    break

            print(f" +{n}")
            time.sleep(0.3)
        except Exception as e:
            print(f" ERR: {e}")

    return found


# ═══════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Марк1 — Поиск конкурентов")
    parser.add_argument("--client", "-c", help="Имя профиля клиента")
    parser.add_argument("--dry-run", action="store_true", help="Без записи в Sheets")
    parser.add_argument("--list-clients", action="store_true", help="Список клиентов")
    parser.add_argument("--sheet-id", help="ID таблицы (переопределяет профиль)")
    parser.add_argument("--sheet-tab", default="тест3", help="Название листа")
    parser.add_argument("--sources", help="Источники через запятую")
    parser.add_argument(
        "--subscriber-tier",
        choices=["1", "2", "3", ""],
        default="",
        help="Tier по подписчикам: 1=≥100K, 2=≥50K, 3=≥10K",
    )
    parser.add_argument(
        "--pass-label",
        default="",
        help="Метка прохода для отладки",
    )
    args = parser.parse_args()

    if args.list_clients:
        for c in list_clients():
            print(f"  • {c}")
        return

    # Загрузка профиля
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
        if args.subscriber_tier:
            tier_label = {"1": "≥100K", "2": "≥50K", "3": "≥10K"}.get(args.subscriber_tier, "all")
            print(f"   Tier: {tier_label} подписчиков")
        if args.pass_label:
            print(f"   Pass: {args.pass_label}")
    else:
        cfg = {}
        sheet_id = args.sheet_id or os.getenv("SHEET_ID", "1zVNwBX7e8FIZ-0bP7qU2UTbueXrukoev0NbSCS9EwHQ")
        sheet_tab = args.sheet_tab or "тест3"
        client_title = "Конкуренты (legacy)"
        print(f"\n📋 Legacy mode")

    # Источники
    if args.sources:
        enabled = [s.strip() for s in args.sources.split(",")]
    else:
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
        except Exception as e:
            print(f"\n1. Предупреждение Sheets: {e}")
        ensure_headers(sheet_id, sheet_tab)

    # 2-6. Сбор
    all_new = []
    step = 2

    source_funcs = {
        "instagram": ("Instagram 📸", lambda: from_instagram(cfg, existing_links, existing_names, args.subscriber_tier)),
        "tiktok": ("TikTok 🎵", lambda: from_tiktok(cfg, existing_links, existing_names, args.subscriber_tier)),
        "youtube": ("YouTube ▶️", lambda: from_youtube(cfg, existing_links, existing_names, args.subscriber_tier)),
        "vk": ("VK 💬", lambda: from_vk(cfg, existing_links, existing_names, args.subscriber_tier)),
        "brave": ("Brave (сайты) 🌐", lambda: from_brave(cfg, existing_links, existing_names, args.subscriber_tier)),
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

    # Дубликаты
    total_unique = dedup(all_new)

    # Батч-валидация
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

        # Квоты по источникам
        print("  📦 Проверка квот по источникам...")
        for src_key in ("youtube", "instagram", "tiktok"):
            cnt = sum(1 for r in total_unique if r.get("_source") == src_key)
            print(f"     {src_key}: {cnt}")
        MIN_SOURCE_QUOTA = {"youtube": 15, "instagram": 10, "tiktok": 10}
        for src, min_cnt in MIN_SOURCE_QUOTA.items():
            current = sum(1 for r in total_unique if r.get("_source") == src)
            if current < min_cnt:
                needed = min_cnt - current
                from_src = [r for r in rejected_all if r.get("_source") == src]
                from_src.sort(key=lambda x: x.get("subscribers", 0) or 0, reverse=True)
                rescued = from_src[:needed]
                total_unique.extend(rescued)
                print(f"  📦 Квота {src}: добрано {len(rescued)} из отсеянных → {current + len(rescued)}")
        total_unique = dedup(total_unique)
        print(f"  Итого после квот: {len(total_unique)}")
        step += 1
    else:
        print(f"\n{'='*60}")
        print(f"  ИТОГО: {len(total_unique)} новых")
        print(f"{'='*60}")

    # Пост-валидация
    if total_unique:
        total_unique = validate_and_repair(total_unique, cfg)

    # Лимит
    max_total = cfg.get("max_total")
    if max_total and len(total_unique) > max_total:
        total_unique = total_unique[:max_total]
        print(f"  🎯 Обрезано до {max_total} (max_total из конфига)")

    # Запись
    if total_unique and sheet_id and not args.dry_run:
        print(f"\n{step}. Запись в Google Sheets...")
        now_ts = datetime.now().strftime("%d.%m.%Y %H:%M")
        for r in total_unique:
            r["parsed_at"] = now_ts
        written = write_results(sheet_id, sheet_tab, total_unique)
        print(f"  ✅ Записано: {written}")
    elif total_unique and args.dry_run:
        print(f"\n  (dry-run) Запись пропущена")
    else:
        print(f"\n  {'✅ Новых не найдено' if not total_unique else '⚠️ Нет SHEET_ID'}")

    # Первые 5
    if total_unique:
        print(f"\n  Первые 5:")
        for item in total_unique[:5]:
            name = item.get("name", "?")[:50]
            subs = item.get("subscribers", 0)
            cat = item.get("category", "")[:30]
            print(f"    • {name}  [{subs}]  {cat}")


if __name__ == "__main__":
    main()
