"""LDAP authentication + in-memory session management for TenderIQ.

Employees log in with their normal SSP Windows/email username and password —
verified against Active Directory over LDAP. A successful LDAP "bind" IS the
credential check (AD itself rejects a wrong password), so no password is ever
stored here, only a random session token per logged-in browser.
"""
import os
import secrets
import time
from pathlib import Path

from dotenv import load_dotenv
from ldap3 import Server, Connection, SIMPLE

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / "ldap.env.txt")

LDAP_SERVER = os.getenv("LDAP_SERVER", "").strip()
LDAP_PORT = int(os.getenv("LDAP_PORT", "389").strip() or "389")
LDAP_USE_SSL = os.getenv("LDAP_USE_SSL", "False").strip().lower() == "true"
LDAP_DOMAIN = os.getenv("LDAP_DOMAIN", "").strip()

COOKIE_NAME = "tenderiq_session"
SESSION_TTL_SECONDS = 8 * 60 * 60  # one workday

_sessions: dict[str, dict] = {}  # token -> {"user": str, "expires": float}


def is_configured() -> bool:
    return bool(LDAP_SERVER and LDAP_DOMAIN)


def check_credentials(username: str, password: str) -> bool:
    """Verify username/password against Active Directory via an LDAP simple bind."""
    if not username or not password:
        return False
    if not is_configured():
        raise RuntimeError(
            "LDAP is not configured yet — set LDAP_SERVER and LDAP_DOMAIN in ldap.env.txt"
        )
    server = Server(LDAP_SERVER, port=LDAP_PORT, use_ssl=LDAP_USE_SSL)
    user_principal = f"{username}@{LDAP_DOMAIN}"
    try:
        conn = Connection(server, user=user_principal, password=password, authentication=SIMPLE)
        ok = conn.bind()
        if ok:
            conn.unbind()
        return ok
    except Exception:
        return False


def create_session(username: str) -> str:
    token = secrets.token_urlsafe(32)
    _sessions[token] = {"user": username, "expires": time.time() + SESSION_TTL_SECONDS}
    return token


def validate_session(token: str | None) -> str | None:
    """Return the logged-in username for a valid, unexpired token, else None."""
    if not token:
        return None
    entry = _sessions.get(token)
    if not entry:
        return None
    if entry["expires"] < time.time():
        del _sessions[token]
        return None
    return entry["user"]


def destroy_session(token: str | None) -> None:
    if token:
        _sessions.pop(token, None)
