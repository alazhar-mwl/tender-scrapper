"""
Where v2 keeps its data, and the handful of settings everything shares.

The data directory holds the SQLite database and downloaded tender documents.
It must NOT be inside a OneDrive-synced folder: OneDrive uploading a database
file while SQLite is mid-write can corrupt it. On a laptop the default is
%USERPROFILE%\\TenderIQ-data (not AppData: Windows redirects AppData writes
from packaged apps such as the Claude desktop app into a private copy, so two
terminals could silently see different databases). On the server set
TENDERIQ_DATA_DIR (e.g. C:\\TenderIQ\\v2-data).
"""
import os
from datetime import timedelta, timezone
from pathlib import Path

# Oman is UTC+4 all year (no daylight saving). A fixed offset avoids needing
# the tzdata package, which ZoneInfo("Asia/Muscat") requires on Windows.
OMAN_TZ = timezone(timedelta(hours=4), "GST")

API_HOST = os.environ.get("TENDERIQ_HOST", "127.0.0.1")
API_PORT = int(os.environ.get("TENDERIQ_PORT", "8790"))


def data_dir() -> Path:
    raw = os.environ.get("TENDERIQ_DATA_DIR")
    path = Path(raw) if raw else Path.home() / "TenderIQ-data"
    path = path.resolve()
    # Windows sets these to the root of each synced OneDrive folder.
    onedrive_roots = [
        Path(os.environ[var]).resolve()
        for var in ("OneDrive", "OneDriveCommercial", "OneDriveConsumer")
        if os.environ.get(var)
    ]
    if any(path.is_relative_to(root) for root in onedrive_roots):
        raise RuntimeError(
            f"TenderIQ data directory {path} is inside OneDrive. OneDrive syncing "
            "can corrupt the database - set TENDERIQ_DATA_DIR to a local folder."
        )
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return data_dir() / "tenderiq.db"


def documents_dir() -> Path:
    path = data_dir() / "documents"
    path.mkdir(parents=True, exist_ok=True)
    return path
