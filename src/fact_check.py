"""
fact_check.py — Трёхуровневый факт-чекинг для верификации конкурентов.

Уровень 1 (быстрый):   бизнес-сигналы / стоп-слова / маркеры статей
Уровень 2 (сайт):     HEAD-запросы к страницам услуг/контактов
Уровень 3 (AI):       LLM-верификация через OpenRouter (только для uncertain)

Использование:
    from src.fact_check import verify_competitor
    verdict, reason = verify_competitor(title, description, url)
    # verdict: "pass" | "fail" | "uncertain"

Результат можно встраивать в поток поиска:
    if verdict == "fail":
        continue  # пропускаем
    if verdict == "uncertain":
        # или пропустить, или пометить для ручной проверки
        item["verified"] = False
"""

import os
import re
import time
from urllib.parse import urlparse

import requests

# ── AI опционально ──
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
AI_MODEL = "google/gemini-2.0-flash-001"
AI_API_URL = "https://openrouter.ai/api/v1/chat/completions"

# ═══════════════════════════════════════════════════════
#  УРОВЕНЬ 1 — Быстрые маркеры (regex, без запросов)
# ═══════════════════════════════════════════════════════

# Явные признаки БИЗНЕСА (оказывает косметологические услуги)
BUSINESS_SIGNALS = [
    # Конкретные услуги с ценами
    r"ботокс\s*(от|\d|цена|₽|руб)",
    r"биоревитализаци[яи]",
    r"контурн[ая]?\s*пластик",
    r"лазерн[ая]?\s*(эпиляци|шлив|омоложен)",
    r"аппаратн[ая]?\s*косметологи",
    r"инъекционн[ая]?\s*косметологи",
    r"плазмолифтинг",
    r"мезотерапи",
    r"фотоомоложен",
    r"RF-лифтинг|рф-лифтинг",
    r"безоперационн[ая]?\s*(лифтинг|омоложен)",
    r"чиск[ау]\s*лиц[ае]",
    r"пилинг\w*\s*(лиц|химическ)",
    r"нитив[аое]?\s*(лиц|подтяжк)",
    r"удален\w*\s*(сосуд|папиллом|родинк)",
    r"пересадк[ау]\s*волос",
    
    # Контакт/запись — признак работающего бизнеса
    r"(запись|записаться)\s*(на|по|онлайн|сегодня)",
    r"(тел|phone|\+7[\s\(]?\d)",
    r"консультаци[яю]?\s*(бесплатн|специалист|врач)",
    
    # Адрес / метро — физическая точка
    r"(метро|м\.)\s*[А-Яа-я]{2,}",
    r"(пр\.|проспект|ул\.|улица|бульвар|шоссе|пер\.)\s",
    
    # Страницы-признаки бизнес-сайта
    r"(прайс|цена|стоимость|услуг|акци)",
]

# Явные признаки НЕ-БИЗНЕСА (статья, магазин, обучение)
NON_BUSINESS_SIGNALS = [
    # Статьи/рейтинги/подборки
    r"топ[\s\-]?\d{1,2}",
    r"рейтинг",
    r"как выбрать",
    r"обзор\s+(лучш|клиник|салон|процедур)",
    r"10 лучших",
    r"подборк[аи]",
    r"гид\s+по",
    r"инструкци",
    r"чек-лист|чеклист|гайд",
    r"что нужно знать",
    
    # Магазины / доставка
    r"интернет-магазин",
    r"купить\s+(ботокс|филлер|препарат|косметик)",
    r"доставк[ау]",
    r"корзин[ау]",
    r"оформить заказ",
    r"товар[аы]",
    
    # Обучение / курсы
    r"(курс|обучени|семинар|вебинар|мастер-класс).*косметологи",
    r"обучени[ея]\s+(косметологи|инъекци)",
    r"повышен\w*\s*квалификаци",
    r"сертификаци[яю].*косметологи",
    
    # Работа / вакансии — не клиника
    r"ваканси",
    r"работ[ау]\s+в\s+косметологи",
    
    # Новости (не бизнес)
    r"новость|новост",
]

# Проверки на SEO-мусор (бессмысленные страницы-агрегаторы)
SEO_GARBAGE = [
    r"ооо.*(медицина|клиник|красота)",
    r"стр\..*\d{1,3}",           # "стр. 1 из 20" — признак списка
    r"page\s*\d+|страниц[ау]\s+\d+",
    r"каталог\s*(клиник|салон|врач)",
]


def level1_check(title: str, desc: str, url: str) -> tuple:
    """
    Быстрая проверка по маркерам.
    Возвращает ("pass", reason) | ("fail", reason) | ("uncertain", reason).
    """
    text = f"{title} {desc}".lower()
    url_lower = url.lower()

    # 1. SEO-мусор → сразу fail
    for pattern in SEO_GARBAGE:
        if re.search(pattern, text) or re.search(pattern, url_lower):
            return "fail", f"SEO-мусор: {pattern}"

    # 2. Явные статьи/рейтинги → fail
    for pattern in NON_BUSINESS_SIGNALS:
        if re.search(pattern, text):
            return "fail", f"не бизнес (статья/магазин): {pattern}"

    # 3. Явные бизнес-сигналы → pass
    for pattern in BUSINESS_SIGNALS:
        if re.search(pattern, text):
            return "pass", f"бизнес-сигнал: {pattern}"

    # 4. Дополнительно: URL содержит признак клиники
    if any(d in url_lower for d in (
        "prodoctorov", "napopravku", "docdoc",
        "zoon", "spr",
    )):
        return "pass", "профиль на отзовике (клиника)"

    # 5. Ничего не поймали → uncertain
    return "uncertain", "нет явных бизнес-сигналов"


# ═══════════════════════════════════════════════════════
#  УРОВЕНЬ 2 — Проверка сайта (HEAD-запросы)
# ═══════════════════════════════════════════════════════

# Страницы, наличие которых говорит о том, что это бизнес-сайт
BUSINESS_PAGES = [
    "/uslugi", "/services", "/price", "/prajs",
    "/about", "/o-kompanii", "/about-us",
    "/kontakty", "/contacts", "/contact",
    "/catalog", "/napravleniya",
]

# Страницы, наличие которых говорит что это НЕ клиника, а магазин/блог
NON_BUSINESS_PAGES = [
    "/shop", "/catalog", "/product", "/cart", "/checkout",
    "/category", "/collection",
    "/blog", "/article", "/news",
]


def level2_check(url: str, timeout: float = 5.0) -> tuple:
    """
    Проверка сайта HEAD-запросами к ключевым страницам.
    Возвращает ("pass", reason) | ("fail", reason) | ("uncertain", reason).

    Ограничение: не больше 8 запросов, каждый с таймаутом 5 сек.
    """
    if not url or not url.startswith("http"):
        return "uncertain", "нет URL для проверки"

    # Нормализуем базовый URL
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    business_found = False
    non_business_found = False

    # Проверяем бизнес-страницы
    for page in BUSINESS_PAGES[:6]:  # не больше 6 запросов
        target = base.rstrip("/") + page
        try:
            r = requests.head(target, timeout=timeout, allow_redirects=True)
            if r.status_code == 200:
                business_found = True
            elif r.status_code in (403, 401, 429):
                # Сервер блокирует HEAD — пробуем GET для малого количества
                try:
                    r2 = requests.get(target, timeout=timeout, allow_redirects=True)
                    if r2.status_code == 200:
                        business_found = True
                except Exception:
                    pass
        except Exception:
            pass

    # Проверяем не-бизнес страницы (магазин/блог)
    for page in NON_BUSINESS_PAGES[:4]:
        target = base.rstrip("/") + page
        try:
            r = requests.head(target, timeout=timeout, allow_redirects=True)
            if r.status_code == 200:
                non_business_found = True
        except Exception:
            pass

    if business_found and not non_business_found:
        return "pass", "сайт: найдены страницы услуг/контактов"

    if non_business_found and not business_found:
        return "fail", "сайт похож на магазин/блог (найдены shop/blog)"

    if business_found and non_business_found:
        return "pass", "сайт: есть и услуги, и магазин (гибрид)"

    return "uncertain", "сайт: ни бизнес, ни магазин-страницы не найдены"


# ═══════════════════════════════════════════════════════
#  УРОВЕНЬ 3 — AI-верификация (через OpenRouter)
# ═══════════════════════════════════════════════════════

# Кэш AI-ответов (чтобы не гонять одно и то же)
_ai_cache: dict[str, str] = {}


def level3_check(title: str, desc: str) -> tuple:
    """
    AI-верификация через OpenRouter.
    Возвращает ("pass", reason) | ("fail", reason).

    Тратит ~50-100 токенов на вызов. Вызывать ТОЛЬКО для uncertain случаев.
    """
    if not OPENROUTER_API_KEY:
        return "uncertain", "AI: нет OPENROUTER_API_KEY"

    # Проверяем кэш
    cache_key = f"{title}|{desc[:100]}".lower()
    if cache_key in _ai_cache:
        verdict = _ai_cache[cache_key]
        return verdict, f"AI (кэш): {verdict[1]}"

    prompt = f"""Определи, является ли этот объект косметологической/медицинской клиникой ИЛИ салоном красоты, оказывающим КОСМЕТОЛОГИЧЕСКИЕ УСЛУГИ населению.

Название: {title}
Описание: {description}

Ответь строго одним словом:
- YES — если это бизнес, который ОКАЗЫВАЕТ косметологические услуги (клиника, салон, кабинет, центр)
- NO — если это статья, интернет-магазин, блог, образовательный курс, новостной портал или что-то другое

Без объяснений. Только YES или NO."""

    try:
        resp = requests.post(
            AI_API_URL,
            json={
                "model": AI_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.0,
                "max_tokens": 10,
            },
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            timeout=15,
        )

        if resp.status_code != 200:
            return "uncertain", f"AI: HTTP {resp.status_code}"

        answer = resp.json()["choices"][0]["message"]["content"].strip().upper()

        if answer == "YES":
            verdict = ("pass", "AI подтвердил: бизнес")
        elif answer == "NO":
            verdict = ("fail", "AI определил: не бизнес")
        else:
            verdict = ("uncertain", f"AI: неоднозначно ({answer})")

        _ai_cache[cache_key] = verdict
        return verdict

    except Exception as e:
        return "uncertain", f"AI: ошибка ({e})"


# ═══════════════════════════════════════════════════════
#  ОРКЕСТРАТОР
# ═══════════════════════════════════════════════════════

def verify_competitor(
    title: str,
    desc: str,
    url: str,
    use_level2: bool = True,
    use_level3: bool = False,
    verbose: bool = False,
) -> tuple:
    """
    Полная проверка конкурента по всем уровням.

    Args:
        title:         Название
        desc:          Описание
        url:           Ссылка на сайт/профиль
        use_level2:    Проверять сайт HEAD-запросами (по умолчанию True)
        use_level3:    Использовать AI для uncertain (по умолчанию False — дёшево)
        verbose:       Печатать этапы проверки

    Returns:
        ("pass", reason)
        ("fail", reason)
        ("uncertain", reason)

    Пример:
        >>> verdict, reason = verify_competitor("Клиника А", "ботокс от 300р", "https://clinic-a.ru")
        >>> print(verdict)  # "pass"
    """
    # --- Уровень 1 ---
    if verbose:
        print(f"    [fact-check] L1: {title[:40]}")

    verdict, reason = level1_check(title, desc, url)

    if verdict != "uncertain":
        if verbose:
            print(f"    [fact-check] L1 -> {verdict} ({reason})")
        return verdict, reason

    # --- Уровень 2 ---
    if use_level2 and url and url.startswith("http"):
        if verbose:
            print(f"    [fact-check] L2: {url[:50]}")

        verdict, reason = level2_check(url)

        if verdict != "uncertain":
            if verbose:
                print(f"    [fact-check] L2 -> {verdict} ({reason})")
            return verdict, reason

    # --- Уровень 3 (AI) ---
    if use_level3:
        if verbose:
            print(f"    [fact-check] L3: AI...")

        verdict, reason = level3_check(title, desc)

        if verbose:
            print(f"    [fact-check] L3 -> {verdict} ({reason})")
        return verdict, reason

    # Если ничего не дало ответа
    if verbose:
        print(f"    [fact-check] -> uncertain")
    return "uncertain", "не удалось верифицировать (режим без AI)"
