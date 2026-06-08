#!/usr/bin/env python3
"""
post_validate.py — Пост-валидация и ретрай-заполнение недостающих данных.
Вызывается после парсинга, ДО записи в Google Sheets.

Логика:
  1. Проверить КАЖДУЮ строку на критические поля: links, subscribers, weaknesses, tov
  2. Если поле пустое/некорректное — до 2 попыток дозаполнить:
     - subscribers: через VK API / YouTube Data API
     - weaknesses, tov: через AI-обогащение (OpenRouter)
     - links: через повторный поиск VK API
  3. Все поля subscribers проверяются что они числа, а не текст
"""

import os, re, time, json, traceback
import requests as req

# ─── API ключи ───
YT_KEY = os.getenv("YOUTUBE_API_KEY", "")
VK_KEY = os.getenv("VK_API_KEY", "")
OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "")
MAX_RETRIES = 2

# ─── Критические поля и их значения по умолчанию ───
CRITICAL_FIELDS = {
    "links": "",
    "subscribers": 0,
    "weaknesses": "—",
    "tov": "—",
    "strengths": "—",
    "audience": "—",
    "activity": "—",
    "formats": "—",
    "threat_level": 1,
    "borrow": "—",
    "validation": "—",
    "description": "—",
}

# ─── Вспомогательные ───

def is_empty(value):
    """True если поле пустое (None, пустая строка, прочерк, 0 для подписчиков)."""
    if value is None:
        return True
    if isinstance(value, (int, float)):
        return False
    s = str(value).strip()
    return s in ("", "—", "-", "None")


def is_url(value):
    """Проверяет что строка похожа на URL."""
    if not value:
        return False
    v = str(value).strip()
    for part in v.split(","):
        p = part.strip()
        if any(p.lower().startswith(x) for x in ["http://", "https://", "vk.com/", "t.me/",
                                                    "instagram.com/", "youtube.com/", "tiktok.com/"]):
            return True
    return False


def parse_subs_clean(s):
    """Извлечь число подписчиков из строки (рус. форматы) → int."""
    if s is None:
        return 0
    if isinstance(s, (int, float)):
        return int(s)
    s = str(s).strip().replace("\xa0", " ").replace("\u00a0", " ")
    try:
        return int(s)
    except (ValueError, TypeError):
        pass
    # "641 тыс." / "1.26 млн"
    m = re.search(r'([\d\s,.]+)\s*(тыс|млн|млрд|т[ыиею]с|миллион|миллиард|k|K|m|M|b|B)', s)
    if m:
        num = m.group(1).replace(" ", "").replace(",", ".")
        try:
            val = float(num)
            unit = m.group(2).lower()[:3]
            if unit in ("млн", "мил", "mil", "m"):
                return int(val * 1_000_000)
            elif unit in ("тыс", "k"):
                return int(val * 1_000)
            elif unit in ("млр", "bil", "b"):
                return int(val * 1_000_000_000)
        except (ValueError, TypeError):
            pass
    # Просто число вперемешку с текстом
    clean = re.sub(r'[^\d]', '', s)
    if clean:
        return int(clean)
    return 0


def is_vk_row(row):
    """Строка из VK источника."""
    link = str(row.get("links", "") or "")
    cat = str(row.get("category", "") or "")
    return "vk.com/" in link.lower() or "vk" in cat.lower() or "VK" in cat


def is_yt_row(row):
    """Строка из YouTube источника."""
    link = str(row.get("links", "") or "")
    cat = str(row.get("category", "") or "")
    return "youtube.com/" in link.lower() or "youtube" in cat.lower()


def extract_vk_screen_name(link):
    """Извлечь screen_name или clubID из ссылки VK."""
    m = re.search(r'vk\.com/([a-zA-Z0-9_.]+)', str(link or ""))
    return m.group(1) if m else None


def extract_yt_handle_or_id(link):
    """Извлечь handle (@xxx) или channel ID из ссылки YouTube."""
    for pat in [r'youtube\.com/@([a-zA-Z0-9_.\-]+)', r'youtube\.com/channel/([a-zA-Z0-9_\-]+)',
                r'youtube\.com/(c|user)/([a-zA-Z0-9_\-]+)']:
        m = re.search(pat, str(link or ""))
        if m:
            return m.group(2) if m.lastindex >= 2 else m.group(1)
    return None


# ═══════════════════════════════════════════
#  РЕТРАЙ-ФУНКЦИИ
# ═══════════════════════════════════════════

def retry_vk_subscribers(row, attempt=1):
    """Запросить подписчиков VK-сообщества через API. Возвращает int или None."""
    if not VK_KEY:
        return None
    screen_name = extract_vk_screen_name(row.get("links", ""))
    if not screen_name:
        return None
    try:
        resp = req.get("https://api.vk.com/method/groups.getById",
            params={"group_id": screen_name, "fields": "members_count,counters",
                    "access_token": VK_KEY, "v": "5.199"},
            timeout=10)
        data = resp.json()
        groups = (data.get("response", {}) or {}).get("groups", [])
        if isinstance(groups, list) and groups:
            members = int(groups[0].get("members_count", 0) or 0)
            if members == 0:
                members = int(groups[0].get("counters", {}).get("members", 0) or 0)
            if members > 0:
                print(f"    [retry VK subs a#{attempt}] {screen_name} → {members}")
                return members
    except Exception as e:
        print(f"    [retry VK subs a#{attempt}] error: {e}")
    return None


def retry_yt_subscribers(row, attempt=1):
    """Запросить подписчиков YouTube-канала через API. Возвращает int или None."""
    if not YT_KEY:
        return None
    ch = extract_yt_handle_or_id(row.get("links", ""))
    if not ch:
        return None
    try:
        if ch.startswith("@"):
            resp = req.get("https://www.googleapis.com/youtube/v3/channels",
                params={"part": "statistics", "forHandle": ch, "key": YT_KEY}, timeout=10)
        else:
            resp = req.get("https://www.googleapis.com/youtube/v3/channels",
                params={"part": "statistics", "id": ch, "key": YT_KEY}, timeout=10)
        data = resp.json()
        items = data.get("items", [])
        if items:
            subs = int(items[0].get("statistics", {}).get("subscriberCount", 0) or 0)
            if subs > 0:
                print(f"    [retry YT subs a#{attempt}] {ch} → {subs}")
                return subs
    except Exception as e:
        print(f"    [retry YT subs a#{attempt}] error: {e}")
    return None


def retry_ai_fields(rows_to_fix, client_config, attempt=1):
    """
    AI-обогащение для конкретных строк и полей.
    rows_to_fix: [(row_dict, [field_names])]
    Возвращает количество заполненных полей.
    """
    if not OPENROUTER_KEY or not rows_to_fix:
        return 0

    company = client_config.get("title", client_config.get("name", "компании"))
    fixed = 0

    for batch_start in range(0, len(rows_to_fix), 3):  # Бачи по 3 для надёжности
        batch = rows_to_fix[batch_start:batch_start + 3]
        
        items_text = ""
        for row, fields in batch:
            items_text += f"### {row.get('name', '?')[:60]}\n"
            items_text += f"Услуги: {(row.get('services') or '—')[:200]}\n"
            items_text += f"Позиционирование: {(row.get('positioning') or '—')[:200]}\n"
            items_text += f"Сильные стороны: {(row.get('strengths') or '—')[:200]}\n"
            items_text += f"Описание: {(row.get('description') or '—')[:200]}\n"
            items_text += f"Категория: {(row.get('category') or '—')[:100]}\n\n"
        
        fields_str = ", ".join(sorted(set(f for _, fs in batch for f in fs)))
        
        prompt = f"""Ты аналитик конкурентов в нише «{company}». Заполни поля [{fields_str}] для конкурентов.
Пиши кратко (1-3 предложения), на русском, по делу.

{items_text}
Верни JSON-массив: [{{"name": "название", "weaknesses": "...", "tov": "..."}}]
Только JSON, без пояснений."""

        try:
            resp = req.post("https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENROUTER_KEY}", "Content-Type": "application/json"},
                json={
                    "model": "deepseek/deepseek-v4-flash",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.4, "max_tokens": 1500
                },
                timeout=90)
            
            if resp.status_code != 200:
                print(f"    [retry AI a#{attempt}] HTTP {resp.status_code}")
                continue
            
            content = resp.json()["choices"][0]["message"]["content"]
            json_match = re.search(r'\[.*\]', content, re.DOTALL)
            if not json_match:
                continue
            
            data = json.loads(json_match.group(0))
            for item in data:
                name = (item.get("name") or "").lower()[:40]
                for row, fields in batch:
                    row_name = (row.get("name") or "").lower()[:40]
                    if name == row_name:
                        for f in fields:
                            val = item.get(f)
                            if val and not is_empty(val):
                                if f == "threat_level":
                                    try:
                                        row[f] = max(1, min(10, int(float(str(val)))))
                                    except:
                                        row[f] = 3
                                else:
                                    row[f] = str(val)[:300]
                                fixed += 1
                        break
            
            time.sleep(1.5)
        except Exception as e:
            print(f"    [retry AI a#{attempt}] error: {e}")
    
    return fixed


def retry_vk_link(row, attempt=1):
    """Восстановить ссылку VK через поиск по названию."""
    if not VK_KEY:
        return None
    name = row.get("name", "")
    if not name:
        return None
    try:
        resp = req.get("https://api.vk.com/method/newsfeed.search",
            params={"q": name[:50], "count": 3, "extended": 1,
                    "access_token": VK_KEY, "v": "5.199"},
            timeout=10)
        data = resp.json()
        groups = data.get("response", {}).get("groups", [])
        for g in groups:
            gname = (g.get("name") or "").lower()
            if gname[:20] in name.lower() or name.lower()[:20] in gname:
                sid = g.get("screen_name", "")
                gid = g.get("id", 0)
                url = f"https://vk.com/{sid}" if sid else f"https://vk.com/club{gid}"
                print(f"    [retry VK link a#{attempt}] {name[:30]} → {url}")
                return url
    except Exception as e:
        print(f"    [retry VK link a#{attempt}] error: {e}")
    return None


# ═══════════════════════════════════════════
#  ГЛАВНАЯ ФУНКЦИЯ
# ═══════════════════════════════════════════

def validate_and_repair(rows: list[dict], client_config: dict) -> list[dict]:
    """
    Проверить каждую строку и дозаполнить недостающие поля (до 2 попыток).
    
    Возвращает отфильтрованный список (строки без ссылок удаляются
    только если не удалось восстановить ссылку за 2 попытки).
    """
    if not rows:
        return rows

    total = len(rows)
    print(f"\n🔍 Пост-валидация: {total} строк...")

    # ── Шаг 0: Нормализация подписчиков (текст → число) ──
    subs_fixed = 0
    for row in rows:
        raw = row.get("subscribers")
        if raw is not None and isinstance(raw, (int, float)):
            continue
        clean = parse_subs_clean(raw)
        if clean > 0:
            row["subscribers"] = clean
            subs_fixed += 1
    if subs_fixed:
        print(f"  📊 Нормализовано подписчиков: {subs_fixed}")

    # ── Шаг 1: Аудит ──
    issues = {
        "no_link": [],
        "no_subs_vk": [],
        "no_subs_yt": [],
        "text_subs": [],
        "no_weaknesses": [],
        "no_tov": [],
        "no_strengths": [],
        "no_audience": [],
        "no_activity": [],
        "no_formats": [],
        "no_threat": [],
        "no_borrow": [],
        "no_validation": [],
        "no_description": [],
    }

    for row in rows:
        link = str(row.get("links", "") or "")
        subs = row.get("subscribers", 0)
        subs_str = str(subs or "")
        
        if not is_url(link):
            issues["no_link"].append(row)
        
        # Подписчики: 0 или текст
        is_text_subs = False
        if isinstance(subs, str) and subs.strip() and subs.strip() not in ("0", "—", "-"):
            try:
                int(subs)
            except (ValueError, TypeError):
                is_text_subs = True
        if is_text_subs:
            issues["text_subs"].append(row)
        elif isinstance(subs, (int, float)) and int(subs) == 0:
            if is_vk_row(row):
                issues["no_subs_vk"].append(row)
            elif is_yt_row(row):
                issues["no_subs_yt"].append(row)
        elif isinstance(subs, str) and subs.strip() in ("0", "—", "-", ""):
            if is_vk_row(row):
                issues["no_subs_vk"].append(row)
            elif is_yt_row(row):
                issues["no_subs_yt"].append(row)
        
        if is_empty(row.get("weaknesses")):
            issues["no_weaknesses"].append(row)
        if is_empty(row.get("tov")):
            issues["no_tov"].append(row)
        if is_empty(row.get("strengths")):
            issues["no_strengths"].append(row)

    print(f"  🔴 Без ссылок: {len(issues['no_link'])}")
    print(f"  🔴 VK без подписчиков: {len(issues['no_subs_vk'])}")
    print(f"  🔴 YT без подписчиков: {len(issues['no_subs_yt'])}")
    print(f"  🟠 Текстовые подписчики: {len(issues['text_subs'])}")
    print(f"  🟡 Без слабых сторон: {len(issues['no_weaknesses'])}")
    print(f"  🟡 Без ToV: {len(issues['no_tov'])}")
    print(f"  🟡 Без сильных сторон: {len(issues['no_strengths'])}")

    # ── Шаг 2: Ретрай — subscribers (VK + YT) ──
    for attempt in range(1, MAX_RETRIES + 1):
        # VK
        still_no_vk = []
        for row in issues["no_subs_vk"]:
            subs = retry_vk_subscribers(row, attempt)
            if subs:
                row["subscribers"] = subs
            else:
                still_no_vk.append(row)
        issues["no_subs_vk"] = still_no_vk

        # YT
        still_no_yt = []
        for row in issues["no_subs_yt"]:
            subs = retry_yt_subscribers(row, attempt)
            if subs:
                row["subscribers"] = subs
            else:
                still_no_yt.append(row)
        issues["no_subs_yt"] = still_no_yt

        # Текстовые → числа (повторно)
        still_text = []
        for row in issues["text_subs"]:
            clean = parse_subs_clean(row.get("subscribers"))
            if clean > 0:
                row["subscribers"] = clean
            else:
                still_text.append(row)
        issues["text_subs"] = still_text

        if not issues["no_subs_vk"] and not issues["no_subs_yt"] and not issues["text_subs"]:
            break
    
    vk_left = len(issues["no_subs_vk"])
    yt_left = len(issues["no_subs_yt"])
    txt_left = len(issues["text_subs"])
    if vk_left or yt_left or txt_left:
        print(f"  ⚠️ После ретраев: VK={vk_left}, YT={yt_left}, текст={txt_left} остались без подписчиков")

    # ── Шаг 3: Ретрай — links ──
    for attempt in range(1, MAX_RETRIES + 1):
        still_no_link = []
        for row in issues["no_link"]:
            if is_vk_row(row):
                new_link = retry_vk_link(row, attempt)
                if new_link:
                    row["links"] = new_link
                else:
                    still_no_link.append(row)
            else:
                still_no_link.append(row)
        issues["no_link"] = still_no_link
        if not issues["no_link"]:
            break
    
    # Удаляем строки без ссылок (не удалось восстановить)
    rows_to_remove = {id(r) for r in issues["no_link"]}
    if rows_to_remove:
        removed = len(rows_to_remove)
        rows = [r for r in rows if id(r) not in rows_to_remove]
        print(f"  ❌ Удалено без ссылок: {removed}")

    # ── Шаг 4: Ретрай — AI поля (weaknesses, tov, strengths) ──
    # Собираем уникальные строки которым нужен AI
    ai_rows = {}
    for field in ["no_weaknesses", "no_tov", "no_audience", "no_activity",
                  "no_formats", "no_borrow", "no_description"]:
        for row in issues[field]:
            field_name = field.replace("no_", "")
            ai_rows.setdefault(id(row), [row, set()])[1].add(field_name)
    # threat_level handled separately below
    for row in issues["no_threat"]:
        ai_rows.setdefault(id(row), [row, set()])[1].add("threat_level")

    if ai_rows:
        todo = [(row, list(fields)) for row, fields in ai_rows.values()]
        for attempt in range(1, MAX_RETRIES + 1):
            # Filter: only rows that still need enrichment
            still_todo = []
            for row, fields in todo:
                needs = []
                if "weaknesses" in fields and is_empty(row.get("weaknesses")):
                    needs.append("weaknesses")
                if "tov" in fields and is_empty(row.get("tov")):
                    needs.append("tov")
                if needs:
                    still_todo.append((row, needs))
            
            if not still_todo:
                break
            
            n = retry_ai_fields(still_todo, client_config, attempt)
            print(f"    [retry AI a#{attempt}] заполнено полей: {n}")
            todo = still_todo
            if n == 0:
                break

    # ── Финальная статистика ──
    final_stats = {}
    for field in ["links", "weaknesses", "tov", "audience", "activity",
                  "formats", "threat_level", "borrow", "validation", "description"]:
        empty_count = 0
        for r in rows:
            val = r.get(field)
            if field == "links":
                empty_count += int(not is_url(str(val or "")))
            elif field == "threat_level":
                empty_count += int(is_empty(val) or str(val).strip() in ("0",))
            else:
                empty_count += int(is_empty(val))
        final_stats[field] = empty_count
    
    final_no_link = final_stats.get("links", 0)
    final_no_subs = sum(1 for r in rows if int(r.get("subscribers", 0) or 0) == 0 and (is_vk_row(r) or is_yt_row(r)))

    print(f"\n  ✅ Итого: {len(rows)} строк")
    print(f"     без ссылок: {final_no_link}")
    print(f"     VK/YT без подписчиков: {final_no_subs}")
    for field, cnt in final_stats.items():
        if field != "links":
            print(f"     без {field}: {cnt}")
    print()

    return rows
