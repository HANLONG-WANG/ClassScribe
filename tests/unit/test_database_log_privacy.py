from __future__ import annotations

from pathlib import Path

import pytest
from classscribe.db.session import create_sqlite_engine
from sqlalchemy.exc import IntegrityError


def test_database_errors_hide_transcript_parameters(tmp_path: Path) -> None:
    engine = create_sqlite_engine(tmp_path / "privacy.sqlite3")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE samples (body TEXT UNIQUE)")
        connection.exec_driver_sql("INSERT INTO samples (body) VALUES (?)", ("private transcript",))
        with pytest.raises(IntegrityError) as failure:
            connection.exec_driver_sql(
                "INSERT INTO samples (body) VALUES (?)", ("private transcript",)
            )
    assert "private transcript" not in str(failure.value)
    assert "hidden" in str(failure.value)
