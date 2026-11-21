import os
import random
import time
from typing import List

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from opentelemetry.trace import SpanKind, Status, StatusCode

from db import DB_BACKEND, format_sql, get_cursor, get_db, init_db
from models import Task, TaskCreate, TaskUpdate
from telemetry import (
    job_start_counter,
    logger,
    request_counter,
    request_duration,
    task_create_counter,
    tracer,
)
from version import get_app_version
from worker_queue import publish_job

VALID_STATUSES = {"pending", "processing", "completed"}
APP_VERSION = get_app_version()
ENABLE_WORKER = os.getenv("ENABLE_WORKER", "true").lower() == "true"
NODE_NAME = os.getenv("NODE_NAME", "")
NODE_ZONE_FILE = os.getenv("NODE_ZONE_FILE", "/var/run/demoboard/node_zone")
DEGRADED_NODE_ZONE_MATCH = os.getenv(
    "DEGRADED_NODE_ZONE_MATCH",
    os.getenv("DEGRADED_NODE_MATCH", "eu-west-3c"),
)


def _resolve_node_zone() -> str:
    env_value = os.getenv("NODE_ZONE", "").strip()
    if env_value:
        return env_value
    try:
        return open(NODE_ZONE_FILE, "r", encoding="utf-8").read().strip()
    except OSError:
        return ""


NODE_ZONE = _resolve_node_zone()


def _is_degraded_node() -> bool:
    return bool(
        NODE_ZONE and DEGRADED_NODE_ZONE_MATCH and DEGRADED_NODE_ZONE_MATCH in NODE_ZONE
    )


def _simulate_api_db_reconnect(method: str, path: str) -> int:
    if not _is_degraded_node():
        return 0

    retry_count = random.randint(2, 3)
    for attempt in range(1, retry_count + 1):
        logger.warning(
            "Database connection lost on node %s while handling %s %s; retry %s/%s",
            NODE_ZONE or NODE_NAME or "unknown",
            method,
            path,
            attempt,
            retry_count,
        )
        time.sleep(random.uniform(0.2, 0.3))
    logger.info(
        "Database connection restored on node %s for %s %s",
        NODE_ZONE or NODE_NAME or "unknown",
        method,
        path,
    )
    return retry_count

app = FastAPI(title="Demoboard API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup_event() -> None:
    init_db()
    logger.info("API service started")


def _row_to_task(row: tuple) -> Task:
    return Task(id=row[0], title=row[1], status=row[2])


def _ensure_task_exists(cursor, task_id: int) -> None:
    cursor.execute(format_sql("SELECT id FROM tasks WHERE id=%s"), (task_id,))
    if cursor.fetchone() is None:
        raise HTTPException(status_code=404, detail="Task not found")


def _validate_status(status: str | None) -> None:
    if status is None:
        return
    if status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid status value")


@app.get("/healthz")
def healthcheck() -> dict:
    return {
        "status": "ok",
        "version": APP_VERSION,
        "node": NODE_NAME or "unknown",
        "zone": NODE_ZONE or "unknown",
    }


@app.middleware("http")
async def observe_requests(request: Request, call_next):
    start = time.perf_counter()
    raw_path = request.url.path
    span_name = f"{request.method} {raw_path}"
    with tracer.start_as_current_span(span_name, kind=SpanKind.SERVER) as span:
        logger.info(
            "demoboard.lifecycle action=request.started method=%s path=%s",
            request.method,
            raw_path,
        )
        reconnect_retries = _simulate_api_db_reconnect(request.method, raw_path)
        span.set_attribute("http.method", request.method)
        span.set_attribute("url.path", raw_path)
        span.set_attribute("node.name", NODE_NAME or "unknown")
        span.set_attribute("node.zone", NODE_ZONE or "unknown")
        span.set_attribute("node.degraded", _is_degraded_node())
        if reconnect_retries:
            span.set_attribute("api.db_reconnect_retries", reconnect_retries)
        try:
            response = await call_next(request)
        except Exception as exc:
            route = request.scope.get("route")
            route_path = getattr(route, "path", raw_path)
            duration_ms = (time.perf_counter() - start) * 1000
            labels = {
                "http.method": request.method,
                "url.path": route_path,
                "http.status_code": 500,
            }
            request_counter.add(1, labels)
            request_duration.record(duration_ms, labels)
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR))
            logger.exception(
                "demoboard.lifecycle action=request.failed method=%s path=%s status=500",
                request.method,
                route_path,
            )
            raise

        route = request.scope.get("route")
        route_path = getattr(route, "path", raw_path)
        duration_ms = (time.perf_counter() - start) * 1000
        labels = {
            "http.method": request.method,
            "url.path": route_path,
            "http.status_code": response.status_code,
        }
        request_counter.add(1, labels)
        request_duration.record(duration_ms, labels)
        span.set_attribute("http.route", route_path)
        span.set_attribute("http.status_code", response.status_code)
        if response.status_code >= 500:
            span.set_status(Status(StatusCode.ERROR))
        logger.info(
            "demoboard.lifecycle action=request.completed method=%s path=%s status=%s duration_ms=%.2f",
            request.method,
            route_path,
            response.status_code,
            duration_ms,
        )
        return response


@app.post("/tasks", response_model=Task, status_code=201)
def create_task(task: TaskCreate) -> Task:
    with tracer.start_as_current_span("tasks.create") as span:
        span.set_attribute("task.title", task.title)
        logger.info("demoboard.lifecycle action=task.create.started")
        logger.info("demoboard.lifecycle action=task.create.database_write.started")
        with get_db() as conn:
            with get_cursor(conn) as cursor:
                if DB_BACKEND == "postgres":
                    cursor.execute(
                        format_sql(
                            "INSERT INTO tasks (title, status) VALUES (%s, %s) RETURNING id, title, status"
                        ),
                        (task.title, "pending"),
                    )
                    row = cursor.fetchone()
                else:
                    cursor.execute(
                        format_sql("INSERT INTO tasks (title, status) VALUES (%s, %s)"),
                        (task.title, "pending"),
                    )
                    cursor.execute(
                        format_sql("SELECT id, title, status FROM tasks WHERE id=%s"),
                        (cursor.lastrowid,),
                    )
                    row = cursor.fetchone()
        created_task = _row_to_task(row)
        span.set_attribute("task.id", created_task.id)
        task_create_counter.add(1, {"status": created_task.status})
        logger.info(
            "demoboard.lifecycle action=task.create.database_write.completed task_id=%s status=%s",
            created_task.id,
            created_task.status,
        )
        logger.info(
            "demoboard.lifecycle action=task.create.completed task_id=%s status=%s",
            created_task.id,
            created_task.status,
        )
        return created_task


@app.get("/tasks", response_model=List[Task])
def list_tasks() -> List[Task]:
    with tracer.start_as_current_span("tasks.list") as span:
        logger.info("demoboard.lifecycle action=task.list.started")
        with get_db() as conn:
            with get_cursor(conn) as cursor:
                cursor.execute(format_sql("SELECT id, title, status FROM tasks ORDER BY id"))
                rows = cursor.fetchall()
        span.set_attribute("tasks.count", len(rows))
        logger.info("demoboard.lifecycle action=task.list.completed count=%s", len(rows))
        return [_row_to_task(row) for row in rows]


@app.get("/tasks/{task_id}", response_model=Task)
def get_task(task_id: int) -> Task:
    with tracer.start_as_current_span("tasks.get") as span:
        span.set_attribute("task.id", task_id)
        logger.info("demoboard.lifecycle action=task.get.started task_id=%s", task_id)
        with get_db() as conn:
            with get_cursor(conn) as cursor:
                cursor.execute(
                    format_sql("SELECT id, title, status FROM tasks WHERE id=%s"), (task_id,)
                )
                row = cursor.fetchone()
                if row is None:
                    raise HTTPException(status_code=404, detail="Task not found")
        task = _row_to_task(row)
        logger.info(
            "demoboard.lifecycle action=task.get.completed task_id=%s status=%s",
            task_id,
            task.status,
        )
        return task


@app.put("/tasks/{task_id}", response_model=Task)
def update_task(task_id: int, payload: TaskUpdate) -> Task:
    if payload.title is None and payload.status is None:
        raise HTTPException(status_code=400, detail="Nothing to update")
    _validate_status(payload.status)
    fields = []
    values = []
    if payload.title is not None:
        fields.append("title=%s")
        values.append(payload.title)
    if payload.status is not None:
        fields.append("status=%s")
        values.append(payload.status)
    values.append(task_id)
    set_clause = ", ".join(fields)
    with tracer.start_as_current_span("tasks.update") as span:
        span.set_attribute("task.id", task_id)
        logger.info("demoboard.lifecycle action=task.update.started task_id=%s", task_id)
        with get_db() as conn:
            with get_cursor(conn) as cursor:
                _ensure_task_exists(cursor, task_id)
                if DB_BACKEND == "postgres":
                    cursor.execute(
                        format_sql(
                            f"UPDATE tasks SET {set_clause} WHERE id=%s RETURNING id, title, status"
                        ),
                        tuple(values),
                    )
                    row = cursor.fetchone()
                else:
                    cursor.execute(
                        format_sql(f"UPDATE tasks SET {set_clause} WHERE id=%s"),
                        tuple(values),
                    )
                    cursor.execute(
                        format_sql("SELECT id, title, status FROM tasks WHERE id=%s"),
                        (task_id,),
                    )
                    row = cursor.fetchone()
        task = _row_to_task(row)
        logger.info(
            "demoboard.lifecycle action=task.update.completed task_id=%s status=%s",
            task_id,
            task.status,
        )
        return task


@app.delete("/tasks/{task_id}", status_code=204)
def delete_task(task_id: int) -> None:
    with tracer.start_as_current_span("tasks.delete") as span:
        span.set_attribute("task.id", task_id)
        logger.info("demoboard.lifecycle action=task.delete.started task_id=%s", task_id)
        with get_db() as conn:
            with get_cursor(conn) as cursor:
                cursor.execute(format_sql("DELETE FROM tasks WHERE id=%s"), (task_id,))
                if cursor.rowcount == 0:
                    raise HTTPException(status_code=404, detail="Task not found")
        logger.info("demoboard.lifecycle action=task.delete.completed task_id=%s", task_id)


@app.post("/tasks/{task_id}/start-job")
def start_job(task_id: int) -> dict:
    if not ENABLE_WORKER:
        raise HTTPException(
            status_code=503,
            detail="Worker disabled in light mode",
        )
    with tracer.start_as_current_span("tasks.start_job") as span:
        span.set_attribute("task.id", task_id)
        logger.info("demoboard.lifecycle action=task.process.started task_id=%s", task_id)
        logger.info(
            "demoboard.lifecycle action=task.process.database_status_update.started task_id=%s status=processing",
            task_id,
        )
        with get_db() as conn:
            with get_cursor(conn) as cursor:
                _ensure_task_exists(cursor, task_id)
                cursor.execute(
                    format_sql("UPDATE tasks SET status='processing' WHERE id=%s"), (task_id,)
                )
        logger.info(
            "demoboard.lifecycle action=task.process.database_status_update.completed task_id=%s status=processing",
            task_id,
        )
        logger.info("demoboard.lifecycle action=task.process.queue_publish.started task_id=%s", task_id)
        publish_job({"task_id": task_id})
        job_start_counter.add(1, {"queue.name": "jobs"})
        logger.info("demoboard.lifecycle action=task.process.queue_publish.completed task_id=%s", task_id)
        logger.info("demoboard.lifecycle action=task.process.completed task_id=%s", task_id)
        return {"message": "Job started", "task_id": task_id}
