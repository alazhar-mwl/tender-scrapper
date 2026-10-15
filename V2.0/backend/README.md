# TenderIQ v2 — backend

FastAPI + SQLite. Phase 1: database, v1 import, read-only API (no login yet).
Run every command below from this `backend` folder.

## Setup

```
python -m pip install -r requirements.txt
```

## Where the data lives

The database (`tenderiq.db`) and tender documents live in the **data folder**,
which must be outside OneDrive (syncing can corrupt the database):

- Laptop default: `C:\Users\<you>\TenderIQ-data`
- Server: set `TENDERIQ_DATA_DIR`, e.g. `C:\TenderIQ\v2-data`

## Import from v1

```
python -m tenderiq.migrate_v1                         # v1 = the folder above V2.0
python -m tenderiq.migrate_v1 --v1-dir C:\TenderIQ    # or point at a v1 install
```

Reads v1's `tenders.json` and `documents/` (never changes them), copies the
documents into the data folder, then rebuilds every v1 record from the
database and compares field by field. It ends with `Verify OK` or a list of
differences. Safe to re-run — re-run it right before switching over so v2
has everything v1 scraped in the meantime.

## Run the API

```
python -m tenderiq
```

Serves on http://127.0.0.1:8790 — `/api/health`, `/api/tenders`
(`?status=active|inactive|all&source=pdo_srm|oq_tawreed`), `/api/tenders/{id}`,
and interactive docs at `/docs`. Localhost only until login arrives in Phase 2.

## Schema changes

Tables are managed by Alembic (`migrations/`). After changing `tenderiq/models.py`:

```
python -m alembic revision --autogenerate -m "what changed"
```

Review the generated file; the app applies pending migrations on start.

## Tests

```
python -m pytest
```
