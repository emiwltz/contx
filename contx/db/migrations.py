"""Programmatic Alembic migration operations."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from urllib.parse import quote

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from contx.db.engine import create_database_engine
from contx.errors import DatabaseError
from contx.settings.paths import PRIVATE_FILE_MODE


def upgrade_database(database_path: Path) -> str:
    """Upgrade a database atomically to the packaged schema head."""
    engine = create_database_engine(database_path)
    config = _alembic_config()
    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
    except Exception as error:
        if isinstance(error, DatabaseError):
            raise
        raise DatabaseError("Cannot upgrade the CONTX database") from error
    finally:
        engine.dispose()
    try:
        database_path.chmod(PRIVATE_FILE_MODE)
    except OSError as error:
        raise DatabaseError(
            f"Cannot protect CONTX database: {database_path}"
        ) from error
    revision = current_database_revision(database_path)
    if revision is None:
        raise DatabaseError("CONTX database migration completed without a revision")
    return revision


def head_database_revision() -> str:
    """Return the single packaged Alembic head revision."""
    heads = ScriptDirectory.from_config(_alembic_config()).get_heads()
    if len(heads) != 1:
        raise DatabaseError("CONTX requires exactly one database migration head")
    return heads[0]


def current_database_revision(database_path: Path) -> str | None:
    """Read the current revision without creating or upgrading the database."""
    if not database_path.is_file() or database_path.is_symlink():
        return None
    uri = f"file:{quote(str(database_path), safe='/')}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
        try:
            row = connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error as error:
        raise DatabaseError("CONTX database schema is missing or unreadable") from error
    return None if row is None else str(row[0])


def _alembic_config() -> Config:
    migrations_path = Path(__file__).with_name("migrations")
    config = Config()
    config.set_main_option("script_location", str(migrations_path))
    return config
