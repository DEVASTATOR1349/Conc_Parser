"""
validate_batch.py — Батч-валидация найденных конкурентов через дешёвый LLM.

Используется между поиском и глубоким анализом.
Один вызов API на 20-25 кандидатов → экономия токенов.

Использование:
    from src.validate_batch import validate_candidates
    relevant, rejected = validate_candidates(items, client_config)
"""

import json, os, re, requests
from pathlib import Path

def _load_api_key():
    key = os.getenv("OPENROUTER_API_KEY", "")
    if key:
        return key
    # Fallback: читаем .env
    for env_path in ["/app/.env", ".env"]:
        if Path(env_path).exists():
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("OPENROUTER_API_KEY=") and not line.startswith("#"):
                        return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""

OPENROUTER_KEY = _load_api_key()
API_URL = "https://openrouter.ai/api/v1/chat/completions"
# DeepSeek Flash — самый дешёвый ($0.1/1M токенов), достаточно умный для этой задачи
MODEL = "deepseek/deepseek-v4-flash"

# Кэш для избегания повторов
_cache: dict[str, list] = {}

VALIDATION_PROMPT = """Ты — фильтр конкурентов. Проверь список кандидатов ниже.

{context}

Для КАЖДОГО пункта реши — это РЕАЛЬНЫЙ конкурент или мусор?

РЕАЛЬНЫЙ КОНКУРЕНТ — это:
- Компания/сервис/клиника из той же ниши, что и {company}
- Имеет сайт/профиль с описанием услуг
- Реально существует и может оттянуть клиентов

МУСОР — это:
- Новостные статьи, рейтинги, подборки, обзоры, каталоги
- Агрегаторы без собственных услуг
- Страницы университетов/справочные (для PFP London)
- Банки/брокеры/биржевые сервисы (для Росвекселя)
- Компании из другого города (для Инмедос — только Москва)
- Магазины, курсы, вакансии, форумы

Верни ТОЛЬКО JSON-массив. Каждый элемент:
{{"idx": <номер из списка>, "relevant": true/false, "reason": "<1 предложение — почему>"}}

НЕ пиши markdown, НЕ оборачивай в ```json. Только JSON-массив.

Список:
{items}"""


def validate_candidates(items: list[dict], client_config: dict) -> tuple[list[dict], list[dict]]:
    """
    Батч-валидация списка кандидатов.
    
    Args:
        items: список словарей с полями name, links, positioning
        client_config: профиль клиента (description, название)
    
    Returns:
        (relevant, rejected) — два списка словарей
    """
    if not items or not OPENROUTER_KEY:
        return items, []
    
    company = client_config.get("title", client_config.get("name", "компании"))
    niche_desc = client_config.get("description", "")
    
    # Собираем контекст
    context_parts = [f"Компания: {company}"]
    if niche_desc:
        context_parts.append(f"Ниша: {niche_desc}")
    
    # Добавляем специфичные правила из конфига
    exclude_rules = client_config.get("exclude_keywords", [])
    include_rules = client_config.get("include_keywords", [])
    if exclude_rules:
        context_parts.append(f"Исключить содержащие: {', '.join(exclude_rules[:10])}")
    if include_rules:
        context_parts.append(f"Искать содержащие: {', '.join(include_rules[:10])}")
    
    context = "\n".join(context_parts)
    
    # Формируем список для промпта
    items_text = "\n".join(
        f"{i+1}. {it.get('name', '')[:120]}\n   URL: {it.get('links', '')[:100]}\n   Сниппет: {it.get('positioning', '')[:200]}"
        for i, it in enumerate(items)
    )
    
    prompt = VALIDATION_PROMPT.format(
        context=context,
        company=company,
        items=items_text
    )
    
    try:
        resp = requests.post(
            API_URL,
            headers={
                "Authorization": f"Bearer {OPENROUTER_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 2000,
                "temperature": 0.0,
            },
            timeout=60,
        )
        
        if resp.status_code != 200:
            print(f"    [validate] API error {resp.status_code}: {resp.text[:150]}")
            return items, []
        
        resp_json = resp.json()
        choices = resp_json.get("choices", [])
        if not choices:
            print(f"    [validate] Empty choices: {resp.text[:200]}")
            return items, []
        msg = choices[0].get("message", {})
        text = (msg.get("content") or "").strip()
        if not text:
            print(f"    [validate] Empty content, refusal: {msg.get('refusal','')[:100]}")
            return items, []
        
        # Извлекаем JSON
        json_match = re.search(r'\[.*\]', text, re.DOTALL)
        if not json_match:
            print(f"    [validate] Не нашёл JSON в ответе: {text[:200]}")
            return items, []
        
        results = json.loads(json_match.group(0))
        
        relevant = []
        rejected = []
        
        for r in results:
            idx = r.get("idx", 0) - 1  # 1-based → 0-based
            if 0 <= idx < len(items):
                item = items[idx]
                item["_validation_reason"] = r.get("reason", "")
                if r.get("relevant"):
                    relevant.append(item)
                else:
                    rejected.append(item)
        
        return relevant, rejected
        
    except json.JSONDecodeError as e:
        print(f"    [validate] JSON parse error: {e}")
        return items, []
    except Exception as e:
        print(f"    [validate] Exception: {e}")
        return items, []
