"""
TenderIQ local server — serves the dashboard and provides a /api/scrape endpoint
that actually runs tender_scraper.py as a subprocess.
"""
import http.server
import json
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

# Windows consoles default to cp1252, which can't encode → — degrade, don't crash
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(errors="replace")

BASE_DIR = Path(__file__).parent
PORT = 8787

_proc: subprocess.Popen | None = None
_log_file = None
_lock = threading.Lock()


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    def do_OPTIONS(self):
        self._cors(200)

    def do_POST(self):
        global _proc, _log_file
        if self.path == "/api/scrape":
            with _lock:
                if _proc and _proc.poll() is None:
                    return self._json(200, {"status": "running"})
                # Previously stdout/stderr went nowhere retrievable — the UI's
                # own "check scraper.log" error message was pointing at a file
                # that was never actually written to from this endpoint, so a
                # failed scrape looked like it silently did nothing.
                if _log_file:
                    _log_file.close()
                _log_file = open(BASE_DIR / "scraper.log", "a", encoding="utf-8")
                _log_file.write(f"\n[web] --- scrape triggered from dashboard ---\n")
                _log_file.flush()
                _proc = subprocess.Popen(
                    ["python", str(BASE_DIR / "tender_scraper.py")],
                    cwd=str(BASE_DIR),
                    stdout=_log_file,
                    stderr=subprocess.STDOUT,
                )
            self._json(200, {"status": "started"})
        else:
            self._json(404, {"error": "not found"})

    def do_GET(self):
        global _proc
        if self.path == "/api/status":
            if _proc is None:
                status = "idle"
            elif _proc.poll() is None:
                status = "running"
            else:
                status = "done" if _proc.returncode == 0 else "error"
            return self._json(200, {"status": status})
        super().do_GET()

    def _json(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _cors(self, code):
        self.send_response(code)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def log_message(self, fmt, *args):
        pass  # suppress noisy request logs


if __name__ == "__main__":
    url = f"http://localhost:{PORT}/tender_intelligence_app.html"
    server = http.server.HTTPServer(("localhost", PORT), Handler)
    print(f"TenderIQ running → {url}")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Server stopped.")
