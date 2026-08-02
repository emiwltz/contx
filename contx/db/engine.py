"""Create hardened SQLite engines and explicit session transactions."""

from __future__ import annotations

import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker

from contx.errors import DatabaseError
from contx.settings.paths import PRIVATE_FILE_MODE


def create_database_engine(database_path: Path) -> Engine:
    """Open one SQLite database with required safety pragmas."""
    _prepare_database_file(database_path)
    url = URL.create("sqlite+pysqlite", database=str(database_path))
    engine = create_engine(url, connect_args={"timeout": 10.0})

    @event.listens_for(engine, "connect")
    def configure_connection(
        dbapi_connection: sqlite3.Connection,
        _connection_record: Any,
    ) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=10000")
            journal_mode = cursor.execute("PRAGMA journal_mode=WAL").fetchone()
            if journal_mode is None or str(journal_mode[0]).lower() != "wal":
                raise DatabaseError("SQLite could not enable WAL journal mode")
        finally:
            cursor.close()

    return engine


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    """Commit all work atomically or roll it back on failure."""
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as session:
        yield session


def _prepare_database_file(path: Path) -> None:
    if path.is_symlink():
        raise DatabaseError(f"CONTX database must not be a symlink: {path}")
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as error:
        raise DatabaseError(
            f"Cannot create CONTX database directory: {path.parent}"
        ) from error

    if not path.exists():
        try:
            descriptor = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                PRIVATE_FILE_MODE,
            )
            os.close(descriptor)
        except FileExistsError:
            pass
        except OSError as error:
            raise DatabaseError(f"Cannot create CONTX database: {path}") from error

    try:
        file_status = path.stat()
    except OSError as error:
        raise DatabaseError(f"Cannot inspect CONTX database: {path}") from error
    if not stat.S_ISREG(file_status.st_mode):
        raise DatabaseError(f"CONTX database is not a regular file: {path}")
    try:
        path.chmod(PRIVATE_FILE_MODE)
    except OSError as error:
        raise DatabaseError(f"Cannot protect CONTX database: {path}") from error
