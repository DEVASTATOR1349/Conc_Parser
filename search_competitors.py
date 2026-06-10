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
from enrich import enrich_candidates
from post_validate import validate_and_repair

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
        except Exception:
            try:
                run = client.actor("apify/instagram-search-scraper").call(
                    run_input={
                        "searchType": "user",
                        "search": query,
                        "resultsLimit": 10,
                        "proxy": {"useApifyProxy": True},
                    },
                )
            except Exception as e:
                print(f" ERR: {e}")
                time.sleep(0.5)
                continue

        try:
            ds_id = getattr(run, "default_dataset_id", None)
            dataset = client.dataset(ds_id)
            items = list(dataset.iterate_items())
        except Exception as e:
            print(f" ERR: {e}")
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
            is_verified = item.get("isVerified", False)
            external_url = item.get("externalUrl", "") or ""
            category_name = item.get("categoryName", "") or ""
            igtv = item.get("igtvVideoCount", 0) or 0
            highlights = item.get("highlightsCount", 0) or 0
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
            if is_verified:
                cat += " ✓"

            # services: категория из Instagram Business API
            services = category_name if category_name else "—"
            if external_url:
                services += f" | Сайт: {external_url}" if services != "—" else f"Сайт: {external_url}"

            # price_segment: эвристика из bio
            price_lower = (bio + " " + full_name).lower()
            if any(w in price_lower for w in ["премиум","люкс","vip","элит","премиаль","premium","luxury"]):
                price = "премиум"
            elif any(w in price_lower for w in ["доступ","эконом","бюджет","недорог","акци","скидк","affordable","budget"]):
                price = "ниже среднего"
            elif any(w in price_lower for w in ["средн","medium","стандарт"]):
                price = "средний"
            else:
                price = "—"

            # strengths: followers + posts + category
            strengths_parts = [f"Instagram. {followers} подписчиков, {posts} постов"]
            if is_verified: strengths_parts.append("✓ верифицирован")
            if category_name: strengths_parts.append(category_name)
            if bio: strengths_parts.append(bio[:100])
            strengths = ". ".join(strengths_parts)[:300]

            # weaknesses — для enrich.py но даём базовое
            weaknesses = "—"

            # tov: из первых строк bio
            tov = ""
            if bio:
                first_line = (bio or "").split(chr(10))[0].strip()
                if len(first_line) > 10:
                    tov = first_line[:150]

            # audience: эвристика
            audience_parts = []
            if category_name:
                audience_parts.append(category_name)
            if followers > 50000: audience_parts.append("массовая аудитория")
            elif followers > 5000: audience_parts.append("средняя ниша")
            else: audience_parts.append("нишевая аудитория")
            audience = ", ".join(audience_parts)
            if not audience: audience = "—"

            # activity: расширенная
            act_parts = [f"{posts} постов"]
            if igtv: act_parts.append(f"{igtv} IGTV")
            if highlights: act_parts.append(f"{highlights} highlights")
            activity = ", ".join(act_parts)

            # formats
            fmts = ["Instagram (посты", "stories", "reels"]
            if igtv: fmts.append("IGTV")
            if external_url: fmts.append(f"сайт: {external_url}")
            formats = ", ".join(fmts) + ")"

            # borrow — для enrich
            borrow = "—"

            # conclusion
            conclusion = f"@{username}"
            if followers: conclusion += f" | {followers} подпис."
            if category_name: conclusion += f" | {category_name}"
            if posts: conclusion += f" | {posts} постов"
            conclusion = conclusion[:300]

            # description: полное bio для AI-анализа
            description = bio[:300] if bio else f"Instagram @{username}"
            if category_name:
                description += f" [{category_name}]"
            description = description[:300]

            row = empty_row()
            row.update({
                "name": full_name,
                "category": cat,
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
            if n >= MAX_PER_SOURCE:
                break

        print(f" +{n}")
        time.sleep(0.5)

    return found, existing_links, existing_names


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
    seen_profiles = set()
    found = []

    for query in queries[:2]:
        print(f"  [TikTok] \"{query[:50]}\"", end="", flush=True)
        try:
            run = client.actor("clockworks/tiktok-scraper").call(
                run_input={
                    "searchQueries": [query],
                    "maxProfilesPerQuery": 10,
                    "searchSection": "",
                    "proxyCountryCode": "None",
                },
            )
        except Exception as e:
            print(f" ERR: {e}")
            time.sleep(0.5)
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
            time.sleep(0.5)
            continue

        # TikTok search returns VIDEOS. Deduplicate authors into profiles.
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

            if not name or not username:
                continue
            if username.lower() in seen_profiles:
                continue

            play_count = int(item.get("playCount", 0) or 0)
            digg_count = int(item.get("diggCount", 0) or 0)
            share_count = int(item.get("shareCount", 0) or 0)
            comment_count = int(item.get("commentCount", 0) or 0)

            if name.lower().strip() in existing_names:
                continue
            link = profile_url.rstrip("/") if profile_url else f"https://tiktok.com/@{username}"
            if link.lower() in existing_links:
                continue
            if not is_relevant(name, bio, cfg):
                continue

            seen_profiles.add(username.lower())

            # Настоящие подписчики (а не лайки!)
            follower_count = int(author.get("fans", 0) or author.get("followers", 0) or 0)
            following_count = int(author.get("following", 0) or 0)
            video_count_raw = int(author.get("video", 0) or 0)
            if not follower_count and digg_count > 0:
                # Fallback: если фолловеров нет, используем лайки
                follower_count = digg_count

            # Хэштеги из видео
            hashtags = [h.get("name","") for h in item.get("hashtags", []) or []]
            hashtag_str = ", ".join(hashtags[:10]) if hashtags else ""

            # Музыка
            music = item.get("musicMeta", {}) or {}
            music_name = music.get("musicName", "") or ""

            # --- business heuristics ---

            # services
            svc_text = (bio + " " + hashtag_str).lower()
            svc_parts = []
            for kw, svc in [("косметолог", "Косметология"),
                           ("дерматолог", "Дерматология"),
                           ("хирург", "Хирургия"),
                           ("массаж", "Массаж"),
                           ("фитнес", "Фитнес"),
                           ("йог", "Йога"),
                           ("макияж", "Макияж"),
                           ("ногтев", "Ногтевой сервис"),
                           ("бров", "Брови"),
                           ("тату", "Тату"),
                           ("стилист", "Стиль"),
                           ("real estate", "Недвижимость"),
                           ("property", "Недвижимость"),
                           ("doctor", "Медицина"),
                           ("clinic", "Клиника"),
                           ("dental", "Стоматология")]:
                if kw in svc_text and svc not in svc_parts:
                    svc_parts.append(svc)
            services = ", ".join(svc_parts) if svc_parts else "—"

            # price
            price_lower = (bio + " " + name + " " + hashtag_str).lower()
            if any(w in price_lower for w in ["премиум","люкс","vip","элит","premium","luxury"]):
                price = "премиум"
            elif any(w in price_lower for w in ["доступ","эконом","бюджет","недорог","акци","скидк","affordable","budget"]):
                price = "ниже среднего"
            else:
                price = "—"

            # engagement stats
            eng_parts = []
            if follower_count: eng_parts.append(f"{follower_count} подписчиков")
            if play_count: eng_parts.append(f"{play_count} просм.")
            if digg_count: eng_parts.append(f"{digg_count} ❤")
            engagement = " | ".join(eng_parts) if eng_parts else ""

            # strengths
            strength_parts = [f"TikTok @{username}"]
            if follower_count: strength_parts.append(f"{follower_count} подписчиков")
            if engagement: strength_parts.append(engagement)
            if verified: strength_parts.append("✓")
            if bio: strength_parts.append(bio[:80])
            strengths = ". ".join(strength_parts)[:300]

            # tov
            tov = "Короткие видео"
            if hashtag_str: tov += f" | #{hashtag_str[:80]}"
            if music_name: tov += f" | {music_name}"

            # audience
            aud_parts = []
            if follower_count > 100000: aud_parts.append("массовая аудитория")
            elif follower_count > 10000: aud_parts.append("средняя ниша")
            elif follower_count > 0: aud_parts.append("нишевая аудитория")
            if hashtags:
                interesting = [h for h in hashtags if h.lower() in ["косметология","медицина","beauty","skincare","realestate","недвижимость","лондон","london"]]
                if interesting: aud_parts.append(f"интересы: {', '.join(interesting[:4])}")
            audience = ", ".join(aud_parts) if aud_parts else "—"

            # threat (на основе настоящих подписчиков)
            if follower_count > 100000: threat = min(10, 9)
            elif follower_count > 10000: threat = min(10, follower_count // 5000 + 2)
            elif follower_count > 1000: threat = 3
            elif follower_count > 100: threat = 2
            else: threat = 1

            # activity
            act_parts = []
            if video_count_raw: act_parts.append(f"{video_count_raw} видео")
            if engagement: act_parts.append(engagement)
            activity = " | ".join(act_parts) if act_parts else "—"

            # formats
            fmts = "TikTok (короткие видео"
            if music_name: fmts += f", музыка: {music_name}"
            fmts += ")"
            formats = fmts[:200]

            # conclusion
            conclusion = f"TikTok @{username}"
            if follower_count: conclusion += f", {follower_count} подпис."
            if verified: conclusion += " ✓"
            conclusion = conclusion[:300]

            # description
            desc_parts = [f"TikTok @{username}"]
            if engagement: desc_parts.append(engagement)
            desc_parts.append(bio[:120] if bio else "")
            if hashtag_str: desc_parts.append(f"#{hashtag_str[:100]}")
            description = ". ".join(p for p in desc_parts if p)[:300]

            row = empty_row()
            row.update({
                "name": name,
                "category": "TikTok — профиль" + (" ✓" if verified else ""),
                "links": link,
                "subscribers": follower_count,
                "positioning": bio[:300] if bio else f"TikTok @{username}",
                "services": services[:200],
                "price_segment": price,
                "strengths": strengths,
                "weaknesses": "—",
                "tov": tov[:150],
                "audience": audience[:150],
                "activity": activity[:150],
                "formats": formats,
                "threat_level": threat,
                "borrow": "—",
                "conclusion": conclusion,
                "validation": "—",
                "description": description,
            })
            found.append(row)
            existing_links.add(link.lower())
            existing_names.add(name.lower().strip())
            n += 1
            if n >= MAX_PER_SOURCE:
                break

        print(f" +{n}")
        time.sleep(0.5)

    return found, existing_links, existing_names


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
                custom_url = s.get("customUrl", "") or ""
                published_at = s.get("publishedAt", "") or ""
                c_url = f"https://www.youtube.com/channel/{cid}"

                if not is_relevant(title, desc, cfg): continue
                if title.lower() in existing_names: continue
                if c_url.lower() in existing_links: continue

                stats = {}
                topics = []
                try:
                    sr = requests.get(
                        "https://youtube.googleapis.com/youtube/v3/channels",
                        params={"part": "statistics,topicDetails,brandingSettings,snippet",
                                "id": cid, "key": YT_KEY},
                        timeout=10,
                    )
                    if sr.status_code == 200:
                        si = sr.json().get("items", [])
                        if si:
                            st = si[0].get("statistics", {})
                            stats = {"subs": int(st.get("subscriberCount", 0)),
                                     "videos": int(st.get("videoCount", 0)),
                                     "views": int(st.get("viewCount", 0))}
                            td = si[0].get("topicDetails", {}) or {}
                            topics = td.get("topicCategories", [])
                            # Может быть и второй snippet с customUrl
                            s2 = si[0].get("snippet", {}) or {}
                            if not custom_url:
                                custom_url = s2.get("customUrl", "") or ""
                            if not desc:
                                desc = s2.get("description", "") or ""
                            # branding
                            bs = si[0].get("brandingSettings", {}) or {}
                            ch = bs.get("channel", {}) or {}
                            if not desc:
                                desc = ch.get("description", "") or ""
                except: pass

                subs = stats.get("subs", 0)
                vids = stats.get("videos", 0)
                views = stats.get("views", 0)

                # --- extract topic names ---
                topic_names = []
                for t in topics:
                    name = t.split("/")[-1].replace("_", " ")
                    topic_names.append(name)

                # --- heuristics ---

                # services: из топиков
                svc_map = {
                    "health": "Медицина / Здоровье",
                    "medicine": "Медицина",
                    "cosmetology": "Косметология",
                    "beauty": "Бьюти",
                    "lifestyle": "Лайфстайл",
                    "fashion": "Мода",
                    "real estate": "Недвижимость",
                    "property": "Недвижимость",
                    "fitness": "Фитнес",
                    "education": "Образование",
                    "technology": "Технологии",
                    "business": "Бизнес",
                    "finance": "Финансы",
                    "food": "Еда",
                    "travel": "Путешествия",
                }
                svc_found = []
                all_text = (title + " " + desc + " " + " ".join(topic_names)).lower()
                for kw, svc in svc_map.items():
                    if kw in all_text and svc not in svc_found:
                        svc_found.append(svc)
                services = ", ".join(svc_found) if svc_found else "—"

                # price
                price_lower = all_text
                if any(w in price_lower for w in ["премиум","люкс","vip","элит","premium","luxury"]):
                    price = "премиум"
                elif any(w in price_lower for w in ["доступ","эконом","бюджет","недорог","affordable","budget"]):
                    price = "ниже среднего"
                else:
                    price = "—"

                # strengths
                strength_parts = [f"YouTube. {subs} подписчиков, {vids} видео"]
                if views: strength_parts.append(f"{views} просмотров")
                if topic_names: strength_parts.append(", ".join(topic_names[:3]))
                if desc: strength_parts.append(desc[:100])
                strengths = ". ".join(strength_parts)[:300]

                # tov: из описания + формата
                tov = ""
                if desc:
                    tov = desc.split(".")[0][:150]

                # audience: из топиков + размера
                aud_parts = []
                if topic_names:
                    aud_parts.append(", ".join(topic_names[:3]))
                if subs > 100000: aud_parts.append("широкая аудитория")
                elif subs > 10000: aud_parts.append("средняя ниша")
                elif subs > 0: aud_parts.append("нишевая")
                audience = ", ".join(aud_parts) if aud_parts else "—"

                # threat
                if subs > 100000: threat = min(10, 9)
                elif subs > 10000: threat = min(10, subs // 5000 + 2)
                elif subs > 1000: threat = 3
                elif subs > 100: threat = 2
                else: threat = 1

                # activity
                act_parts = [f"{vids} видео"]
                if subs: act_parts.append(f"{subs} подписчиков")
                if views: act_parts.append(f"{views} просмотров")
                activity = " | ".join(act_parts)

                # formats
                fmts = "YouTube (длинные видео, shorts"
                if topic_names:
                    fmts += f" | {', '.join(topic_names[:2])}"
                fmts += ")"
                formats = fmts[:200]

                # conclusion
                conclusion = f"YouTube: {title}"
                if custom_url: conclusion += f" ({custom_url})"
                if subs: conclusion += f" | {subs} подпис."
                if vids: conclusion += f" | {vids} видео"
                conclusion = conclusion[:300]

                # description (для AI)
                description = desc[:300] if desc else title
                if topic_names:
                    description += " | Тематики: " + ", ".join(topic_names[:5])
                description = description[:300]

                row = empty_row()
                row.update({
                    "name": title,
                    "category": "YouTube — канал",
                    "links": c_url,
                    "subscribers": subs,
                    "positioning": desc[:300] if desc else "YouTube-канал",
                    "services": services[:200],
                    "price_segment": price,
                    "strengths": strengths,
                    "weaknesses": "—",
                    "tov": tov[:150],
                    "audience": audience[:150],
                    "activity": activity[:150],
                    "formats": formats,
                    "threat_level": threat,
                    "borrow": "—",
                    "conclusion": conclusion,
                    "validation": "—",
                    "description": description,
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

    return found, existing_links, existing_names


# ═══════════════════════════════════════════════════
#  ИСТОЧНИК 4 — VK
# ═══════════════════════════════════════════════════

def from_vk(cfg, existing_links, existing_names):
    """Поиск VK-сообществ."""
    if not VK_KEY:
        print("  [VK] Нет VK_API_KEY — пропускаем")
        return []

    # Извлекаем целевой город из запросов
    target_cities = set()
    for q in (cfg.get("queries_vk", []) + cfg.get("queries_brave", [])):
        for word in ["Москва", "Москвы", "Moscow", "Санкт-Петербург", "Питер",
                     "Краснодар", "Ростов", "Казань", "Екатеринбург", "Новосибирск",
                     "London", "Лондон"]:
            if word.lower() in q.lower():
                target_cities.add(word)

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
            # Запрашиваем детали групп (описание, подписчики, активность)
            group_ids = [str(g["id"]) for g in groups if g.get("id")]
            group_details = {}
            if group_ids:
                try:
                    dresp = requests.get(
                        "https://api.vk.com/method/groups.getById",
                        params={
                            "group_ids": ",".join(group_ids),
                            "fields": "description,members_count,activity,status,contacts,verified,site,counters",
                            "access_token": VK_KEY, "v": "5.199"
                        },
                        timeout=12
                    )
                    ddata = dresp.json()
                    for dg in ddata.get("response", {}).get("groups", []):
                        gid = dg.get("id")
                        if gid:
                            group_details[str(gid)] = dg
                except Exception:
                    pass  # если не вышло — используем то что есть

            n = 0
            for g in groups:
                name = g.get("name", "").strip()
                screen_name = g.get("screen_name", "")
                gid = str(g.get("id", ""))
                url = f"https://vk.com/{screen_name}" if screen_name else f"https://vk.com/club{g['id']}"

                if not name: continue
                if not is_relevant(name, "", cfg): continue

                # Детали из groups.getById
                d = group_details.get(gid, {})
                mc_raw = d.get("members_count", 0)
                members = 0
                if mc_raw:
                    try:
                        members = int(mc_raw)
                    except (ValueError, TypeError):
                        members = 0
                if members == 0:
                    cnt = (d.get("counters", {}) or {}).get("members", 0)
                    if cnt:
                        try:
                            members = int(cnt)
                        except (ValueError, TypeError):
                            pass
                desc = (d.get("description", "") or "").strip()
                activity = (d.get("activity", "") or "").strip()
                status = (d.get("status", "") or "").strip()
                verified = int(d.get("verified", 0) or 0)
                site = (d.get("site", "") or "").strip()

                # Гео-фильтр: проверяем что группа в целевом городе
                if target_cities:
                    group_text = (name + " " + desc + " " + status + " " + activity).lower()
                    if not any(c.lower() in group_text for c in target_cities):
                        continue
                if name.lower() in existing_names: continue
                if url.lower().rstrip("/") in existing_links: continue

                # ----- собираем контент из постов (wall.get) -----
                gid_int = g.get("id", 0)
                wall_text = ""
                try:
                    wresp = requests.get(
                        "https://api.vk.com/method/wall.get",
                        params={"owner_id": -gid_int, "count": 5, "filter": "owner",
                                "access_token": VK_KEY, "v": "5.199"},
                        timeout=10
                    )
                    wdata = wresp.json()
                    posts = wdata.get("response", {}).get("items", [])
                    if posts:
                        post_texts = []
                        for p in posts[:5]:
                            pt = (p.get("text", "") or "").strip()
                            if pt and len(pt) > 5:
                                post_texts.append(pt)
                        if post_texts:
                            wall_text = " | ".join(post_texts[:3])[:500]
                except Exception:
                    pass

                # ----- заполняем поля -----
                # subscribers
                followers = members

                # positioning: описание + контент постов + статус + категория
                pos_parts = []
                if desc:
                    pos_parts.append(desc[:200])
                if status and status != desc:
                    pos_parts.append("Статус: " + status)
                if wall_text:
                    pos_parts.append("Контент: " + wall_text[:200])
                if activity:
                    pos_parts.append("Категория: " + activity)
                if not pos_parts:
                    pos_parts.append("VK-сообщество «" + name + "»")
                positioning = " ".join(pos_parts)[:300]

                # services: activity + status + keyword scan from description & posts
                svc_parts = [x for x in [activity, status] if x and len(x) > 2]
                svc_text = (desc + " " + name + " " + wall_text).lower()
                for kw, svc in [
                    ("косметолог", "Косметология"), ("дерматолог", "Дерматология"),
                    ("хирург", "Хирургия"), ("пластическ", "Пластическая хирургия"),
                    ("массаж", "Массаж"), ("эпиляци", "Лазерная эпиляция"),
                    ("инъекци", "Инъекционная косметология"), ("ботокс", "Ботокс"),
                    ("контурн", "Контурная пластика"), ("биоревитализ", "Биоревитализация"),
                    ("трихолог", "Трихология"), ("стоматолог", "Стоматология"),
                    ("ногтев", "Ногтевой сервис"), ("маникюр", "Ногтевой сервис"),
                    ("бров", "Брови"), ("ресниц", "Ресницы"),
                    ("тату", "Татуаж"), ("эстетическ", "Эстетическая медицина"),
                    ("клиник", "Медицинская клиника"), ("медцентр", "Медицинский центр"),
                    ("лазерн", "Лазерные процедуры"), ("омоложен", "Омоложение"),
                    ("салон красот", "Салон красоты"), ("spa", "СПА"), ("спа", "СПА"),
                    ("премиум", "Премиум-услуги"), ("фитнес", "Фитнес"),
                ]:
                    if kw in svc_text and svc not in svc_parts:
                        svc_parts.append(svc)
                services = "; ".join(svc_parts[:6]) if svc_parts else "—"

                # price_segment: эвристика
                price_text = (desc + " " + status + " " + name + " " + wall_text).lower()
                if any(w in price_text for w in ["премиум","люкс","vip","элит","премиаль","premium","luxury"]):
                    price = "премиум"
                elif any(w in price_text for w in ["доступ","эконом","бюджет","низк","дешев","акци","скидк","affordable","budget"]):
                    price = "ниже среднего"
                elif any(w in price_text for w in ["средн","medium","стандарт"]):
                    price = "средний"
                else:
                    price = "—"

                # strengths: followers + verified + content
                str_parts = ["VK-сообщество. " + str(followers) + " подписчиков."]
                if verified: str_parts.append("Верифицировано")
                if activity: str_parts.append("Категория: " + activity)
                if wall_text: str_parts.append("Контент: " + wall_text[:120])
                elif desc: str_parts.append(desc[:120])
                strengths = " ".join(str_parts)[:300]

                # threat based on followers
                if followers > 100000: threat = min(10, 9)
                elif followers > 10000: threat = min(10, followers // 5000 + 2)
                elif followers > 1000: threat = min(10, followers // 2000 + 2)
                elif followers > 100: threat = 2
                else: threat = 1

                # activity: followers + category + content freshness
                act_parts = []
                if followers: act_parts.append(str(followers) + " подписчиков")
                if activity: act_parts.append("категория: " + activity)
                if wall_text: act_parts.append("активный контент")
                activity_str = " | ".join(act_parts) if act_parts else "—"

                # formats
                formats = "VK (посты, видео, статьи, клипы"
                if site:
                    formats += f", сайт: {site}"
                formats += ")"

                # description: описание + посты + статус для AI-обогащения
                desc_parts = []
                if desc: desc_parts.append(desc[:200])
                if wall_text: desc_parts.append("Посты: " + wall_text[:200])
                if status and status not in (desc or ""): desc_parts.append("Статус: " + status)
                if activity: desc_parts.append("Категория: " + activity)
                if not desc_parts: desc_parts.append("VK-сообщество «" + name + "»")
                full_desc = " | ".join(desc_parts)[:300]

                # conclusion
                conc_parts = [name[:100]]
                if followers: conc_parts.append(str(followers) + " подпис.")
                if status: conc_parts.append(status[:80])
                conclusion = " | ".join(conc_parts)[:300]

                row = empty_row()
                row.update({
                    "name": name,
                    "category": "VK — сообщество" + (" ✓" if verified else ""),
                    "links": url,
                    "subscribers": followers,
                    "positioning": positioning[:300],
                    "services": services[:200],
                    "price_segment": price,
                    "strengths": strengths,
                    "weaknesses": "—",
                    "tov": wall_text[:150] if wall_text else (status[:150] if status else "—"),
                    "audience": "—",
                    "activity": activity_str[:200],
                    "formats": formats[:200],
                    "threat_level": threat,
                    "borrow": "—",
                    "conclusion": conclusion[:300],
                    "validation": "—",
                    "description": full_desc[:300],
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

    return found, existing_links, existing_names


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
                # Extra snippets могут содержать доп. информацию
                extra_snippets = item.get("extra_snippets", []) or []
                extra_text = " ".join(extra_snippets)[:200] if extra_snippets else ""

                if not title or not is_relevant(title, desc, cfg): continue
                if title.lower() in existing_names: continue
                if link.lower().rstrip("/") in existing_links: continue

                domain = urlparse(link).netloc.lower()
                full_text = (title + " " + desc + " " + extra_text).lower()

                # --- эвристики из текста ---

                # services: по ключевым словам
                svc_map = {
                    "клиник": "Медицинская клиника",
                    "медицинск": "Медицинские услуги",
                    "косметолог": "Косметология",
                    "стоматолог": "Стоматология",
                    "дерматолог": "Дерматология",
                    "хирург": "Хирургия",
                    "массаж": "Массаж",
                    "спа": "СПА",
                    "фитнес": "Фитнес",
                    "йог": "Йога",
                    "тату": "Тату",
                    "барбер": "Барбершоп",
                    "салон красот": "Салон красоты",
                    "ногтев": "Ногтевой сервис",
                    "бров": "Брови / Ресницы",
                    "real estate": "Недвижимость",
                    "estate agent": "Агентство недвижимости",
                    "letting": "Аренда недвижимости",
                    "property": "Недвижимость",
                    "дентал": "Стоматология",
                    "аптек": "Аптека",
                    "пластическ": "Пластическая хирургия",
                    "инъекци": "Инъекционная косметология",
                    "эпиляц": "Лазерная эпиляция",
                    "трихолог": "Трихология",
                    "лаборатор": "Лаборатория",
                    "диагностик": "Диагностика",
                }
                svc_found = []
                for kw, svc in svc_map.items():
                    if kw in full_text and svc not in svc_found:
                        svc_found.append(svc)
                services = ", ".join(svc_found[:5]) if svc_found else "—"

                # price
                price_lower = full_text
                if any(w in price_lower for w in ["премиум","люкс","vip","элит","премиаль","premium","luxury","эксклюзив"]):
                    price = "премиум"
                elif any(w in price_lower for w in ["доступ","эконом","бюджет","недорог","акци","скидк","affordable","budget","дешёв"]):
                    price = "ниже среднего"
                elif any(w in price_lower for w in ["средн","medium","стандарт"]):
                    price = "средний"
                else:
                    price = "—"

                # strengths
                str_parts = []
                str_parts.append(f"Сайт {domain}")
                if svc_found: str_parts.append(", ".join(svc_found[:2]))
                if desc: str_parts.append(desc[:150])
                strengths = ". ".join(str_parts)[:300]

                # audience
                aud = []
                if svc_found: aud.append(svc_found[0])
                if "москв" in full_text: aud.append("Москва")
                elif "петербург" in full_text or "спб" in full_text: aud.append("Санкт-Петербург")
                elif "london" in full_text: aud.append("Лондон")
                audience = ", ".join(aud) if aud else "—"

                # threat: по типу сайта
                if any(kw in domain for kw in ["prodoctorov","yell","zoon","2gis","yandex","google","otzovik"]):
                    threat = 2  # агрегатор/каталог — не прямой конкурент
                elif any(kw in full_text for kw in ["клиник","медицинск","clinic","hospital"]):
                    threat = 8  # другой клиники сайт — прямой конкурент
                elif any(kw in full_text for kw in ["косметолог","cosmetology","beauty","салон"]):
                    threat = 6
                elif any(kw in full_text for kw in ["агентств","недвижимост","estate agent","real estate"]):
                    threat = 7
                else:
                    threat = 4

                # formats
                if any(kw in domain for kw in ["prodoctorov","yell","zoon","2gis","otzovik"]):
                    fmts = "Агрегатор/каталог (отзывы, рейтинг)"
                elif any(kw in full_text for kw in ["блог","blog","стат","journal"]):
                    fmts = "Сайт (блог / статьи)"
                elif any(kw in full_text for kw in ["shop","магазин","куп","заказ","cart"]):
                    fmts = "Интернет-магазин"
                elif any(kw in full_text for kw in ["клиник","clinic","hospital","медицинск"]):
                    fmts = "Сайт клиники (услуги, запись, цены)"
                else:
                    fmts = "Сайт (лендинг / каталог услуг)"
                formats = fmts[:200]

                # conclusion
                conc = f"{title}"
                if svc_found: conc += f" | {svc_found[0]}"
                if price != "—": conc += f" | {price}"
                conclusion = conc[:300]

                # description
                desc_full = desc[:300] if desc else title
                if extra_text: desc_full += " " + extra_text[:150]
                description = desc_full[:300]

                row = empty_row()
                row.update({
                    "name": title,
                    "category": classify_source(domain, "Сайт"),
                    "links": link,
                    "subscribers": 0,
                    "positioning": desc[:300] if desc else "—",
                    "services": services[:200],
                    "price_segment": price,
                    "strengths": strengths,
                    "weaknesses": "—",
                    "tov": "—",
                    "audience": audience[:150],
                    "activity": "—",
                    "formats": formats,
                    "threat_level": threat,
                    "borrow": "—",
                    "conclusion": conclusion,
                    "validation": "—",
                    "description": description,
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

    return found, existing_links, existing_names


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
        except Exception as e:
            print(f"\n1. Предупреждение Sheets: {e}")
        # Всегда проверяем/создаём заголовки
        ensure_headers(sheet_id, sheet_tab)

    # 2-6. Сбор (соцсети первые!)
    all_new = []
    step = 2

    source_funcs = {
        "instagram": ("Instagram 📸", lambda: from_instagram(cfg, existing_links, existing_names)),
        "tiktok": ("TikTok 🎵", lambda: from_tiktok(cfg, existing_links, existing_names)),
        "youtube": ("YouTube ▶️", lambda: from_youtube(cfg, existing_links, existing_names)),
        "vk": ("VK 💬", lambda: from_vk(cfg, existing_links, existing_names)[0]),
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

    # Обогащение через AI
    if OPENROUTER_KEY and total_unique:
        from enrich import enrich_candidates
        print(f"\n{step}. AI-обогащение ({len(total_unique)} конкурентов)...")
        total_unique = enrich_candidates(total_unique, cfg)
        step += 1

    # 🔍 Пост-валидация: проверка и дозаполнение недостающих данных (до 2 попыток)
    if total_unique:
        total_unique = validate_and_repair(total_unique, cfg)

    # Запись
    if total_unique and sheet_id and not args.dry_run:
        print(f"\n{step}. Запись в Google Sheets...")
        # Stamp parse time
        now_ts = datetime.now().strftime("%d.%m.%Y %H:%M")
        for r in total_unique:
            r["parsed_at"] = now_ts
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
