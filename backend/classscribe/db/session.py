"""SQLite engine/session configuration with mandatory WAL and foreign keys."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from classscribe.db.base import Base


def create_sqlite_engine(path: Path, *, echo: bool = False) -> Engine:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    url = f"sqlite:///{path}"
    engine = create_engine(url, echo=echo, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def configure_connection(connection: sqlite3.Connection, _: object) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=FULL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    return engine


def create_schema(engine: Engine) -> None:
    Base.metadata.create_all(engine)


def upgrade_schema(engine: Engine) -> None:
    """Upgrade the authoritative database, including legacy unversioned current schemas."""
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    config.attributes["database_url"] = engine.url.render_as_string(hide_password=False)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if tables and "alembic_version" not in tables:
        # Older core releases created the latest schema without recording a revision.
        # Only stamp a schema that has every current table and column; never guess
        # the version of a partially upgraded database.
        for table in Base.metadata.sorted_tables:
            if table.name not in tables or {column.name for column in table.columns} != {
                column["name"] for column in inspector.get_columns(table.name)
            }:
                raise RuntimeError(
                    "Unversioned database schema is incompatible. Back up classscribe.sqlite3 "
                    "and establish its Alembic revision before upgrading; "
                    "the database was not modified."
                )
        command.stamp(config, "head")
    command.upgrade(config, "head")


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    session = factory()
    try:
        with session.begin():
            yield session
    finally:
        session.close()


def sqlite_pragmas(engine: Engine) -> dict[str, str | int]:
    with engine.connect() as connection:
        return {
            "journal_mode": str(connection.execute(text("PRAGMA journal_mode")).scalar_one()),
            "foreign_keys": int(connection.execute(text("PRAGMA foreign_keys")).scalar_one()),
            "synchronous": int(connection.execute(text("PRAGMA synchronous")).scalar_one()),
        }
