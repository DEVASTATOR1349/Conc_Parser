#!/usr/bin/env python3
"""
client_loader.py — Загрузка профиля клиента из MD-файла.
Позволяет переключаться между проектами: --client nomos / --client polza / --client studio
"""

import os
import re
from typing import Dict, List, Optional


CLIENTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "clients")


def list_clients() -> List[str]:
    """Список доступных клиентов (MD-файлы в clients/)."""
    if not os.path.isdir(CLIENTS_DIR):
        os.makedirs(CLIENTS_DIR, exist_ok=True)
        return []
    return sorted(
        f.replace(".md", "") for f in os.listdir(CLIENTS_DIR) if f.endswith(".md")
    )


def load_client(name: str) -> Dict:
    """
    Парсит MD-файл профиля и возвращает словарь с конфигом.
    Формат MD:
      # Клиент: Название
      ## Описание
      текст...

      ## Поисковые запросы
      queries_brave:
        - query1
        - query2

      queries_youtube:
        - query1

      queries_vk:
        - query1

      ## Фильтры
      include_keywords:
        - слово

      exclude_keywords:
        - слово

      ## Google Sheets
      sheet_id: xxx
      sheet_tab: yyy

      ## Источники
      sources:
        - brave
        - youtube
    """
    path = os.path.join(CLIENTS_DIR, f"{name}.md")
    if not os.path.exists(path):
        return {"error": f"Клиент '{name}' не найден. Доступны: {', '.join(list_clients())}"}

    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

    sections = _split_sections(text)
    config = {"name": name, "title": _extract_title(text)}

    # Парсим секции
    raw_queries = sections.get("Поисковые запросы", "")
    config["queries_brave"] = _parse_list(raw_queries, "queries_brave")
    config["queries_youtube"] = _parse_list(raw_queries, "queries_youtube")
    config["queries_vk"] = _parse_list(raw_queries, "queries_vk")
    config["queries_instagram"] = _parse_list(raw_queries, "queries_instagram")

    raw_filters = sections.get("Фильтры", "")
    config["include_keywords"] = _parse_list(raw_filters, "include_keywords")
    config["exclude_keywords"] = _parse_list(raw_filters, "exclude_keywords")

    raw_sheets = sections.get("Google Sheets", "")
    config["sheet_id"] = _parse_value(raw_sheets, "sheet_id")
    config["sheet_tab"] = _parse_value(raw_sheets, "sheet_tab")

    raw_sources = sections.get("Источники", "")
    config["sources"] = _parse_list(raw_sources, "sources")

    raw_desc = sections.get("Описание", "")
    config["description"] = raw_desc.strip() if raw_desc else ""

    return config


def _split_sections(text: str) -> Dict[str, str]:
    """Разбивает MD на секции по ##."""
    sections = {}
    current_key = None
    current_lines = []

    for line in text.split("\n"):
        if line.startswith("## "):
            if current_key:
                sections[current_key] = "\n".join(current_lines).strip()
            current_key = line[3:].strip()
            current_lines = []
        elif current_key:
            current_lines.append(line)

    if current_key:
        sections[current_key] = "\n".join(current_lines).strip()

    return sections


def _extract_title(text: str) -> str:
    """Извлекает # Клиент: Название."""
    m = re.search(r"^#\s+Клиент:\s+(.+)", text, re.MULTILINE)
    return m.group(1).strip() if m else ""


def _parse_list(text: str, key: str) -> List[str]:
    """Извлекает список YAML-like под ключом."""
    pattern = rf"^{key}\s*:\s*\n((?:\s*-\s+.+\n?)*)"
    m = re.search(pattern, text, re.MULTILINE)
    if not m:
        return []
    return [
        line.strip().lstrip("- ").strip().strip('"')
        for line in m.group(1).split("\n")
        if line.strip() and not line.strip().startswith("#")
    ]


def _parse_value(text: str, key: str) -> Optional[str]:
    """Извлекает scalar-значение после ключа:"""
    pattern = rf"^{key}\s*:\s*(.+)$"
    m = re.search(pattern, text, re.MULTILINE)
    return m.group(1).strip() if m else None
