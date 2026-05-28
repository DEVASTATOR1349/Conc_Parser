#!/usr/bin/env python3
"""
http_server.py — HTTP-сервер для n8n-оркестрации.
Полностью самодостаточный, использует те же search_competitors.py / analyze_competitors.py.

Эндпоинты:
  POST /competitors/search   — запуск поиска (Brave + YouTube + Instagram)
  POST /competitors/analyze  — AI-анализ через OpenRouter
  GET  /competitors/status   — статус последнего запуска
  GET  /health               — healthcheck
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

STATUS = {"last_search": None, "last_analyze": None, "search_count": 0}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write(f"[HTTP] {fmt % args}\n")

    def _json(self, data, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/competitors/status":
            self._json({
                "status": "ok",
                "last_search": str(STATUS["last_search"] or "never"),
                "last_analyze": str(STATUS["last_analyze"] or "never"),
                "search_count": STATUS["search_count"],
            })
        elif path == "/health":
            self._json({"status": "alive", "time": datetime.now().isoformat()})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/competitors/search":
            self._json(self._run("search_competitors.py", "search", 300))
        elif path == "/competitors/analyze":
            self._json(self._run("analyze_competitors.py", "analyze", 600))
        else:
            self._json({"error": "not found"}, 404)

    def _run(self, script, status_key, timeout):
        try:
            result = subprocess.run(
                ["python3", f"/app/{script}"],
                capture_output=True, text=True, timeout=timeout,
            )

            STATUS[f"last_{status_key}"] = datetime.now().isoformat()
            if status_key == "search":
                STATUS["search_count"] += 1

            # Парсим количество из вывода
            count = 0
            for line in result.stdout.split("\n"):
                if "ИТОГО" in line or "проанализировано" in line.lower():
                    nums = re.findall(r'\d+', line)
                    if nums:
                        count = int(nums[-1])

            report = result.stdout[-500:] if len(result.stdout) > 500 else result.stdout
            return {
                "success": result.returncode == 0,
                "total": count,
                "report": report.strip(),
                "errors": result.stderr[:300] if result.stderr else "",
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "error": f"Timeout ({timeout}s)"}
        except Exception as e:
            return {"success": False, "error": str(e)}


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8888
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"🚀 HTTP Server :{port}")
    print(f"   POST /competitors/search")
    print(f"   POST /competitors/analyze")
    print(f"   GET  /competitors/status")
    print(f"   GET  /health")
    server.serve_forever()


if __name__ == "__main__":
    main()
