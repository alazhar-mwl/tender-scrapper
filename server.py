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
import urllib.parse
import urllib.request
import webbrowser
from http.cookies import SimpleCookie
from pathlib import Path

from dotenv import load_dotenv
import os

import auth
import credentials

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

    def _session_token(self) -> str | None:
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        jar = SimpleCookie()
        jar.load(raw)
        morsel = jar.get(auth.COOKIE_NAME)
        return morsel.value if morsel else None

    def _current_user(self) -> str | None:
        return auth.validate_session(self._session_token())

    def _is_dotfile_path(self) -> bool:
        # Defense-in-depth: this working directory is a git checkout, so
        # .git/* sits inside the served folder. Nothing should ever be able
        # to fetch a dotfile/dotdir (.git, .env, etc.) regardless of the
        # auth check above — reject it outright rather than relying solely
        # on that check never having a bypass.
        path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        return any(part.startswith(".") for part in path.split("/") if part)

    def _handle_login(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            data = json.loads(self.rfile.read(length))
            username = data.get("username", "").strip()
            password = data.get("password", "")
        except Exception:
            return self._json(400, {"error": "Malformed request"})
        try:
            ok = auth.check_credentials(username, password)
        except RuntimeError as exc:
            return self._json(500, {"error": str(exc)})
        if not ok:
            return self._json(401, {"error": "Invalid username or password"})
        token = auth.create_session(username)
        body = json.dumps({"status": "ok", "user": username}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header(
            "Set-Cookie",
            f"{auth.COOKIE_NAME}={token}; Path=/; HttpOnly; Max-Age={auth.SESSION_TTL_SECONDS}; SameSite=Lax",
        )
        self.end_headers()
        self.wfile.write(body)

    def _handle_logout(self):
        auth.destroy_session(self._session_token())
        body = b'{"status": "ok"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Set-Cookie", f"{auth.COOKIE_NAME}=; Path=/; HttpOnly; Max-Age=0")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        global _proc, _log_file
        if self.path == "/api/login":
            return self._handle_login()
        if self.path == "/api/logout":
            return self._handle_logout()
        if not self._current_user():
            return self._json(401, {"error": "Not authenticated"})
        if self.path == "/api/scrape":
            with _lock:
                if _proc and _proc.poll() is None:
                    return self._json(200, {"status": "running"})
                # Previously stdout/stderr went nowhere retrievable — the UI's
                # own "check scraper.log" error message was pointing at a file
                # that was never actually written to from this endpoint, so a
                # failed scrape looked like it silently did nothing.
                #
                # Uses its OWN log file (scraper_web.log), separate from
                # scraper.log — run_scraper.bat's scheduled pipeline holds
                # scraper.log open via cmd.exe's ">>" redirection for the
                # full duration of each step (confirmed live: hours at a
                # time), and this endpoint opening the SAME file while that
                # redirection holds it raised an unhandled
                # PermissionError — killing this request with zero response
                # bytes (client sees a bare connection failure) before any
                # error could be reported. A 2026-08-06 fix already closes
                # this handle promptly to stop the dashboard's scrape from
                # blocking the scheduled one; a separate file removes the
                # contention in both directions instead of narrowing the
                # window one of them can block the other.
                if _log_file:
                    _log_file.close()
                _log_file = open(BASE_DIR / "scraper_web.log", "a", encoding="utf-8")
                _log_file.write(f"\n[web] --- scrape triggered from dashboard ---\n")
                _log_file.flush()
                _proc = subprocess.Popen(
                    [sys.executable, str(BASE_DIR / "tender_scraper.py")],
                    cwd=str(BASE_DIR),
                    stdout=_log_file,
                    stderr=subprocess.STDOUT,
                )
                # The handle above used to stay open indefinitely — only ever
                # closed right before the *next* scrape started — which held
                # a Windows file lock on this log for the rest of this
                # server's lifetime. Close it as soon as the subprocess
                # actually finishes instead.
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
        elif self.path == "/api/credentials":
            try:
                length = int(self.headers.get("Content-Length", 0))
                fields = json.loads(self.rfile.read(length))
                changed = credentials.update(fields, self._current_user())
                self._json(200, {"status": "ok", "changed": changed})
            except Exception as exc:
                self._json(500, {"error": str(exc)})
        else:
            self._json(404, {"error": "not found"})

    PUBLIC_PATHS = ("/login", "/login.html")
    PUBLIC_PREFIXES = ("/Logo/",)

    def do_GET(self):
        global _proc
        if self._is_dotfile_path():
            return self._json(404, {"error": "not found"})
        if self.path in self.PUBLIC_PATHS:
            self.path = "/login.html"
            return super().do_GET()
        if any(self.path.startswith(p) for p in self.PUBLIC_PREFIXES):
            return super().do_GET()

        if self.path == "/api/whoami":
            user = self._current_user()
            if not user:
                return self._json(401, {"error": "Not authenticated"})
            return self._json(200, {"user": user})

        if self.path == "/api/status":
            if not self._current_user():
                return self._json(401, {"error": "Not authenticated"})
            if _proc is None:
                status = "idle"
            elif _proc.poll() is None:
                status = "running"
            else:
                status = "done" if _proc.returncode == 0 else "error"
            return self._json(200, {"status": status})

        if self.path == "/api/credentials":
            if not self._current_user():
                return self._json(401, {"error": "Not authenticated"})
            return self._json(200, credentials.get_status())

        if self.path.startswith("/api/"):
            if not self._current_user():
                return self._json(401, {"error": "Not authenticated"})
            return self._json(404, {"error": "not found"})

        if not self._current_user():
            self.send_response(302)
            self.send_header("Location", "/login.html")
            self.end_headers()
            return
        super().do_GET()

    def do_HEAD(self):
        # BaseHTTPRequestHandler dispatches do_HEAD independently of do_GET —
        # without this override, SimpleHTTPRequestHandler's default do_HEAD
        # served file metadata (size, last-modified) for any path, including
        # protected source files and .git/*, without ever checking for a
        # session. Mirror do_GET's gating instead of falling through to it.
        if self._is_dotfile_path():
            self.send_response(404)
            self.end_headers()
            return
        if self.path in self.PUBLIC_PATHS:
            self.path = "/login.html"
            return super().do_HEAD()
        if any(self.path.startswith(p) for p in self.PUBLIC_PREFIXES):
            return super().do_HEAD()
        if self.path.startswith("/api/"):
            self.send_response(200 if self._current_user() else 401)
            self.end_headers()
            return
        if not self._current_user():
            self.send_response(302)
            self.send_header("Location", "/login.html")
            self.end_headers()
            return
        super().do_HEAD()

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
    #
    # Bind to all interfaces, not just localhost, so it's reachable from
    # other machines on the network (e.g. tender.sspdomain.com) once deployed
    # on a shared server rather than run from a laptop.
    server = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"TenderIQ running → {url}")
    # On a shared server this runs unattended (Task Scheduler at boot) —
    # don't pop open a browser window each time. Opt in locally with
    # TENDERIQ_OPEN_BROWSER=1, or just run as before on a laptop.
    if os.getenv("TENDERIQ_OPEN_BROWSER", "1").strip() == "1":
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Server stopped.")
