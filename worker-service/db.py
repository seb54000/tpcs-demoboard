import os
import time
from contextlib import contextmanager
from typing import Iterator

import psycopg2
from psycopg2.extensions import connection


DB_HOST = os.getenv("DB_HOST", "postgres")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "tasks")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "postgres")
DB_MAX_RETRIES = int(os.getenv("DB_MAX_RETRIES", "10"))
DB_RETRY_DELAY = float(os.getenv("DB_RETRY_DELAY", "2"))


def _create_connection() -> connection:
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
                f"[worker-service] Database unavailable (attempt {attempt}/{DB_MAX_RETRIES}): {exc}"
            )
            time.sleep(DB_RETRY_DELAY)
    raise RuntimeError("Could not connect to database") from last_exc


def init_db() -> None:
    conn = _create_connection()
    conn.autocommit = True
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id SERIAL PRIMARY KEY,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending'
                )
                """
            )
    finally:
        conn.close()


@contextmanager
def get_db() -> Iterator[connection]:
    conn = _create_connection()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
