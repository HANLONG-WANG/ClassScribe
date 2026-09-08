from __future__ import annotations

import os
from importlib import import_module
from logging.config import fileConfig

from alembic import context
from classscribe.db.base import Base
from sqlalchemy import engine_from_config, pool

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

import_module("classscribe.db.models")
target_metadata = Base.metadata
TYPE_BOUND_ENUM_CONSTRAINTS = {
    constraint.name
    for table in target_metadata.tables.values()
    for constraint in table.constraints
    if getattr(constraint, "_type_bound", False)
}


def include_object(
    _: object,
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to: object | None,
) -> bool:
    # SQLite reflects SQLAlchemy Enum checks as ordinary constraints while the
    # matching metadata constraints are type-bound. Alembic otherwise reports
    # false removals even though their names and SQL are present in both schemas.
    return not (
        type_ == "check_constraint"
        and reflected
        and compare_to is None
        and name in TYPE_BOUND_ENUM_CONSTRAINTS
    )


def database_url() -> str:
    url = config.attributes.get("database_url") or os.environ.get(
        "CLASSSCRIBE_DATABASE_URL", config.get_main_option("sqlalchemy.url")
    )
    if not url:
        raise RuntimeError("a local SQLAlchemy database URL is required")
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = database_url()
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        if connection.dialect.name == "sqlite":
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.exec_driver_sql("PRAGMA journal_mode=WAL")
            connection.exec_driver_sql("PRAGMA synchronous=FULL")
            connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            compare_type=True,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
