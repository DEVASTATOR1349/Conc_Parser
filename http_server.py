#!/usr/bin/env python3
"""
HTTP server inside apify-parser for n8n orchestration.
Listens on port 8888, proxies commands to Python scripts.

Endpoints:
  POST /competitors/search   - run YouTube search
  POST /competitors/analyze  - run AI analysis (Gorgona)
  GET  /competitors/status   - last run status
  GET  /health               - healthcheck
"""

import json, os, re, subprocess, sys
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
        if self.path == "/competitors/status":
            self._json({
                "status": "ok",
                "last_search": str(STATUS["last_search"] or "never"),
                "last_analyze": str(STATUS["last_analyze"] or "never"),
                "search_count": STATUS["search_count"],
            })
        elif self.path == "/health":
            self._json({"status": "alive", "time": datetime.now().isoformat()})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        clen = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(clen).decode() if clen else ""

        if self.path == "/competitors/search":
            self._json(self._run_search())
        elif self.path == "/competitors/analyze":
            self._json(self._run_analyze())
        elif self.path == "/webhook/analyze-new":
            # For Gorgona: analyze unprocessed competitors
            self._json(self._run_analyze())
        else:
            self._json({"error": "not found"}, 404)

    def _run_search(self):
        try:
            result = subprocess.run(
                ["python3", "/app/gen_compet_search.py"],
                capture_output=True, text=True, timeout=300
            )
            STATUS["last_search"] = datetime.now().isoformat()
            STATUS["search_count"] += 1

            count = 0
            for line in result.stdout.split("\n"):
                if "итого" in line.lower():
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
            return {"success": False, "error": "Timeout (300s)"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _run_analyze(self):
        try:
            result = subprocess.run(
                ["python3", "/app/gorgona_analyze.py"],
                capture_output=True, text=True, timeout=600
            )
            STATUS["last_analyze"] = datetime.now().isoformat()

            count = 0
            for line in result.stdout.split("\n"):
                if "проанализировано" in line.lower() or "анализируем" in line.lower():
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
            return {"success": False, "error": "Timeout (600s)"}
        except Exception as e:
            return {"success": False, "error": str(e)}


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8888
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"Server on port {port}")
    print(f"  POST /competitors/search  - search competitors")
    print(f"  POST /competitors/analyze - AI analysis")
    print(f"  GET  /competitors/status  - status")
    print(f"  GET  /health              - healthcheck")
    server.serve_forever()


if __name__ == "__main__":
    main()
