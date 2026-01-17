import json
import os
import time

import redis

from db import get_db, init_db


def _parse_port(value: str | None, default: int) -> int:
    if not value:
        return default
    candidate = value.strip()
    if candidate.startswith(("tcp://", "http://", "https://")):
        candidate = candidate.split(":")[-1]
    try:
        return int(candidate)
    except ValueError:
        return default


REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = _parse_port(os.getenv("REDIS_PORT"), 6379)
REDIS_DB = int(os.getenv("REDIS_DB", "0"))
QUEUE_NAME = os.getenv("REDIS_QUEUE", "jobs")
PROCESSING_TIME = int(os.getenv("WORKER_PROCESSING_TIME", "5"))


def main() -> None:
    init_db()
    queue = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB)
    print("Worker started, waiting for jobs...")

    while True:
        _, message = queue.blpop(QUEUE_NAME)
        job = json.loads(message)
        task_id = job.get("task_id")
        if task_id is None:
            continue

        print(f"Processing task {task_id}")
        time.sleep(PROCESSING_TIME)

        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute("UPDATE tasks SET status='completed' WHERE id=%s", (task_id,))

        print(f"Task {task_id} completed")


if __name__ == "__main__":
    main()
