"""Alembic environment. Run from V2.0/backend: `python -m alembic upgrade head`."""
from alembic import context

from tenderiq.db import get_engine
from tenderiq.models import Base

config = context.config
target_metadata = Base.metadata


def run_migrations_online() -> None:
    # upgrade_db() passes its own connection; the alembic CLI does not.
    connection = config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    with get_engine().begin() as conn:
        _run(conn)


def _run(connection) -> None:
    # render_as_batch: SQLite can't ALTER most column changes in place, so
    # Alembic rebuilds the table instead.
    context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


run_migrations_online()
