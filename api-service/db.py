import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Protocol, Union

import psycopg2
from psycopg2.extensions import connection as pg_connection


class SupportsCursor(Protocol):
    def cursor(self):
        ...

    def commit(self) -> None:
        ...

    def close(self) -> None:
        ...


DB_BACKEND = os.getenv("DB_BACKEND", "sqlite").lower()
SQLITE_PATH = os.getenv("SQLITE_PATH", "/data/tasks.db")

DB_HOST = os.getenv("DB_HOST", "postgres")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "tasks")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "postgres")

DB_MAX_RETRIES = int(os.getenv("DB_MAX_RETRIES", "10"))
DB_RETRY_DELAY = float(os.getenv("DB_RETRY_DELAY", "2"))

Connection = Union[pg_connection, sqlite3.Connection]


def _create_sqlite_connection() -> sqlite3.Connection:
    db_path = Path(SQLITE_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _create_postgres_connection() -> pg_connection:
    last_exc: Exception | None = None
    for attempt in range(1, DB_MAX_RETRIES + 1):
        try:
            return psycopg2.connect(
                host=DB_HOST,
                port=DB_PORT,
                dbname=DB_NAME,
                user=DB_USER,
                password=DB_PASSWORD,
            )
        except psycopg2.OperationalError as exc:
            last_exc = exc
            print(
                f"[api-service] Database unavailable (attempt {attempt}/{DB_MAX_RETRIES}): {exc}"
            )
            time.sleep(DB_RETRY_DELAY)
    raise RuntimeError("Could not connect to database") from last_exc


def _create_connection() -> Connection:
    if DB_BACKEND == "postgres":
        return _create_postgres_connection()
    if DB_BACKEND == "sqlite":
        return _create_sqlite_connection()
    raise ValueError(f"Unsupported DB_BACKEND '{DB_BACKEND}'")


def _schema_sql() -> str:
    if DB_BACKEND == "postgres":
        return """
            CREATE TABLE IF NOT EXISTS tasks (
                id SERIAL PRIMARY KEY,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
            )
        """
    return """
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
        )
    """


def init_db() -> None:
    conn = _create_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(_schema_sql())
        conn.commit()
    finally:
        cursor.close()
        conn.close()


@contextmanager
def get_db() -> Iterator[Connection]:
    conn = _create_connection()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


@contextmanager
def get_cursor(conn: SupportsCursor):
    cursor = conn.cursor()
    try:
        yield cursor
    finally:
        cursor.close()


def format_sql(statement: str) -> str:
    if DB_BACKEND == "postgres":
        return statement
    return statement.replace("%s", "?")
