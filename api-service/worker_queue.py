import json
import os
from typing import Any, Dict

import redis


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


def _get_client() -> redis.Redis:
    return redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB)


def publish_job(payload: Dict[str, Any]) -> None:
    client = _get_client()
    client.rpush(QUEUE_NAME, json.dumps(payload))
