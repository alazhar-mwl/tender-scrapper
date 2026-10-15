"""SQLite engine/session setup and schema upgrades (via Alembic)."""
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from . import config

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _set_sqlite_pragmas(dbapi_conn, _record) -> None:
    cur = dbapi_conn.cursor()
    # WAL lets the dashboard keep reading while the scraper writes.
    cur.execute("PRAGMA journal_mode=WAL")
    # SQLite ignores foreign keys unless this is switched on per connection.
    cur.execute("PRAGMA foreign_keys=ON")
    # Wait up to 10s for a competing writer instead of failing immediately.
    cur.execute("PRAGMA busy_timeout=10000")
    cur.close()


@lru_cache
def get_engine(db_file: str | None = None) -> Engine:
    path = db_file or str(config.db_path())
    engine = create_engine(f"sqlite:///{path}")
    event.listen(engine, "connect", _set_sqlite_pragmas)
    return engine


def session_factory(engine: Engine | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=engine or get_engine(), expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one session per request."""
    with session_factory()() as session:
        yield session


def upgrade_db(engine: Engine | None = None) -> None:
    """Bring the database schema up to date (creates it on first run)."""
    from alembic import command
    from alembic.config import Config

    engine = engine or get_engine()
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
