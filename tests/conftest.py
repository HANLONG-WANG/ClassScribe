from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from classscribe.db import create_schema, create_sqlite_engine, make_session_factory
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture
def database(
    tmp_path: Path,
) -> Iterator[tuple[Engine, sessionmaker[Session], Path]]:
    path = tmp_path / "classscribe.sqlite3"
    engine = create_sqlite_engine(path)
    create_schema(engine)
    factory = make_session_factory(engine)
    yield engine, factory, path
    engine.dispose()
