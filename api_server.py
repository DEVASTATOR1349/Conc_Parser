#!/usr/bin/env python3
"""
api_server.py — FastAPI REST API для Марк1.

Эндпоинты:
  GET  /                    — веб-интерфейс (SPA)
  GET  /api/status          — статус сервера
  GET  /api/clients         — список MD-профилей
  POST /api/clients/upload  — загрузить .md профиль
  DELETE /api/clients/{name} — удалить профиль
  GET  /api/clients/{name}  — содержимое профиля
  POST /api/search          — запустить поиск конкурентов
  POST /api/analyze         — запустить анализ (глубокий)
  GET  /api/logs            — список логов
  GET  /api/logs/{filename} — содержимое лога
  GET  /api/events          — SSE-стрим логов в реальном времени
  GET  /api/sheets/test     — проверить доступ к таблице
  POST /api/config          — обновить .env переменные

Переменные окружения:
  PORT — порт (по умолчанию 8888)
  OPENROUTER_API_KEY — ключ для AI-валидации/анализа
  BRAVE_API_KEY, YOUTUBE_API_KEY, VK_API_KEY, APIFY_API_TOKEN — источники
  SHEET_ID, SHEET_TAB — Google Sheets по умолчанию
"""

import asyncio
import json
import os
import re
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

# Добавляем src/ в путь
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

# ── FastAPI ──
from fastapi import FastAPI, HTTPException, Query, UploadFile, File, Form, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    FileResponse,
    StreamingResponse,
    RedirectResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# ── Константы ──
APP_DIR = Path("/app")
CLIENTS_DIR = APP_DIR / "clients"
LOGS_DIR = APP_DIR / "logs"
CLIENTS_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# ── Глобальное состояние ──
app = FastAPI(title="Марк1 — Конкурентный анализ", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# SSE-подписчики
_sse_subscribers: list[asyncio.Queue] = []

STATUS = {
    "running": False,
    "current_job": None,
    "current_client": None,
    "started_at": None,
    "last_search": None,
    "last_analyze": None,
    "search_count": 0,
    "errors": [],
}


def broadcast_event(event: str, data: dict):
    """Отправить SSE-событие всем подписчикам."""
    msg = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
    dead = []
    for q in _sse_subscribers:
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            dead.append(q)
    for d in dead:
        _sse_subscribers.remove(d)


def get_client_list():
    """Список доступных MD-профилей."""
    return sorted(
        f.stem for f in CLIENTS_DIR.glob("*.md")
    )


def load_client_config(name: str) -> dict:
    """Загрузить конфиг клиента."""
    try:
        from client_loader import load_client
        return load_client(name)
    except Exception:
        # Fallback: парсим вручную
        path = CLIENTS_DIR / f"{name}.md"
        if not path.exists():
            return {"error": f"Клиент '{name}' не найден"}
        return {"name": name, "title": name, "_raw": path.read_text(encoding="utf-8")}


# ═══════════════════════════════════════════════════
#  API — Статика / Главная
# ═══════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def index():
    """Веб-интерфейс Марк1."""
    return HTMLResponse(content=INDEX_HTML, status_code=200)


@app.get("/health")
async def health():
    return {"status": "alive", "time": datetime.now().isoformat()}


# ═══════════════════════════════════════════════════
#  API — Статус
# ═══════════════════════════════════════════════════

@app.get("/api/status")
async def api_status():
    return {
        **STATUS,
        "clients": get_client_list(),
        "uptime_seconds": (
            time.time() - os.stat("/proc/1/cmdline").st_mtime
        ) if os.path.exists("/proc/1/cmdline") else None,
    }


# ═══════════════════════════════════════════════════
#  API — Клиенты (MD-профили)
# ═══════════════════════════════════════════════════

@app.get("/api/clients")
async def list_clients():
    clients = get_client_list()
    result = []
    for name in clients:
        cfg = load_client_config(name)
        result.append({
            "name": name,
            "title": cfg.get("title", name),
            "description": cfg.get("description", "")[:200],
            "sources": cfg.get("sources", []),
            "sheet_id": cfg.get("sheet_id", ""),
            "sheet_tab": cfg.get("sheet_tab", ""),
            "size_bytes": (CLIENTS_DIR / f"{name}.md").stat().st_size,
        })
    return {"clients": result}


@app.get("/api/clients/{name}")
async def get_client(name: str):
    path = CLIENTS_DIR / f"{name}.md"
    if not path.exists():
        raise HTTPException(404, f"Клиент '{name}' не найден")
    return {
        "name": name,
        "content": path.read_text(encoding="utf-8"),
        "config": load_client_config(name),
    }


@app.post("/api/clients/upload")
async def upload_client(file: UploadFile = File(...)):
    """Загрузка MD-профиля клиента."""
    if not file.filename or not file.filename.endswith(".md"):
        raise HTTPException(400, "Только .md файлы")

    content = await file.read()
    text = content.decode("utf-8")

    # Извлекаем имя из заголовка
    name_match = re.search(r"^#\s+Клиент:\s+(.+)", text, re.MULTILINE)
    if name_match:
        safe_name = name_match.group(1).strip().lower().replace(" ", "_").replace("/", "-")
    else:
        safe_name = file.filename.replace(".md", "").replace(" ", "_")

    # Сохраняем
    dest = CLIENTS_DIR / f"{safe_name}.md"
    dest.write_bytes(content)

    broadcast_event("message", {
        "type": "client_uploaded",
        "name": safe_name,
        "time": datetime.now().isoformat(),
    })

    return {
        "success": True,
        "name": safe_name,
        "size_bytes": len(content),
        "clients": get_client_list(),
    }


@app.delete("/api/clients/{name}")
async def delete_client(name: str):
    path = CLIENTS_DIR / f"{name}.md"
    if not path.exists():
        raise HTTPException(404, f"Клиент '{name}' не найден")
    path.unlink()
    broadcast_event("message", {
        "type": "client_deleted",
        "name": name,
        "time": datetime.now().isoformat(),
    })
    return {"success": True, "name": name}


# ═══════════════════════════════════════════════════
#  API — Поиск / Анализ
# ═══════════════════════════════════════════════════

async def run_script(script: str, client: str = "", timeout: int = 300, extra_args: dict = None) -> dict:
    """Запустить Python-скрипт и вернуть результат."""
    STATUS["running"] = True
    STATUS["current_client"] = client or "legacy"
    STATUS["current_job"] = script
    STATUS["started_at"] = datetime.now().isoformat()
    STATUS["errors"] = []

    cmd = ["python3", str(APP_DIR / script)]
    if client:
        cmd.extend(["--client", client])
    if extra_args:
        for k, v in extra_args.items():
            if v:
                cmd.append(f"--{k.replace('_', '-')}")
                cmd.append(str(v))

    broadcast_event("job_start", {
        "script": script,
        "client": client,
        "time": STATUS["started_at"].isoformat(),
    })

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Читаем вывод построчно и транслируем
        stdout_lines = []
        stderr_lines = []

        async def read_stream(stream, lines_list, prefix):
            while True:
                line = await stream.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                lines_list.append(text)
                broadcast_event("log", {
                    "stream": prefix,
                    "text": text,
                    "time": datetime.now().isoformat(),
                })

        await asyncio.wait_for(
            asyncio.gather(
                read_stream(proc.stdout, stdout_lines, "stdout"),
                read_stream(proc.stderr, stderr_lines, "stderr"),
            ),
            timeout=timeout,
        )

        await proc.wait()
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        broadcast_event("job_error", {"error": f"timeout ({timeout}s)"})
        return {"success": False, "error": f"Превышен таймаут ({timeout}с)", "total": 0}
    except Exception as e:
        broadcast_event("job_error", {"error": str(e)})
        return {"success": False, "error": str(e), "total": 0}

    # Парсим результат
    total = 0
    for line in stdout_lines:
        if "ПОСЛЕ ВАЛИДАЦИИ" in line or "ИТОГО" in line:
            nums = re.findall(r"\d+", line)
            if nums:
                total = int(nums[-1])

    stdout_all = "\n".join(stdout_lines[-100:])
    stderr_all = "\n".join(stderr_lines[-50:])

    # Сохраняем лог в файл
    log_name = f"{client or 'legacy'}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{script}.log"
    LOGS_DIR.joinpath(log_name).write_text(
        stdout_all + "\n\n--- STDERR ---\n" + stderr_all,
        encoding="utf-8",
    )

    STATUS["running"] = False
    STATUS["current_job"] = None
    STATUS["last_search" if "search" in script else "last_analyze"] = datetime.now().isoformat()
    if "search" in script:
        STATUS["search_count"] += 1

    broadcast_event("job_done", {
        "script": script,
        "client": client,
        "total": total,
        "success": proc.returncode == 0,
        "log_file": log_name,
        "time": datetime.now().isoformat(),
    })

    return {
        "success": proc.returncode == 0,
        "total": total,
        "client": client or "legacy",
        "stdout_tail": stdout_all[-2000:],
        "stderr": stderr_all[-1000:],
        "log_file": log_name,
    }


@app.post("/api/search")
async def search(
    client: str = Query("", description="Имя профиля клиента"),
    sources: str = Query("", description="Источники: instagram,tiktok,youtube,vk,brave"),
    sheet_id: str = Query("", description="ID Google таблицы"),
    sheet_tab: str = Query("тест3", description="Название листа"),
    timeout: int = Query(300, ge=30, le=900, description="Таймаут в секундах"),
):
    """Запустить поиск конкурентов."""
    if STATUS["running"]:
        raise HTTPException(409, f"Уже выполняется: {STATUS['current_job']}")

    if sources:
        os.environ["SEARCH_SOURCES"] = sources

    result = await run_script("search_competitors.py", client, timeout,
                              extra_args={"sheet_id": sheet_id, "sheet_tab": sheet_tab, "sources": sources})
    return result


@app.post("/api/analyze")
async def analyze(
    client: str = Query("", description="Имя профиля клиента"),
    timeout: int = Query(600, ge=60, le=1800, description="Таймаут в секундах"),
):
    """Запустить глубокий анализ конкурентов (AI)."""
    if STATUS["running"]:
        raise HTTPException(409, f"Уже выполняется: {STATUS['current_job']}")

    result = await run_script("analyze_competitors.py", client, timeout)
    return result


@app.post("/api/cancel")
async def cancel():
    """Отменить текущую задачу."""
    if not STATUS["running"]:
        return {"success": False, "error": "Нет активных задач"}
    # Пока просто ставим флаг
    STATUS["running"] = False
    STATUS["current_job"] = None
    broadcast_event("job_cancelled", {"time": datetime.now().isoformat()})
    return {"success": True}


# ═══════════════════════════════════════════════════
#  API — Логи
# ═══════════════════════════════════════════════════

@app.get("/api/logs")
async def list_logs(count: int = Query(20, ge=1, le=100)):
    """Список лог-файлов."""
    logs = sorted(
        LOGS_DIR.glob("*.log"),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )[:count]
    return {
        "logs": [
            {
                "name": l.name,
                "size_bytes": l.stat().st_size,
                "modified": datetime.fromtimestamp(l.stat().st_mtime).isoformat(),
            }
            for l in logs
        ]
    }


@app.get("/api/logs/{filename}")
async def get_log(filename: str, tail: int = Query(500, ge=10, le=5000)):
    """Содержимое лог-файла (последние N строк)."""
    path = LOGS_DIR / filename
    if not path.exists():
        raise HTTPException(404, f"Лог '{filename}' не найден")

    lines = path.read_text(encoding="utf-8").split("\n")
    tail_lines = lines[-tail:] if len(lines) > tail else lines
    return {
        "filename": filename,
        "total_lines": len(lines),
        "content": "\n".join(tail_lines),
    }


@app.get("/api/events")
async def event_stream():
    """SSE-стрим событий в реальном времени."""
    async def generate():
        queue = asyncio.Queue(maxsize=200)
        _sse_subscribers.append(queue)
        try:
            # Приветственное сообщение
            yield "event: connected\ndata: {}\n\n"
            while True:
                msg = await queue.get()
                yield msg
        except asyncio.CancelledError:
            pass
        finally:
            _sse_subscribers.remove(queue)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ═══════════════════════════════════════════════════
#  API — Google Sheets
# ═══════════════════════════════════════════════════

@app.get("/api/sheets/test")
async def test_sheets(
    sheet_id: str = Query("", description="ID таблицы (необязательно)"),
    tab: str = Query("Отчёт по конкурентам", description="Название листа"),
):
    """Проверить доступ к Google Sheets."""
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        sid = sheet_id or os.getenv("SHEET_ID", "")
        if not sid:
            return {"success": False, "error": "Не указан SHEET_ID"}

        creds = service_account.Credentials.from_service_account_file(
            str(APP_DIR / "service_account.json"),
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        svc = build("sheets", "v4", credentials=creds)

        # Пробуем прочитать заголовки
        result = svc.spreadsheets().values().get(
            spreadsheetId=sid,
            range=f"{tab}!A1:Z1",
        ).execute()

        headers = result.get("values", [[]])[0] if result.get("values") else []
        return {
            "success": True,
            "sheet_id": sid,
            "tab": tab,
            "headers": headers,
            "cols": len(headers),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


# ═══════════════════════════════════════════════════
#  API — Конфиг (упрощённо)
# ═══════════════════════════════════════════════════

@app.post("/api/config")
async def update_config(
    openrouter_key: str = Form(""),
    brave_key: str = Form(""),
    youtube_key: str = Form(""),
    vk_key: str = Form(""),
    apify_token: str = Form(""),
    sheet_id: str = Form(""),
    sheet_tab: str = Form(""),
    validate_batch: str = Form(""),
):
    """Обновить переменные окружения (через .env файл)."""
    env_path = APP_DIR / ".env"
    if not env_path.exists():
        env_path.touch()

    updates = {}
    if openrouter_key:
        updates["OPENROUTER_API_KEY"] = openrouter_key
    if brave_key:
        updates["BRAVE_API_KEY"] = brave_key
    if youtube_key:
        updates["YOUTUBE_API_KEY"] = youtube_key
    if vk_key:
        updates["VK_API_KEY"] = vk_key
    if apify_token:
        updates["APIFY_API_TOKEN"] = apify_token
    if sheet_id:
        updates["SHEET_ID"] = sheet_id
    if sheet_tab:
        updates["SHEET_TAB"] = sheet_tab
    if validate_batch in ("true", "false"):
        updates["VALIDATE_BATCH"] = validate_batch

    # Читаем текущий .env и мержим
    current = {}
    for line in env_path.read_text(encoding="utf-8").split("\n"):
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            current[k.strip()] = v.strip()

    current.update(updates)

    new_content = "\n".join(f"{k}={v}" for k, v in current.items()) + "\n"
    env_path.write_text(new_content, encoding="utf-8")

    # Применяем в текущий процесс
    for k, v in updates.items():
        os.environ[k] = v

    broadcast_event("message", {
        "type": "config_updated",
        "keys": list(updates.keys()),
        "time": datetime.now().isoformat(),
    })

    return {"success": True, "updated": list(updates.keys())}


# ═══════════════════════════════════════════════════
#  HTML-интерфейс (минимальный)
# ═══════════════════════════════════════════════════

INDEX_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Марк1 — Конкурентный анализ</title>
<style>
:root {
  --bg: #0f1117; --surface: #1a1d27; --border: #2a2d3a;
  --text: #e1e4ed; --text2: #8b8fa3; --accent: #6366f1;
  --danger: #ef4444; --success: #22c55e; --warning: #f59e0b;
  --radius: 8px;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; }
.container { max-width: 1200px; margin: 0 auto; padding: 20px; }
header { display: flex; justify-content: space-between; align-items: center; padding: 16px 0; border-bottom: 1px solid var(--border); margin-bottom: 24px; }
header h1 { font-size: 24px; }
.status-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; margin-right: 8px; }
.status-dot.online { background: var(--success); }
.status-dot.busy { background: var(--warning); animation: pulse 1s infinite; }
@keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: .4; } }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
@media (max-width: 768px) { .grid { grid-template-columns: 1fr; } }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 20px; }
.card h2 { font-size: 16px; margin-bottom: 16px; color: var(--text2); text-transform: uppercase; letter-spacing: 0.5px; }
.form-group { margin-bottom: 12px; }
label { display: block; font-size: 13px; color: var(--text2); margin-bottom: 4px; }
input, select, textarea { width: 100%; padding: 8px 12px; border: 1px solid var(--border); border-radius: 6px; background: var(--bg); color: var(--text); font-size: 14px; }
input:focus, select:focus, textarea:focus { outline: none; border-color: var(--accent); }
textarea { font-family: monospace; font-size: 12px; min-height: 100px; resize: vertical; }
.btn { padding: 8px 16px; border: none; border-radius: 6px; cursor: pointer; font-size: 14px; font-weight: 500; transition: opacity .2s; }
.btn:hover { opacity: .85; }
.btn-primary { background: var(--accent); color: #fff; }
.btn-danger { background: var(--danger); color: #fff; }
.btn-success { background: var(--success); color: #fff; }
.btn-secondary { background: var(--border); color: var(--text); }
.checkbox-group { display: flex; gap: 16px; flex-wrap: wrap; }
.checkbox-group label { display: flex; align-items: center; gap: 6px; color: var(--text); font-size: 14px; }
.checkbox-group input[type=checkbox] { width: auto; accent-color: var(--accent); }
.client-list { max-height: 200px; overflow-y: auto; }
.client-item { display: flex; justify-content: space-between; align-items: center; padding: 8px; border-bottom: 1px solid var(--border); font-size: 14px; }
.client-item:last-child { border-bottom: none; }
.client-name { font-weight: 500; }
.client-sources { font-size: 12px; color: var(--text2); }
.log-window { background: #0a0a0f; border: 1px solid var(--border); border-radius: var(--radius); padding: 12px; font-family: 'Courier New', monospace; font-size: 12px; max-height: 400px; overflow-y: auto; white-space: pre-wrap; line-height: 1.5; }
.log-line.stdout { color: #86efac; }
.log-line.stderr { color: #fca5a5; }
.log-line.info { color: var(--text2); }
.badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 12px; }
.badge-success { background: rgba(34,197,94,.2); color: var(--success); }
.badge-danger { background: rgba(239,68,68,.2); color: var(--danger); }
.badge-info { background: rgba(99,102,241,.2); color: var(--accent); }
:disabled { opacity: .5; cursor: not-allowed; }
</style>
</head>
<body>
<div class="container">

<header>
  <div>
    <h1>🔍 Марк1 <small style="font-size:14px;color:var(--text2);">Конкурентный анализ</small></h1>
  </div>
  <div id="status-bar">
    <span class="status-dot online" id="status-dot"></span>
    <span id="status-text">Готов</span>
  </div>
</header>

<div class="grid">

<!-- Колонка 1: Профиль + настройки -->
<div>

<!-- Клиенты -->
<div class="card">
  <h2>📋 Клиенты (MD-профили)</h2>
  <div class="client-list" id="client-list">Загрузка...</div>
  <div style="margin-top:12px;display:flex;gap:8px;">
    <input type="file" id="md-upload" accept=".md" style="flex:1;padding:6px;">
    <button class="btn btn-secondary" onclick="uploadMd()">Загрузить</button>
  </div>
</div>

<!-- Настройки -->
<div class="card">
  <h2>⚙️ Настройки запуска</h2>
  <div class="form-group">
    <label>Клиент</label>
    <select id="selected-client" onchange="onClientSelect()">
      <option value="">Выберите клиента...</option>
    </select>
  </div>
  <div class="form-group">
    <label>Источники</label>
    <div class="checkbox-group">
      <label><input type="checkbox" name="source" value="instagram" checked> Instagram</label>
      <label><input type="checkbox" name="source" value="tiktok" checked> TikTok</label>
      <label><input type="checkbox" name="source" value="youtube" checked> YouTube</label>
      <label><input type="checkbox" name="source" value="vk"> VK</label>
      <label><input type="checkbox" name="source" value="brave"> Сайты (Brave)</label>
    </div>
  </div>
  <div class="form-group">
    <label>Google Sheets ID</label>
    <input type="text" id="sheet-id" placeholder="ID таблицы (из URL)">
  </div>
  <div class="form-group">
    <label>Название листа</label>
    <input type="text" id="sheet-tab" value="Отчёт по конкурентам">
  </div>
  <div style="display:flex;gap:8px;margin-top:16px;">
    <button class="btn btn-primary" onclick="startSearch()" id="btn-search">🔍 Поиск</button>
    <button class="btn btn-success" onclick="startAnalyze()" id="btn-analyze">🧠 Анализ</button>
    <button class="btn btn-danger" onclick="cancelJob()" id="btn-cancel" disabled>⏹ Стоп</button>
  </div>
  <div style="margin-top:8px;font-size:12px;color:var(--text2);">
    Поиск: быстрый сбор ✓ | Анализ: глубокий (AI) 🤖
  </div>
</div>

<!-- Таблица -->
<div class="card">
  <h2>📊 Проверка Google Sheets</h2>
  <button class="btn btn-secondary" onclick="testSheets()">Проверить доступ</button>
  <div id="sheets-result" style="margin-top:8px;font-size:13px;"></div>
</div>

</div>

<!-- Колонка 2: Логи -->
<div>
<div class="card" style="position:relative;">
  <h2>📜 Логи (live)</h2>
  <button class="btn btn-secondary" style="position:absolute;top:16px;right:16px;font-size:12px;" onclick="clearLogs()">Очистить</button>
  <div class="log-window" id="log-window">
    <div class="log-line info">Жду запуска...</div>
  </div>
</div>

<!-- Предыдущие логи -->
<div class="card">
  <h2>📁 Прошлые запуски</h2>
  <div id="log-files" style="font-size:13px;">Загрузка...</div>
</div>
</div>

</div>
</div>

<script>
// ── SSE ──
const evtSource = new EventSource('/api/events');
evtSource.addEventListener('log', e => {
  const d = JSON.parse(e.data);
  appendLog(d.stream, d.text);
});
evtSource.addEventListener('job_start', e => {
  const d = JSON.parse(e.data);
  appendLog('info', `🚀 Запуск: ${d.script} (${d.client || 'legacy'})`);
  setBusy(true);
});
evtSource.addEventListener('job_done', e => {
  const d = JSON.parse(e.data);
  appendLog('info', d.success ? `✅ Готово! ${d.total} конкурентов` : '❌ Ошибка');
  setBusy(false);
  loadLogFiles();
});
evtSource.addEventListener('job_error', e => {
  const d = JSON.parse(e.data);
  appendLog('stderr', `❌ ${d.error}`);
  setBusy(false);
});
evtSource.addEventListener('job_cancelled', e => {
  appendLog('info', '⏹ Отменено');
  setBusy(false);
});

function setBusy(busy) {
  document.getElementById('btn-search').disabled = busy;
  document.getElementById('btn-analyze').disabled = busy;
  document.getElementById('btn-cancel').disabled = !busy;
  const dot = document.getElementById('status-dot');
  dot.className = 'status-dot ' + (busy ? 'busy' : 'online');
  document.getElementById('status-text').textContent = busy ? 'Выполняется...' : 'Готов';
}

function appendLog(stream, text) {
  const w = document.getElementById('log-window');
  const cls = stream === 'stderr' ? 'stderr' : 'stdout';
  w.innerHTML += `<div class="log-line ${cls}">${escapeHtml(text)}</div>`;
  w.scrollTop = w.scrollHeight;
}

function escapeHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function clearLogs() {
  document.getElementById('log-window').innerHTML = '<div class="log-line info">Очищено</div>';
}

// ── API calls ──
async function api(method, url, body) {
  const opts = { method, headers: {} };
  if (body instanceof FormData) {
    opts.body = body;
  } else if (body) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  return fetch(url, opts).then(r => r.json());
}

async function loadClients() {
  const res = await fetch('/api/clients').then(r => r.json());
  const list = document.getElementById('client-list');
  list.innerHTML = res.clients.map(c => 
    `<div class="client-item">
      <div><div class="client-name">${c.name}</div><div class="client-sources">${c.sources.join(', ') || 'brave'} | ${c.sheet_tab || '—'}</div></div>
      <button class="btn btn-danger" style="padding:2px 8px;font-size:11px;" onclick="deleteClient('${c.name}')">✕</button>
    </div>`
  ).join('') || '<div style="color:var(--text2);padding:8px;">Нет профилей. Загрузите .md файл.</div>';

  // Обновляем select
  const sel = document.getElementById('selected-client');
  sel.innerHTML = '<option value="">Выберите клиента...</option>' + 
    res.clients.map(c => `<option value="${c.name}">${c.name}</option>`).join('');
}

async function loadLogFiles() {
  const res = await fetch('/api/logs?count=10').then(r => r.json());
  document.getElementById('log-files').innerHTML = res.logs.map(l =>
    `<div style="padding:4px 0;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center;">
      <span style="cursor:pointer;color:var(--accent);" onclick="viewLog('${l.name}')">${l.name}</span>
      <span style="font-size:11px;color:var(--text2);">${(l.size_bytes/1024).toFixed(1)} KB | ${l.modified.split('T')[1]?.slice(0,8)}</span>
    </div>`
  ).join('') || '<div style="color:var(--text2);">Нет логов</div>';
}

async function viewLog(filename) {
  const res = await fetch('/api/logs/' + filename).then(r => r.json());
  const w = document.getElementById('log-window');
  w.innerHTML = `<div class="log-line info">=== ${filename} (${res.total_lines} строк) ===</div>`;
  for (const line of res.content.split('\\n')) {
    const cls = line.includes('ERR') || line.includes('Error') ? 'stderr' : 'stdout';
    w.innerHTML += `<div class="log-line ${cls}">${escapeHtml(line)}</div>`;
  }
  w.scrollTop = w.scrollHeight;
}

async function onClientSelect() {
  const name = document.getElementById('selected-client').value;
  if (!name) return;
  const res = await fetch('/api/clients/' + name).then(r => r.json());
  document.getElementById('sheet-id').value = res.config.sheet_id || '';
  document.getElementById('sheet-tab').value = res.config.sheet_tab || 'Отчёт по конкурентам';
}

async function uploadMd() {
  const file = document.getElementById('md-upload').files[0];
  if (!file) return alert('Выберите .md файл');
  const fd = new FormData();
  fd.append('file', file);
  await fetch('/api/clients/upload', { method: 'POST', body: fd }).then(r => r.json());
  document.getElementById('md-upload').value = '';
  loadClients();
}

async function deleteClient(name) {
  if (!confirm('Удалить ' + name + '?')) return;
  await fetch('/api/clients/' + name, { method: 'DELETE' });
  loadClients();
}

async function startSearch() {
  const client = document.getElementById('selected-client').value;
  if (!client) return alert('Выберите клиента');
  const selectedSources = [...document.querySelectorAll('input[name=source]:checked')].map(c => c.value).join(',');
  const sheetId = document.getElementById('sheet-id').value;
  const sheetTab = document.getElementById('sheet-tab').value;
  const params = new URLSearchParams({ client, timeout: 300 });
  if (selectedSources) params.set('sources', selectedSources);
  if (sheetId) params.set('sheet_id', sheetId);
  if (sheetTab) params.set('sheet_tab', sheetTab);
  fetch('/api/search?' + params, { method: 'POST' });
}

async function startAnalyze() {
  const client = document.getElementById('selected-client').value;
  if (!client) return alert('Выберите клиента');
  fetch('/api/analyze?client=' + client + '&timeout=600', { method: 'POST' });
}

async function cancelJob() {
  fetch('/api/cancel', { method: 'POST' });
}

async function testSheets() {
  const sid = document.getElementById('sheet-id').value;
  const tab = document.getElementById('sheet-tab').value;
  const res = await fetch('/api/sheets/test?sheet_id=' + sid + '&tab=' + tab).then(r => r.json());
  const el = document.getElementById('sheets-result');
  if (res.success) {
    el.innerHTML = `<span class="badge badge-success">✅ Доступ есть</span> Колонки: ${res.cols}: ${res.headers.slice(0,10).join(', ')}${res.headers.length > 10 ? '...' : ''}`;
  } else {
    el.innerHTML = `<span class="badge badge-danger">❌ ${res.error}</span>`;
  }
}

// ── Startup ──
loadClients();
loadLogFiles();
setInterval(() => loadLogFiles(), 30000);

// Обновляем статус каждые 10 сек
setInterval(async () => {
  const res = await fetch('/api/status').then(r => r.json());
  setBusy(res.running);
}, 10000);
</script>
</body>
</html>
"""


# ═══════════════════════════════════════════════════
#  Точка входа
# ═══════════════════════════════════════════════════

def main():
    port = int(os.getenv("PORT", "8888"))
    print(f"🚀 Марк1 API на http://0.0.0.0:{port}")
    print(f"   Веб-интерфейс: http://0.0.0.0:{port}/")
    print(f"   Swagger docs:  http://0.0.0.0:{port}/docs")
    print(f"   SSE events:    http://0.0.0.0:{port}/api/events")

    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
