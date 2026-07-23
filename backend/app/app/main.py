from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from .config import get_settings
from .audit_models import ensure_audit_model_available, get_audit_model
from .auth import SESSION_COOKIE, create_session_token, ensure_initial_admin, get_current_user, hash_password, require_admin, user_view, verify_password
from .db import SessionLocal, engine, get_session
from .models import Base, CheckBatch, CheckEvent, CheckRun, CheckWorkItem, Question, QuestionVersion, User
from .queue import provider_limit
from .schemas import AcceptedBatch, AcceptedRun, BatchCheckRequest, CheckRequest, LoginRequest, PasswordReset, QuestionCreate, QuestionUpdate, UserCreate
from .services import ActiveModelConflictError, create_run, question_json, question_snapshot_json, question_version_json

settings = get_settings()
redis = Redis.from_url(settings.redis_url, decode_responses=True)

ACTIVE_QUEUE_STATUSES = {"queued", "running", "blocked"}
QUEUE_MONITOR_STATUSES = ACTIVE_QUEUE_STATUSES | {"manual_review"}
TERMINAL_WORK_STATUSES = {"completed", "failed", "dead", "manual_review", "cancelled"}
DEPENDENCY_STALL_GRACE_SECONDS = 30


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with SessionLocal() as session:
        await ensure_initial_admin(session)
    yield
    await redis.aclose()
    await engine.dispose()


app = FastAPI(title="STEM 题目审核 API", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[item for item in settings.cors_origins.split(",") if item], allow_methods=["*"], allow_headers=["*"], allow_credentials=True)


def parse_date(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(422, f"invalid datetime: {value}") from exc


def default_batch_deadline() -> datetime:
    local_now = datetime.now(ZoneInfo(settings.batch_deadline_timezone))
    days_until_monday = (7 - local_now.weekday()) % 7 or 7
    deadline = (local_now + timedelta(days=days_until_monday)).replace(
        hour=settings.batch_deadline_hour, minute=0, second=0, microsecond=0,
    )
    return deadline.astimezone(timezone.utc)


def estimate_batch_seconds(question_count: int, check_types: list[str], model_id: Optional[str]) -> float:
    model = get_audit_model(model_id)
    counts: dict[str, int] = {}
    for check_type in check_types:
        if check_type in {"difficulty", "answer"}:
            counts["solve"] = counts.get("solve", 0) + model.pass_k
            counts["equivalence"] = counts.get("equivalence", 0) + 1
        elif check_type == "synthesis":
            counts["synthesis"] = counts.get("synthesis", 0) + 1
    lanes: dict[str, tuple[int, int]] = {}
    for stage, count in counts.items():
        limit = provider_limit(settings, model.provider, stage)
        prior, _ = lanes.get(limit.lane, (0, limit.lane_concurrency))
        lanes[limit.lane] = (prior + count, limit.lane_concurrency)
    return max((question_count * count / max(1, concurrency) * settings.batch_estimated_model_p95_seconds
                for count, concurrency in lanes.values()), default=0.0)


async def batch_view(session: AsyncSession, batch: CheckBatch) -> dict[str, Any]:
    status_rows = (await session.execute(
        select(CheckWorkItem.status, func.count(CheckWorkItem.id))
        .join(CheckRun, CheckRun.id == CheckWorkItem.run_id)
        .where(CheckRun.batch_id == batch.id)
        .group_by(CheckWorkItem.status)
    )).all()
    provider_rows = (await session.execute(
        select(CheckWorkItem.provider, CheckWorkItem.status, func.count(CheckWorkItem.id))
        .join(CheckRun, CheckRun.id == CheckWorkItem.run_id)
        .where(CheckRun.batch_id == batch.id)
        .group_by(CheckWorkItem.provider, CheckWorkItem.status)
    )).all()
    counts = {name: count for name, count in status_rows}
    providers: dict[str, dict[str, int]] = {}
    for provider, item_status, count in provider_rows:
        providers.setdefault(provider, {})[item_status] = count
    now = datetime.now(timezone.utc)
    return {
        "id": str(batch.id),
        "status": batch.status,
        "priority": batch.priority,
        "checkTypes": batch.check_types,
        "totalCount": batch.total_count,
        "completedCount": batch.completed_count,
        "failedCount": batch.failed_count,
        "manualReviewCount": batch.manual_review_count,
        "queuedCount": counts.get("queued", 0) + counts.get("blocked", 0),
        "runningCount": counts.get("running", 0),
        "providerWaiting": providers,
        "deadlineAt": batch.deadline_at.isoformat() if batch.deadline_at else None,
        "estimatedCompleteAt": batch.estimated_complete_at.isoformat() if batch.estimated_complete_at else None,
        "secondsToDeadline": max(0, int((batch.deadline_at - now).total_seconds())) if batch.deadline_at else None,
        "createdAt": batch.created_at.isoformat(),
    }


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _queue_user_view(user: Optional[User]) -> Optional[dict[str, Any]]:
    return {"id": user.id, "username": user.username} if user else None


def _work_item_view(work: CheckWorkItem) -> dict[str, Any]:
    return {
        "id": str(work.id), "checkType": work.check_type, "stage": work.stage,
        "provider": work.provider, "status": work.status, "attemptNo": work.attempt_no,
        "createdAt": _iso(work.created_at), "updatedAt": _iso(work.updated_at),
        "availableAt": _iso(work.available_at), "startedAt": _iso(work.started_at),
        "completedAt": _iso(work.completed_at), "leaseOwner": work.lease_owner,
        "leaseExpiresAt": _iso(work.lease_expires_at), "error": work.error,
        "errorCode": work.error_code, "errorStatusCode": work.error_status_code,
    }


def _blocked_dependency_is_ready(work: CheckWorkItem, run_works: list[CheckWorkItem]) -> bool:
    if work.stage == "equivalence":
        prerequisites = [item for item in run_works if item.check_type == work.check_type and item.stage == "solve"]
    else:
        prerequisites = [item for item in run_works if item.check_type == "latex" and item.stage == "check"]
    return bool(prerequisites) and all(item.status in TERMINAL_WORK_STATUSES for item in prerequisites)


def queue_run_diagnosis(run_works: list[CheckWorkItem], now: datetime, worker_online: bool,
                        queue_wait_seconds: int) -> dict[str, str]:
    """Classify a run without treating a valid long model call as an outage."""
    active = [item for item in run_works if item.status in ACTIVE_QUEUE_STATUSES]
    if not active:
        return {"health": "normal", "label": "已转人工复核", "reason": "当前没有活跃工作项"}
    if not worker_online:
        return {"health": "stuck", "label": "Worker 离线", "reason": "存在活跃任务，但未检测到存活 Worker 心跳"}
    expired = [item for item in active if item.status == "running" and item.lease_expires_at and item.lease_expires_at < now]
    if expired:
        return {"health": "stuck", "label": "运行租约过期", "reason": "模型工作项的租约已过期，等待 Worker 回收"}
    stalled_dependencies = [
        item for item in active
        if item.status == "blocked" and item.updated_at
        and (now - item.updated_at).total_seconds() > DEPENDENCY_STALL_GRACE_SECONDS
        and _blocked_dependency_is_ready(item, run_works)
    ]
    if stalled_dependencies:
        return {"health": "stuck", "label": "依赖未唤醒", "reason": "前置工作项已完成，但后续工作项仍未进入队列"}
    overdue = [
        item for item in active
        if item.status == "queued" and item.available_at <= now and item.created_at
        and (now - item.created_at).total_seconds() > queue_wait_seconds
    ]
    if overdue:
        return {"health": "attention", "label": "排队时间过长", "reason": f"可执行任务等待已超过 {queue_wait_seconds // 60} 分钟"}
    if any(item.status == "queued" and item.available_at > now for item in active):
        return {"health": "normal", "label": "重试等待", "reason": "任务处于退避期，尚未到可执行时间"}
    if any(item.status == "blocked" for item in active):
        return {"health": "normal", "label": "等待前置检查", "reason": "前置检查尚未完成"}
    if any(item.status == "running" for item in active):
        return {"health": "normal", "label": "执行中", "reason": "Worker 正在处理模型调用"}
    return {"health": "normal", "label": "等待调度", "reason": "任务已进入待调度队列"}


async def worker_heartbeats() -> list[dict[str, Any]]:
    keys = [key async for key in redis.scan_iter(match="stem:workers:heartbeat:*")]
    if not keys:
        return []
    values = await redis.mget(keys)
    heartbeats: list[dict[str, Any]] = []
    for key, value in zip(keys, values):
        try:
            seen_at = datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (TypeError, ValueError):
            continue
        heartbeats.append({"id": key.rsplit(":", 1)[-1], "seenAt": seen_at.isoformat()})
    return sorted(heartbeats, key=lambda item: item["seenAt"], reverse=True)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


def question_scope(current_user: User):
    return True if current_user.role == "admin" else Question.owner_id == current_user.id


async def get_visible_question(session: AsyncSession, question_id: int, current_user: User, *, with_results: bool = False) -> Question:
    stmt = select(Question).where(Question.id == question_id, question_scope(current_user)).options(
        selectinload(Question.owner),
        selectinload(Question.current_version_author),
    )
    if with_results:
        stmt = stmt.options(selectinload(Question.check_results))
    question = await session.scalar(stmt)
    if not question:
        raise HTTPException(404, "Not found")
    return question


async def get_visible_run(session: AsyncSession, run_id: uuid.UUID, current_user: User) -> CheckRun:
    stmt = select(CheckRun).join(Question).where(CheckRun.id == run_id, question_scope(current_user))
    run = await session.scalar(stmt)
    if not run:
        raise HTTPException(404, "Not found")
    return run


@app.post("/api/auth/login")
async def login(payload: LoginRequest, response: Response, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    user = await session.scalar(select(User).where(User.username == payload.username))
    if not user or not user.is_active or (user.expires_at is not None and user.expires_at <= datetime.now(timezone.utc)) or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    response.set_cookie(SESSION_COOKIE, create_session_token(user), httponly=True, samesite="lax", max_age=settings.auth_session_hours * 3600, secure=settings.auth_cookie_secure)
    return user_view(user)


@app.post("/api/auth/logout")
async def logout(response: Response) -> dict[str, bool]:
    response.delete_cookie(SESSION_COOKIE, httponly=True, samesite="lax")
    return {"success": True}


@app.get("/api/auth/me")
async def me(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return user_view(current_user)


@app.get("/api/users")
async def list_users(_: User = Depends(require_admin), session: AsyncSession = Depends(get_session)) -> list[dict[str, Any]]:
    users = (await session.scalars(select(User).order_by(User.created_at.desc()))).all()
    return [user_view(user) for user in users]


@app.post("/api/users", status_code=status.HTTP_201_CREATED)
async def create_user(payload: UserCreate, _: User = Depends(require_admin), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    exists = await session.scalar(select(User.id).where(User.username == payload.username))
    if exists:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户名已存在")
    user = User(username=payload.username, password_hash=hash_password(payload.password), role=payload.role, expires_at=payload.expiresAt)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return {**user_view(user), "initialPassword": payload.password}


@app.put("/api/users/{user_id}/password")
async def reset_password(user_id: int, payload: PasswordReset, _: User = Depends(require_admin), session: AsyncSession = Depends(get_session)) -> dict[str, bool]:
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(404, "Not found")
    user.password_hash = hash_password(payload.password)
    await session.commit()
    return {"success": True}


@app.get("/api/questions")
async def list_questions(request: Request, current_user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    query = request.query_params
    page = max(1, int(query.get("page", "1")))
    page_size = min(100, max(1, int(query.get("pageSize", "20"))))
    clauses = []
    search = query.get("search", "").strip()
    if search:
        token = f"%{search}%"
        clauses.append(or_(Question.question.ilike(token), Question.title.ilike(token), Question.expert_id.ilike(token), Question.domain.ilike(token)))
    if query.get("status"):
        clauses.append(Question.status == query["status"])
    for column, low, high in ((Question.created_at, query.get("createdAtFrom"), query.get("createdAtTo")), (Question.updated_at, query.get("updatedAtFrom"), query.get("updatedAtTo"))):
        if parsed := parse_date(low): clauses.append(column >= parsed)
        if parsed := parse_date(high): clauses.append(column <= parsed)
    clauses.append(question_scope(current_user))
    where = and_(*clauses)
    all_rows = query.get("all") == "true"
    stmt = select(Question).where(where).options(
        selectinload(Question.check_results),
        selectinload(Question.owner),
        selectinload(Question.current_version_author),
    ).order_by(Question.id.desc())
    if not all_rows:
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    total = await session.scalar(select(func.count()).select_from(Question).where(where)) or 0
    questions = (await session.scalars(stmt)).unique().all()
    qualified = query.get("qualified")
    if qualified:
        required = {"latex", "difficulty", "answer", "synthesis"}
        def is_qualified(item: Question) -> bool:
            values = {result.check_type: result.result for result in item.check_results}
            return all(values.get(check_type) == "pass" for check_type in required)
        questions = [item for item in questions if is_qualified(item) == (qualified == "true")]
        total = len(questions)
    return {"total": total, "items": [question_json(item) for item in questions], "page": page, "pageSize": page_size}


@app.post("/api/questions", status_code=status.HTTP_201_CREATED)
async def create_questions(payload: QuestionCreate, current_user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    values = [item.question.strip() for item in payload.items if item.question.strip()]
    existing = set((await session.scalars(select(Question.question).where(Question.question.in_(values), question_scope(current_user)))).all()) if values else set()
    new_items = [item for item in payload.items if item.question.strip() and item.question.strip() not in existing]
    created: list[Question] = []
    for item in new_items:
        question = Question(title=item.title, type=item.type, domain=item.domain, difficulty=item.difficulty,
            knowledge_points=item.knowledgePoints, question=item.question, answer=item.answer, solution=item.solution,
            expert_id=item.expertId, subject=item.subject or "其他", batch_id=item.batchId, status="pending", owner_id=current_user.id,
            current_version=1, current_version_created_at=datetime.now(timezone.utc), current_version_author_id=current_user.id)
        session.add(question); created.append(question)
    await session.commit()
    return {"created": len(created), "skipped": len(payload.items) - len(created), "ids": [item.id for item in created]}


@app.put("/api/questions/{question_id}")
async def update_question(question_id: int, payload: QuestionUpdate, current_user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    stmt = (
        select(Question)
        .where(Question.id == question_id, question_scope(current_user))
        .options(
            selectinload(Question.check_results),
            selectinload(Question.owner),
            selectinload(Question.current_version_author),
        )
        .with_for_update()
    )
    question = await session.scalar(stmt)
    if not question:
        raise HTTPException(404, "Not found")

    active_run = await session.scalar(
        select(CheckRun.id)
        .where(CheckRun.question_id == question_id, CheckRun.status.in_(["queued", "running", "cancelling"]))
        .limit(1)
    )
    if active_run:
        raise HTTPException(409, "该题目存在进行中的质检任务，请先完成或取消后再编辑")
    if question.current_version != payload.currentVersion:
        raise HTTPException(409, "题目已有新版本，请刷新页面后再保存")

    editable = {
        "title": payload.title,
        "type": payload.type,
        "domain": payload.domain,
        "difficulty": payload.difficulty,
        "knowledge_points": payload.knowledgePoints,
        "question": payload.question,
        "answer": payload.answer,
        "solution": payload.solution,
        "expert_id": payload.expertId,
        "subject": payload.subject or "其他",
    }
    if all(getattr(question, field) == value for field, value in editable.items()):
        raise HTTPException(422, "未检测到题目内容改动，未创建新版本")

    archived = QuestionVersion(
        question_id=question.id,
        version_number=question.current_version,
        snapshot=question_snapshot_json(question),
        version_created_at=question.current_version_created_at or question.created_at,
        author_id=question.current_version_author_id or question.owner_id,
        change_note=question.current_version_note,
    )
    session.add(archived)
    for field, value in editable.items():
        setattr(question, field, value)
    question.current_version += 1
    question.current_version_created_at = datetime.now(timezone.utc)
    question.current_version_author_id = current_user.id
    question.current_version_note = payload.changeNote.strip() if payload.changeNote else None
    question.status = "pending"
    question.batch_id = None
    question.check_results.clear()
    await session.commit()

    refreshed = await get_visible_question(session, question_id, current_user, with_results=True)
    return question_json(refreshed)


@app.get("/api/questions/{question_id}/versions")
async def list_question_versions(question_id: int, current_user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    await get_visible_question(session, question_id, current_user)
    versions = (await session.scalars(
        select(QuestionVersion)
        .where(QuestionVersion.question_id == question_id)
        .options(selectinload(QuestionVersion.author))
        .order_by(QuestionVersion.version_number.desc())
    )).all()
    return {"items": [question_version_json(version) for version in versions]}


@app.get("/api/questions/{question_id}/versions/{version_number}")
async def get_question_version(question_id: int, version_number: int, current_user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    await get_visible_question(session, question_id, current_user)
    version = await session.scalar(
        select(QuestionVersion)
        .where(QuestionVersion.question_id == question_id, QuestionVersion.version_number == version_number)
        .options(selectinload(QuestionVersion.author))
    )
    if not version:
        raise HTTPException(404, "Not found")
    return question_version_json(version, include_snapshot=True)


@app.get("/api/questions/{question_id}")
async def get_question(question_id: int, current_user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    question = await get_visible_question(session, question_id, current_user, with_results=True)
    data = question_json(question)
    active_run = await session.scalar(
        select(CheckRun)
        .where(CheckRun.question_id == question_id, CheckRun.status.in_(["queued", "running", "cancelling"]))
        .order_by(CheckRun.created_at.desc())
        .limit(1)
    )
    if active_run:
        data["activeCheckRun"] = run_view(active_run)
    return data


@app.delete("/api/questions/{question_id}")
async def delete_question(question_id: int, current_user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, bool]:
    question = await get_visible_question(session, question_id, current_user)
    await session.delete(question); await session.commit()
    return {"success": True}


@app.post("/api/questions/{question_id}/check", response_model=AcceptedRun, status_code=status.HTTP_202_ACCEPTED)
async def start_check(question_id: int, payload: CheckRequest, current_user: User = Depends(get_current_user), idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    await get_visible_question(session, question_id, current_user)
    key = idempotency_key or f"interactive:{question_id}:{uuid.uuid4()}"
    try:
        model = get_audit_model(payload.model)
        if any(check_type != "latex" for check_type in payload.checkTypes):
            model = ensure_audit_model_available(settings, model.id)
        run = await create_run(session, redis, question_id, payload.checkTypes, key, model_id=model.id,
                               requested_by_user_id=current_user.id)
        await session.commit()
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ActiveModelConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"checkRunId": run.id, "status": run.status}


@app.post("/api/check-batches", response_model=AcceptedBatch, status_code=status.HTTP_202_ACCEPTED)
async def start_batch(payload: BatchCheckRequest, current_user: User = Depends(get_current_user), idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    for question_id in payload.questionIds:
        await get_visible_question(session, question_id, current_user)
    try:
        model = get_audit_model(payload.model)
        if any(check_type != "latex" for check_type in payload.checkTypes):
            model = ensure_audit_model_available(settings, model.id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    active_runs = (await session.scalars(select(CheckRun).where(
        CheckRun.question_id.in_(payload.questionIds),
        CheckRun.status.in_(["queued", "running", "cancelling"]),
    ))).all()
    if any(get_audit_model((run.model_versions or {}).get("id")).id != model.id for run in active_runs):
        raise HTTPException(409, "存在使用其他模型的进行中质检，请等待完成或取消后再切换模型")
    deadline = payload.deadlineAt or default_batch_deadline()
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    estimated_seconds = estimate_batch_seconds(len(payload.questionIds), payload.checkTypes, model.id)
    batch = CheckBatch(
        check_types=payload.checkTypes,
        total_count=len(payload.questionIds),
        priority="batch",
        deadline_at=deadline,
        estimated_complete_at=datetime.now(timezone.utc) + timedelta(seconds=estimated_seconds),
        status="queued",
    )
    session.add(batch); await session.flush()
    runs = []
    for question_id in payload.questionIds:
        key = f"{idempotency_key or uuid.uuid4()}:{question_id}"
        try: runs.append(await create_run(session, redis, question_id, payload.checkTypes, key, "batch", batch.id, model.id,
                                          current_user.id))
        except ActiveModelConflictError as exc: raise HTTPException(409, str(exc)) from exc
        except LookupError: batch.failed_count += 1
    await session.commit()
    return {"batchId": batch.id, "runIds": [item.id for item in runs], "status": batch.status, "deadlineAt": batch.deadline_at}


def run_view(run: CheckRun) -> dict[str, Any]:
    return {"id": str(run.id), "questionId": run.question_id, "batchId": str(run.batch_id) if run.batch_id else None,
        "checkTypes": run.check_types, "priority": run.priority, "status": run.status,
        "model": run.model_versions or get_audit_model().snapshot(),
        "createdAt": run.created_at.isoformat(), "startedAt": run.started_at.isoformat() if run.started_at else None,
        "completedAt": run.completed_at.isoformat() if run.completed_at else None}


@app.get("/api/check-runs/{run_id}")
async def get_run(run_id: uuid.UUID, current_user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    run = await get_visible_run(session, run_id, current_user)
    return run_view(run)


@app.get("/api/check-batches/{batch_id}")
async def get_batch(batch_id: uuid.UUID, current_user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    batch = await session.get(CheckBatch, batch_id)
    if not batch or not await session.scalar(select(CheckRun.id).join(Question).where(CheckRun.batch_id == batch_id, question_scope(current_user)).limit(1)):
        raise HTTPException(404, "Not found")
    return await batch_view(session, batch)


@app.get("/api/admin/queue")
async def admin_queue_monitor(request: Request, _: User = Depends(require_admin), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """Return a read-only operational view of current audit work for administrators."""
    query = request.query_params
    status_filter = query.get("status", "active").strip()
    status_map = {
        "active": ACTIVE_QUEUE_STATUSES,
        "all": QUEUE_MONITOR_STATUSES,
        "queued": {"queued"},
        "running": {"running"},
        "blocked": {"blocked"},
        "manual_review": {"manual_review"},
    }
    if status_filter not in status_map:
        raise HTTPException(422, "不支持的队列状态筛选")
    health_filter = query.get("health", "all").strip()
    if health_filter not in {"all", "normal", "attention", "stuck"}:
        raise HTTPException(422, "不支持的健康状态筛选")
    provider_filter = query.get("provider", "").strip()
    try:
        page = max(1, int(query.get("page", "1")))
        page_size = min(100, max(1, int(query.get("pageSize", "20"))))
    except ValueError as exc:
        raise HTTPException(422, "分页参数必须为整数") from exc

    now = datetime.now(timezone.utc)
    queue_wait_seconds = max(1, settings.ai_queue_max_wait_ms // 1_000)
    monitor_works = (await session.scalars(
        select(CheckWorkItem).where(CheckWorkItem.status.in_(QUEUE_MONITOR_STATUSES))
    )).all()
    active_works = [work for work in monitor_works if work.status in ACTIVE_QUEUE_STATUSES]
    all_works_by_run: dict[uuid.UUID, list[CheckWorkItem]] = {}
    active_works_by_run: dict[uuid.UUID, list[CheckWorkItem]] = {}
    for work in monitor_works:
        all_works_by_run.setdefault(work.run_id, []).append(work)
        if work.status in ACTIVE_QUEUE_STATUSES:
            active_works_by_run.setdefault(work.run_id, []).append(work)

    heartbeats = await worker_heartbeats()
    worker_online = bool(heartbeats)
    diagnoses = {
        run_id: queue_run_diagnosis(all_works_by_run[run_id], now, worker_online, queue_wait_seconds)
        for run_id in active_works_by_run
    }
    health_counts = {"normal": 0, "attention": 0, "stuck": 0}
    for diagnosis in diagnoses.values():
        health_counts[diagnosis["health"]] += 1

    matching_works = [work for work in monitor_works if work.status in status_map[status_filter]]
    if provider_filter:
        matching_works = [work for work in matching_works if work.provider == provider_filter]
    matching_run_ids = {work.run_id for work in matching_works}
    if health_filter != "all":
        matching_run_ids = {
            run_id for run_id in matching_run_ids
            if diagnoses.get(run_id, {"health": "normal"})["health"] == health_filter
        }

    runs = (await session.scalars(
        select(CheckRun).where(CheckRun.id.in_(matching_run_ids)).order_by(CheckRun.created_at.desc())
    )).all() if matching_run_ids else []
    total = len(runs)
    page_runs = runs[(page - 1) * page_size: page * page_size]
    page_run_ids = [run.id for run in page_runs]

    requested_by = aliased(User)
    question_owner = aliased(User)
    rows = (await session.execute(
        select(CheckRun, Question, requested_by, question_owner)
        .join(Question, Question.id == CheckRun.question_id)
        .outerjoin(requested_by, requested_by.id == CheckRun.requested_by_user_id)
        .outerjoin(question_owner, question_owner.id == Question.owner_id)
        .where(CheckRun.id.in_(page_run_ids))
    )).all() if page_run_ids else []
    row_by_run_id = {run.id: (question, requester, owner) for run, question, requester, owner in rows}
    page_works = (await session.scalars(
        select(CheckWorkItem)
        .where(CheckWorkItem.run_id.in_(page_run_ids))
        .order_by(CheckWorkItem.created_at.asc())
    )).all() if page_run_ids else []
    page_works_by_run: dict[uuid.UUID, list[CheckWorkItem]] = {}
    for work in page_works:
        page_works_by_run.setdefault(work.run_id, []).append(work)

    items = []
    for run in page_runs:
        question, requester, owner = row_by_run_id[run.id]
        works = page_works_by_run.get(run.id, [])
        status_counts: dict[str, int] = {}
        for work in works:
            status_counts[work.status] = status_counts.get(work.status, 0) + 1
        diagnosis = diagnoses.get(run.id, {"health": "normal", "label": "已转人工复核", "reason": "当前没有活跃工作项"})
        items.append({
            "id": str(run.id), "question": {"id": question.id, "title": question.title},
            "requestedBy": _queue_user_view(requester), "questionOwner": _queue_user_view(owner),
            "status": run.status, "priority": run.priority, "checkTypes": run.check_types,
            "model": run.model_versions or get_audit_model().snapshot(),
            "createdAt": _iso(run.created_at), "startedAt": _iso(run.started_at),
            "diagnosis": diagnosis, "workSummary": status_counts,
            "workItems": [_work_item_view(work) for work in works],
        })

    ready_waits = [
        (now - work.created_at).total_seconds() for work in active_works
        if work.status == "queued" and work.available_at <= now and work.created_at
    ]
    status_counts = {state: sum(work.status == state for work in active_works) for state in ACTIVE_QUEUE_STATUSES}
    manual_review_count = await session.scalar(
        select(func.count(CheckWorkItem.id)).where(CheckWorkItem.status == "manual_review")
    ) or 0
    return {
        "generatedAt": now.isoformat(),
        "summary": {
            "workerOnline": worker_online, "workers": heartbeats,
            "queuedCount": status_counts["queued"], "runningCount": status_counts["running"],
            "blockedCount": status_counts["blocked"], "manualReviewCount": manual_review_count,
            "attentionCount": health_counts["attention"], "stuckCount": health_counts["stuck"],
            "oldestReadyWaitSeconds": int(max(ready_waits, default=0)),
        },
        "total": total, "items": items, "page": page, "pageSize": page_size,
    }


@app.get("/api/manual-reviews")
async def list_manual_reviews(_: User = Depends(require_admin), session: AsyncSession = Depends(get_session)) -> list[dict[str, Any]]:
    works = (await session.scalars(
        select(CheckWorkItem)
        .where(CheckWorkItem.status == "manual_review")
        .order_by(CheckWorkItem.manual_review_at.asc())
        .limit(500)
    )).all()
    return [{
        "id": str(work.id), "runId": str(work.run_id), "questionId": work.question_id,
        "checkType": work.check_type, "stage": work.stage, "provider": work.provider,
        "errorCode": work.error_code, "statusCode": work.error_status_code,
        "message": work.error, "attempts": work.attempt_no,
        "manualReviewAt": work.manual_review_at.isoformat() if work.manual_review_at else None,
    } for work in works]


@app.post("/api/check-runs/{run_id}/cancel")
async def cancel_run(run_id: uuid.UUID, current_user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    run = await get_visible_run(session, run_id, current_user)
    if run.status in ("completed", "cancelled"): return {"status": run.status}
    run.status = "cancelled"
    await session.execute(update(CheckWorkItem).where(CheckWorkItem.run_id == run_id, CheckWorkItem.status.in_(["queued", "blocked"])).values(status="cancelled"))
    await session.commit()
    return {"status": "cancelled"}


@app.get("/api/check-runs/{run_id}/events")
async def run_events(run_id: uuid.UUID, request: Request, current_user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    await get_visible_run(session, run_id, current_user)
    after_id = int(request.headers.get("last-event-id", "0") or 0)
    async def generate():
        events = (await session.scalars(select(CheckEvent).where(CheckEvent.run_id == run_id, CheckEvent.id > after_id).order_by(CheckEvent.id))).all()
        for event in events:
            yield f"id: {event.id}\nevent: {event.event_type}\ndata: {json.dumps({'id': event.id, 'type': event.event_type, **event.payload}, ensure_ascii=False, default=str)}\n\n"
        pubsub = redis.pubsub(); await pubsub.subscribe(f"stem:events:{run_id}")
        try:
            while not await request.is_disconnected():
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=15)
                if message: yield f"event: message\ndata: {message['data']}\n\n"
                else: yield "event: ping\ndata: {}\n\n"
        finally:
            await pubsub.unsubscribe(f"stem:events:{run_id}"); await pubsub.aclose()
    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
