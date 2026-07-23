from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class User(Timestamped, Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(16), default="user", index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    questions: Mapped[list["Question"]] = relationship(back_populates="owner", foreign_keys="Question.owner_id")


class Question(Timestamped, Base):
    __tablename__ = "questions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String, default="")
    type: Mapped[str] = mapped_column(String, default="")
    domain: Mapped[str] = mapped_column(String, default="")
    difficulty: Mapped[str] = mapped_column(String, default="")
    knowledge_points: Mapped[str] = mapped_column("knowledgePoints", String, default="")
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text, default="")
    solution: Mapped[str] = mapped_column(Text, default="")
    expert_id: Mapped[str] = mapped_column("expertId", String, default="")
    subject: Mapped[str] = mapped_column(String, default="")
    status: Mapped[str] = mapped_column(String, default="pending", index=True)
    batch_id: Mapped[Optional[str]] = mapped_column("batchId", String, nullable=True)
    owner_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    current_version_created_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    current_version_author_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    current_version_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    owner: Mapped[Optional["User"]] = relationship(back_populates="questions", foreign_keys=[owner_id])
    current_version_author: Mapped[Optional["User"]] = relationship(foreign_keys=[current_version_author_id])
    check_results: Mapped[list["CheckResult"]] = relationship(back_populates="question", cascade="all, delete-orphan")
    versions: Mapped[list["QuestionVersion"]] = relationship(back_populates="question", cascade="all, delete-orphan")


class QuestionVersion(Base):
    __tablename__ = "question_versions"
    __table_args__ = (UniqueConstraint("question_id", "version_number", name="uq_question_version_number"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(JSONB, default=dict)
    version_created_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    author_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    change_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    question: Mapped[Question] = relationship(back_populates="versions")
    author: Mapped[Optional["User"]] = relationship(foreign_keys=[author_id])


class CheckResult(Timestamped, Base):
    __tablename__ = "check_results"
    __table_args__ = (UniqueConstraint("question_id", "check_type", name="uq_check_result_question_type"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"), index=True)
    check_type: Mapped[str] = mapped_column(String)
    result: Mapped[str] = mapped_column(String, default="pending")
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)
    raw_responses: Mapped[list] = mapped_column("rawResponses", JSONB, default=list)
    question: Mapped[Question] = relationship(back_populates="check_results")


class CheckBatch(Timestamped, Base):
    __tablename__ = "check_batches"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    status: Mapped[str] = mapped_column(String, default="queued", index=True)
    check_types: Mapped[list] = mapped_column(JSONB, default=list)
    priority: Mapped[str] = mapped_column(String, default="batch", index=True)
    deadline_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    estimated_complete_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    total_count: Mapped[int] = mapped_column(Integer, default=0)
    completed_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    manual_review_count: Mapped[int] = mapped_column(Integer, default=0)


class CheckRun(Timestamped, Base):
    __tablename__ = "check_runs"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_check_run_idempotency"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"), index=True)
    requested_by_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    batch_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("check_batches.id", ondelete="SET NULL"), nullable=True)
    check_types: Mapped[list] = mapped_column(JSONB, default=list)
    priority: Mapped[str] = mapped_column(String, default="interactive", index=True)
    status: Mapped[str] = mapped_column(String, default="queued", index=True)
    idempotency_key: Mapped[str] = mapped_column(String)
    prompt_version: Mapped[str] = mapped_column(String, default="v1")
    model_versions: Mapped[dict] = mapped_column(JSONB, default=dict)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class CheckWorkItem(Timestamped, Base):
    __tablename__ = "check_work_items"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_work_item_idempotency"),
        Index("ix_work_item_claim", "status", "available_at", "priority"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("check_runs.id", ondelete="CASCADE"), index=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"), index=True)
    check_type: Mapped[str] = mapped_column(String)
    stage: Mapped[str] = mapped_column(String)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    provider: Mapped[str] = mapped_column(String, default="rule", index=True)
    priority: Mapped[str] = mapped_column(String, default="interactive")
    queue_owner_id: Mapped[int] = mapped_column(Integer, default=0, index=True)
    status: Mapped[str] = mapped_column(String, default="queued", index=True)
    idempotency_key: Mapped[str] = mapped_column(String)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    result: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    error_status_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    attempt_no: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    lease_owner: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    manual_review_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # 基准与运营指标使用。started_at 保留首次领取时间，execution_ms 累计实际执行时间（不含排队/退避）。
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    execution_ms: Mapped[float] = mapped_column(Float, default=0)


class CheckEvent(Base):
    __tablename__ = "check_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("check_runs.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
