from pydantic import BaseModel, Field


class TaskBase(BaseModel):
    title: str = Field(..., min_length=1)
    status: str = "pending"


class TaskCreate(BaseModel):
    title: str = Field(..., min_length=1)


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1)
    status: str | None = None


class Task(TaskBase):
    id: int
