import json
import os
import random
import time

import redis
from opentelemetry.propagate import extract
from opentelemetry.trace import SpanKind

from db import get_db, init_db
from telemetry import job_counter, job_duration, logger, tracer


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


def _parse_float(value: str | None, default: float | None = None) -> float | None:
    if value is None:
        return default
    candidate = value.strip()
    if not candidate:
        return default
    try:
        return float(candidate)
    except ValueError:
        return default


REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = _parse_port(os.getenv("REDIS_PORT"), 6379)
REDIS_DB = int(os.getenv("REDIS_DB", "0"))
QUEUE_NAME = os.getenv("REDIS_QUEUE", "jobs")
PROCESSING_TIME = _parse_float(os.getenv("WORKER_PROCESSING_TIME"))
PROCESSING_TIME_MIN = _parse_float(os.getenv("WORKER_PROCESSING_TIME_MIN_SECONDS"), 1.5) or 1.5
PROCESSING_TIME_MAX = _parse_float(os.getenv("WORKER_PROCESSING_TIME_MAX_SECONDS"), 2.7) or 2.7
DEGRADED_PROCESSING_TIME_MIN = (
    _parse_float(os.getenv("DEGRADED_WORKER_PROCESSING_TIME_MIN_SECONDS"), 3.5) or 3.5
)
DEGRADED_PROCESSING_TIME_MAX = (
    _parse_float(os.getenv("DEGRADED_WORKER_PROCESSING_TIME_MAX_SECONDS"), 6.0) or 6.0
)
NODE_NAME = os.getenv("NODE_NAME", "")
DEGRADED_NODE_MATCH = os.getenv("DEGRADED_NODE_MATCH", "eu-west-3c")


def _is_degraded_node() -> bool:
    return bool(NODE_NAME and DEGRADED_NODE_MATCH and DEGRADED_NODE_MATCH in NODE_NAME)


def _resolve_processing_time() -> float:
    if _is_degraded_node():
        lower_bound = min(DEGRADED_PROCESSING_TIME_MIN, DEGRADED_PROCESSING_TIME_MAX)
        upper_bound = max(DEGRADED_PROCESSING_TIME_MIN, DEGRADED_PROCESSING_TIME_MAX)
        return random.uniform(lower_bound, upper_bound)
    if PROCESSING_TIME is not None:
        return max(PROCESSING_TIME, 0.0)
    lower_bound = min(PROCESSING_TIME_MIN, PROCESSING_TIME_MAX)
    upper_bound = max(PROCESSING_TIME_MIN, PROCESSING_TIME_MAX)
    return random.uniform(lower_bound, upper_bound)


def _simulate_processing(processing_time: float, task_id: int) -> int:
    if not _is_degraded_node():
        time.sleep(processing_time)
        return 0

    retry_logs = 0
    remaining = processing_time
    while remaining > 0:
        sleep_for = min(1.0, remaining)
        logger.warning(
            "Worker issue detected on node %s for task %s; retrying processing step in %.1f seconds",
            NODE_NAME,
            task_id,
            sleep_for,
        )
        time.sleep(sleep_for)
        remaining -= sleep_for
        retry_logs += 1
    logger.info("Worker recovered on node %s for task %s", NODE_NAME, task_id)
    return retry_logs


def main() -> None:
    init_db()
    queue = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB)
    logger.info("Worker started and waiting for jobs")

    while True:
        _, message = queue.blpop(QUEUE_NAME)
        job = json.loads(message)
        task_id = job.get("task_id")
        if task_id is None:
            continue

        parent_context = extract(job.get("_trace", {}))
        start = time.perf_counter()
        with tracer.start_as_current_span(
            "worker.process_job",
            context=parent_context,
            kind=SpanKind.CONSUMER,
        ) as span:
            processing_time = _resolve_processing_time()
            span.set_attribute("messaging.system", "redis")
            span.set_attribute("messaging.destination.name", QUEUE_NAME)
            span.set_attribute("task.id", task_id)
            span.set_attribute("worker.processing_time.seconds", processing_time)
            span.set_attribute("node.name", NODE_NAME or "unknown")
            span.set_attribute("node.degraded", _is_degraded_node())
            logger.info("Processing task %s for %.3f seconds", task_id, processing_time)
            retry_logs = _simulate_processing(processing_time, task_id)
            if retry_logs:
                span.set_attribute("worker.retry_logs", retry_logs)

            with tracer.start_as_current_span("worker.complete_task") as db_span:
                db_span.set_attribute("task.id", task_id)
                with get_db() as conn:
                    with conn.cursor() as cursor:
                        cursor.execute("UPDATE tasks SET status='completed' WHERE id=%s", (task_id,))

            duration_ms = (time.perf_counter() - start) * 1000
            job_counter.add(1, {"queue.name": QUEUE_NAME})
            job_duration.record(duration_ms, {"queue.name": QUEUE_NAME})
            logger.info("Task %s completed", task_id)


if __name__ == "__main__":
    main()
