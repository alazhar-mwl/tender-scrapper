"""
TenderIQ local server — serves the dashboard, provides a /api/scrape endpoint
that runs tender_scraper.py as a subprocess, and an /api/ai endpoint that
scores + summarizes a tender via the Anthropic API (server-side, so the API
key never reaches the browser).
"""
import http.server
import json
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

from dotenv import load_dotenv
import os

# Windows consoles default to cp1252, which can't encode → — degrade, don't crash
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(errors="replace")

BASE_DIR = Path(__file__).parent
PORT = 8787

load_dotenv(BASE_DIR / "ai.env.txt")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()

_proc: subprocess.Popen | None = None
_log_file = None
_lock = threading.Lock()


def _close_log_when_done(proc: subprocess.Popen) -> None:
    proc.wait()
    global _log_file
    with _lock:
        # Only close if this subprocess's own handle is still the current
        # one — a newer /api/scrape call may already have replaced it.
        if _proc is proc and _log_file:
            _log_file.close()
            _log_file = None


def call_ai(t: dict) -> dict:
    """Score a tender's fit and summarize its scope of work in one call."""
    if not ANTHROPIC_API_KEY:
        raise RuntimeError(
            "No ANTHROPIC_API_KEY configured — add it to ai.env.txt (see .gitignore)."
        )
    body = json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": 700,
        "system": (
            "You are a BD analyst for Seven Seas Petroleum LLC, an oil & gas "
            "services firm in Oman. Given a tender, return ONLY JSON: "
            '{"score":<0-100 fit for an oil & gas services supplier>,'
            '"reasoning":[{"positive":<bool>,"text":"<max 12 words>"}] (exactly 3 items),'
            '"summary":"<2-3 plain-English sentences on what the supplier would '
            'actually need to deliver — ignore boilerplate T&Cs/VAT/Incoterms '
            "clauses, focus on the real scope>\"}"
        ),
        "messages": [{
            "role": "user",
            "content": f"RFx: {t.get('ref','')}\nTitle: {t.get('title','')}\n"
                       f"Type: {t.get('sector','')}\nScope: {t.get('scope','')[:6000]}",
        }],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    raw = next((c["text"] for c in data.get("content", []) if c.get("type") == "text"), "{}")
    return json.loads(raw.replace("```json", "").replace("```", "").strip())


class Handler(http.server.SimpleHTTPRequestHandler):
    timeout = 30  # belt-and-suspenders: bound how long a stuck read can block a thread

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
                # The handle above used to stay open indefinitely — only ever
                # closed right before the *next* scrape started — which held
                # a Windows file lock on scraper.log for the rest of this
                # server's lifetime and silently blocked anything else (e.g.
                # the scheduled task's run_scraper.bat) from writing to the
                # same file. Confirmed live 2026-08-06: this is exactly what
                # made the scheduled task fail with no diagnostic trail after
                # a dashboard-triggered scrape had run. Close it as soon as
                # the subprocess actually finishes instead.
                proc_ref = _proc
                threading.Thread(target=_close_log_when_done, args=(proc_ref,), daemon=True).start()
            self._json(200, {"status": "started"})
        elif self.path == "/api/ai":
            try:
                length = int(self.headers.get("Content-Length", 0))
                t = json.loads(self.rfile.read(length))
                result = call_ai(t)
                self._json(200, result)
            except urllib.error.HTTPError as exc:
                self._json(502, {"error": f"Anthropic API error {exc.code}: {exc.read().decode(errors='replace')[:300]}"})
            except Exception as exc:
                self._json(500, {"error": str(exc)})
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
    # Plain HTTPServer is single-threaded — one slow/stuck request (e.g. a
    # client that opens a connection but never finishes sending its body)
    # blocks the entire server, including serving the dashboard itself.
    # Confirmed live 2026-08-03: a PowerShell Invoke-RestMethod test request
    # hung waiting on an Expect:100-continue handshake this server never
    # answers, and took the whole dashboard down with it.
    server = http.server.ThreadingHTTPServer(("localhost", PORT), Handler)
    print(f"TenderIQ running → {url}")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Server stopped.")
