"""Parsing the portals' date strings into real dates (Oman local time)."""
from datetime import date, datetime, time

# Both portals put the day first:
#   OQ Tawreed: "02/08/2026 15:00"   PDO SRM: "11.08.2026" (no time)
_WITH_TIME = ("%d/%m/%Y %H:%M", "%d.%m.%Y %H:%M")
_DATE_ONLY = ("%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d")


def parse_portal_datetime(raw: str | None) -> tuple[date | None, time | None]:
    """
    Return (date, time) for a portal date string; time is None when the
    portal gives only a date. Blank → (None, None). Anything unrecognised
    raises ValueError, so a portal changing its format fails loudly instead
    of silently dropping deadlines.
    """
    s = (raw or "").strip()
    if not s:
        return None, None
    for fmt in _WITH_TIME:
        try:
            dt = datetime.strptime(s, fmt)
            return dt.date(), dt.time()
        except ValueError:
            pass
    for fmt in _DATE_ONLY:
        try:
            return datetime.strptime(s, fmt).date(), None
        except ValueError:
            pass
    raise ValueError(f"Unrecognised portal date: {raw!r}")


def parse_iso_date(raw: str | None) -> date | None:
    s = (raw or "").strip()
    return date.fromisoformat(s) if s else None
