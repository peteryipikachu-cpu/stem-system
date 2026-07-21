from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, Field, field_validator

DEFAULT_CHECK_TYPES = ["latex", "difficulty", "answer", "synthesis"]
VALID_CHECK_TYPES = set(DEFAULT_CHECK_TYPES)


class QuestionInput(BaseModel):
    title: str = ""
    type: str = ""
    domain: str = ""
    difficulty: str = ""
    knowledgePoints: str = ""
    question: str = ""
    answer: str = ""
    solution: str = ""
    expertId: str = ""
    subject: str = "其他"
    batchId: Optional[str] = None


class QuestionCreate(BaseModel):
    items: list[QuestionInput] = Field(min_length=1)


class QuestionUpdate(QuestionInput):
    currentVersion: int = Field(ge=1)
    changeNote: Optional[str] = Field(default=None, max_length=1000)

    @field_validator("question")
    @classmethod
    def require_question(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("题目不能为空")
        return value


class CheckRequest(BaseModel):
    checkTypes: list[str] = Field(default_factory=lambda: DEFAULT_CHECK_TYPES.copy())


class BatchCheckRequest(CheckRequest):
    questionIds: list[int] = Field(min_length=1)
    deadlineAt: Optional[datetime] = None


class AcceptedRun(BaseModel):
    checkRunId: UUID
    status: str


class AcceptedBatch(BaseModel):
    batchId: UUID
    runIds: list[UUID]
    status: str
    deadlineAt: Optional[datetime] = None


class RunView(BaseModel):
    id: UUID
    questionId: int
    batchId: Optional[UUID]
    checkTypes: list[str]
    priority: str
    status: str
    createdAt: datetime
    startedAt: Optional[datetime]
    completedAt: Optional[datetime]


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=256)
    role: str = Field(default="user", pattern=r"^(user|admin)$")
    expiresAt: Optional[datetime] = None

    @field_validator("expiresAt")
    @classmethod
    def validate_expiration(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        if value <= datetime.now(timezone.utc):
            raise ValueError("使用期限必须晚于当前时间")
        return value


class PasswordReset(BaseModel):
    password: str = Field(min_length=8, max_length=256)
