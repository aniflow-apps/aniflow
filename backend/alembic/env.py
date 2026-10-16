from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from backend.app.core.config import get_settings
from backend.app.core.logging import logging_is_configured
from backend.app.db.base import Base
import backend.app.models  # noqa: F401 - registers mapped tables for autogeneration

config = context.config
config.set_main_option("sqlalchemy.url", get_settings().database_url)

# Only take over logging when alembic runs standalone (the `alembic` CLI).
#
# When the application applies migrations at startup (AUTO_MIGRATE) it has
# already configured logging, and fileConfig() would tear that down:
# it defaults to disable_existing_loggers=True, which switches off every
# logger created so far - uvicorn's included - and replaces the root handlers
# with alembic.ini's. The result is an app that runs perfectly while emitting
# no logs at all after startup. Even standalone, existing loggers are left
# alone; alembic only needs to add its own.
if config.config_file_name is not None and not logging_is_configured():
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
