#!/usr/bin/env python3
"""Запуск обогащения для Кристины. Загружает .env, хардкодит SHEET_ID."""
import os, sys
from pathlib import Path

# Загружаем .env
for p in [Path(__file__).parent / ".env"]:
    if p.exists():
        with open(p) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"): continue
                if "=" not in line: continue
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip()

# Хардкод Кристины
os.environ["SHEET_ID"] = "1hIsSBIP0f7jXAFQZGhAj_0locMKUdb9JKmWM4kjfSLQ"
os.environ["SHEET_TAB"] = "Отчёт по конкурентам"

print(f"OPENROUTER: {os.environ.get('OPENROUTER_API_KEY','')[:10]}...")
print(f"SHEET_ID: {os.environ.get('SHEET_ID')}")
print()

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "src"))

exec(open(Path(__file__).parent / "analyze_competitors.py").read())
