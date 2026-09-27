"""Read/update password.env.txt (the scraper's portal credentials) in place.

Lets any logged-in team member rotate the PDO/OQ portal passwords from the
dashboard instead of RDPing into wherever the scraper runs and hand-editing
the file — and since the dashboard's copy of this file is the one the
scraper actually reads, there's no second copy left to go stale.
"""
import re
import time
from pathlib import Path

BASE_DIR = Path(__file__).parent
ENV_FILE = BASE_DIR / "password.env.txt"
AUDIT_FILE = BASE_DIR / "credentials_audit.log"

# username fields are shown in the UI as plain text (not secret on their own);
# password fields are only ever reported as set/not-set, never their value.
USERNAME_FIELDS = ["SAP_USERNAME", "TAWREED_USERNAME"]
PASSWORD_FIELDS = ["SAP_PASSWORD", "TAWREED_PASSWORD"]
ALL_FIELDS = USERNAME_FIELDS + PASSWORD_FIELDS

_LINE_RE = re.compile(r'^([A-Z_]+)=(.*)$')


def _read_lines() -> list[str]:
    if not ENV_FILE.exists():
        return []
    return ENV_FILE.read_text(encoding="utf-8").splitlines(keepends=True)


def _current_values() -> dict[str, str]:
    values = {}
    for line in _read_lines():
        m = _LINE_RE.match(line.strip())
        if m and m.group(1) in ALL_FIELDS:
            values[m.group(1)] = m.group(2)
    return values


def get_status() -> dict:
    """Username values in plain text; passwords reported only as set/not-set."""
    values = _current_values()
    status = {}
    for f in USERNAME_FIELDS:
        status[f] = {"value": values.get(f, "")}
    for f in PASSWORD_FIELDS:
        status[f] = {"set": bool(values.get(f))}
    return status


def update(fields: dict, username: str) -> list[str]:
    """Update one or more credential fields in place.

    `fields` maps ALL_FIELDS keys to new values; blank/missing values are
    left untouched (so leaving a password box empty doesn't wipe it out).
    Returns the list of field names actually changed.
    """
    changed = [k for k, v in fields.items() if k in ALL_FIELDS and v]
    if not changed:
        return []

    lines = _read_lines()
    written = set()
    for i, line in enumerate(lines):
        m = _LINE_RE.match(line.strip())
        if m and m.group(1) in changed:
            key = m.group(1)
            lines[i] = f"{key}={fields[key]}\n"
            written.add(key)
    for key in changed:
        if key not in written:
            lines.append(f"{key}={fields[key]}\n")

    ENV_FILE.write_text("".join(lines), encoding="utf-8")

    with open(AUDIT_FILE, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {username}  updated: {', '.join(changed)}\n")

    return changed
