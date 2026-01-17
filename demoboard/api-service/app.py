import os
from typing import List

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from db import DB_BACKEND, format_sql, get_cursor, get_db, init_db
from models import Task, TaskCreate, TaskUpdate
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


@app.post("/tasks", response_model=Task, status_code=201)
def create_task(task: TaskCreate) -> Task:
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
            return _row_to_task(row)


@app.get("/tasks", response_model=List[Task])
def list_tasks() -> List[Task]:
    with get_db() as conn:
        with get_cursor(conn) as cursor:
            cursor.execute(format_sql("SELECT id, title, status FROM tasks ORDER BY id"))
            rows = cursor.fetchall()
            return [_row_to_task(row) for row in rows]


@app.get("/tasks/{task_id}", response_model=Task)
def get_task(task_id: int) -> Task:
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
    with get_db() as conn:
        with get_cursor(conn) as cursor:
            _ensure_task_exists(cursor, task_id)
            cursor.execute(
                format_sql("UPDATE tasks SET status='processing' WHERE id=%s"), (task_id,)
            )
    publish_job({"task_id": task_id})
    return {"message": "Job started", "task_id": task_id}
