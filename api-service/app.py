import os
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
from worker_queue import publish_job

VALID_STATUSES = {"pending", "processing", "completed"}
ENABLE_WORKER = os.getenv("ENABLE_WORKER", "true").lower() == "true"

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
    return {"status": "ok"}


@app.middleware("http")
async def observe_requests(request: Request, call_next):
    start = time.perf_counter()
    span_name = f"{request.method} {request.url.path}"
    with tracer.start_as_current_span(span_name, kind=SpanKind.SERVER) as span:
        span.set_attribute("http.method", request.method)
        span.set_attribute("url.path", request.url.path)
        try:
            response = await call_next(request)
        except Exception as exc:
            duration_ms = (time.perf_counter() - start) * 1000
            labels = {
                "http.method": request.method,
                "url.path": request.url.path,
                "http.status_code": 500,
            }
            request_counter.add(1, labels)
            request_duration.record(duration_ms, labels)
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR))
            logger.exception("Unhandled request error")
            raise

        duration_ms = (time.perf_counter() - start) * 1000
        labels = {
            "http.method": request.method,
            "url.path": request.url.path,
            "http.status_code": response.status_code,
        }
        request_counter.add(1, labels)
        request_duration.record(duration_ms, labels)
        span.set_attribute("http.status_code", response.status_code)
        if response.status_code >= 500:
            span.set_status(Status(StatusCode.ERROR))
        return response


@app.post("/tasks", response_model=Task, status_code=201)
def create_task(task: TaskCreate) -> Task:
    with tracer.start_as_current_span("tasks.create") as span:
        span.set_attribute("task.title", task.title)
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
        logger.info("Task created")
        return created_task


@app.get("/tasks", response_model=List[Task])
def list_tasks() -> List[Task]:
    with tracer.start_as_current_span("tasks.list") as span:
        with get_db() as conn:
            with get_cursor(conn) as cursor:
                cursor.execute(format_sql("SELECT id, title, status FROM tasks ORDER BY id"))
                rows = cursor.fetchall()
        span.set_attribute("tasks.count", len(rows))
        return [_row_to_task(row) for row in rows]


@app.get("/tasks/{task_id}", response_model=Task)
def get_task(task_id: int) -> Task:
    with tracer.start_as_current_span("tasks.get") as span:
        span.set_attribute("task.id", task_id)
        with get_db() as conn:
            with get_cursor(conn) as cursor:
                cursor.execute(
                    format_sql("SELECT id, title, status FROM tasks WHERE id=%s"), (task_id,)
                )
                row = cursor.fetchone()
                if row is None:
                    raise HTTPException(status_code=404, detail="Task not found")
        return _row_to_task(row)


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
        return _row_to_task(row)


@app.delete("/tasks/{task_id}", status_code=204)
def delete_task(task_id: int) -> None:
    with tracer.start_as_current_span("tasks.delete") as span:
        span.set_attribute("task.id", task_id)
        with get_db() as conn:
            with get_cursor(conn) as cursor:
                cursor.execute(format_sql("DELETE FROM tasks WHERE id=%s"), (task_id,))
                if cursor.rowcount == 0:
                    raise HTTPException(status_code=404, detail="Task not found")


@app.post("/tasks/{task_id}/start-job")
def start_job(task_id: int) -> dict:
    if not ENABLE_WORKER:
        raise HTTPException(
            status_code=503,
            detail="Worker disabled in light mode",
        )
    with tracer.start_as_current_span("tasks.start_job") as span:
        span.set_attribute("task.id", task_id)
        with get_db() as conn:
            with get_cursor(conn) as cursor:
                _ensure_task_exists(cursor, task_id)
                cursor.execute(
                    format_sql("UPDATE tasks SET status='processing' WHERE id=%s"), (task_id,)
                )
        publish_job({"task_id": task_id})
        job_start_counter.add(1, {"queue.name": "jobs"})
        logger.info("Job enqueued")
        return {"message": "Job started", "task_id": task_id}
