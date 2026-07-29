from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
import json
import random
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from typing import Optional, Tuple

import httpx
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings
from .db import SessionLocal
from .audit_models import AuditModel, get_audit_model, model_from_snapshot
from .models import CheckBatch, CheckEvent, CheckResult, CheckRun, CheckWorkItem, Question, QuestionVersion
from .queue import acquire, pop_ready as pop_ready_queue, provider_key_candidates, provider_scope, release
from .schemas import DEFAULT_CHECK_TYPES, VALID_CHECK_TYPES


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def question_json(question: Question, include_results: bool = True) -> dict[str, Any]:
    data = {
        "id": question.id, "title": question.title, "type": question.type, "domain": question.domain,
        "difficulty": question.difficulty, "knowledgePoints": question.knowledge_points,
        "question": question.question, "answer": question.answer, "solution": question.solution,
        "expertId": question.expert_id, "subject": question.subject, "status": question.status,
        "batchId": question.batch_id, "createdAt": question.created_at.isoformat(),
        "updatedAt": question.updated_at.isoformat(),
        "currentVersion": question.current_version,
        "versionCount": question.current_version,
        "versionCreatedAt": question.current_version_created_at.isoformat() if question.current_version_created_at else question.created_at.isoformat(),
        "versionAuthor": {"id": question.current_version_author.id, "username": question.current_version_author.username} if question.current_version_author else None,
        "changeNote": question.current_version_note,
        "owner": {"id": question.owner.id, "username": question.owner.username} if question.owner else None,
    }
    if include_results:
        data["checkResults"] = [check_result_json(item) for item in question.check_results]
    return data


def question_snapshot_json(question: Question) -> dict[str, Any]:
    """Return an immutable, API-compatible representation of the current version."""
    return question_json(question, include_results=True)


def question_version_json(version: QuestionVersion, *, include_snapshot: bool = False) -> dict[str, Any]:
    snapshot = version.snapshot or {}
    result_values = {
        item.get("checkType"): item.get("result")
        for item in snapshot.get("checkResults", [])
        if isinstance(item, dict)
    }
    required_types = {"latex", "difficulty", "answer", "synthesis"}
    if not result_values:
        check_summary = "未质检"
    elif not required_types.issubset(result_values):
        check_summary = "部分质检"
    elif all(result_values.get(check_type) == "pass" for check_type in required_types):
        check_summary = "质检通过"
    elif "manual_review" in result_values.values():
        check_summary = "人工复核"
    else:
        check_summary = "质检未通过"
    data = {
        "version": version.version_number,
        "currentVersion": version.version_number,
        "versionCount": version.version_number,
        "title": snapshot.get("title", ""),
        "status": snapshot.get("status", "pending"),
        "versionCreatedAt": version.version_created_at.isoformat() if version.version_created_at else None,
        "savedAt": version.created_at.isoformat() if version.created_at else None,
        "versionAuthor": {"id": version.author.id, "username": version.author.username} if version.author else snapshot.get("versionAuthor"),
        "changeNote": version.change_note,
        "checkResultCount": len(result_values),
        "checkSummary": check_summary,
    }
    if include_snapshot:
        data = {**snapshot, **data, "isHistorical": True}
    return data


def check_result_json(item: CheckResult) -> dict[str, Any]:
    return {"id": item.id, "questionId": item.question_id, "checkType": item.check_type,
            "result": item.result, "detail": json.dumps(item.detail or {}, ensure_ascii=False),
            "rawResponses": json.dumps(item.raw_responses or [], ensure_ascii=False),
            "createdAt": item.created_at.isoformat(), "updatedAt": item.updated_at.isoformat()}


async def emit(session: AsyncSession, redis: Redis, run_id: uuid.UUID, event_type: str, payload: dict[str, Any]) -> None:
    event = CheckEvent(run_id=run_id, event_type=event_type, payload=payload)
    session.add(event)
    await session.flush()
    message = {"id": event.id, "type": event_type, **payload}
    await redis.publish(f"stem:events:{run_id}", json.dumps(message, ensure_ascii=False, default=str))


async def enqueue(redis: Redis, work: CheckWorkItem) -> None:
    score = max(time.time(), work.available_at.timestamp() if work.available_at else time.time())
    if work.priority == "batch":
        owner = str(work.queue_owner_id or 0)
        await redis.zadd(f"stem:ready:batch:owner:{owner}", {str(work.id): score})
        await redis.zadd("stem:ready:batch:owners", {owner: time.time()}, nx=True)
        return
    await redis.zadd(f"stem:ready:{work.priority}", {str(work.id): score})


async def recover_expired_leases(session: AsyncSession, redis: Redis) -> int:
    expired = (await session.scalars(
        select(CheckWorkItem)
        .where(
            CheckWorkItem.status == "running",
            CheckWorkItem.lease_expires_at.is_not(None),
            CheckWorkItem.lease_expires_at < utcnow(),
        )
        .with_for_update(skip_locked=True)
    )).all()
    for work in expired:
        work.status = "queued"
        work.lease_owner = None
        work.lease_expires_at = None
        work.available_at = utcnow()
        await enqueue(redis, work)
    return len(expired)


async def recover_queued_work(session: AsyncSession, redis: Redis) -> int:
    """将 PostgreSQL 中可执行但未出现在 Redis ready 集合的任务重新投递。

    Redis 是调度加速层，数据库才是任务状态的最终事实来源。ZSET 按工作项 ID
    去重，所以周期性补投不会制造重复执行。
    """
    works = (await session.scalars(select(CheckWorkItem).where(
        CheckWorkItem.status == "queued",
        CheckWorkItem.available_at <= utcnow(),
    ))).all()
    for work in works:
        await enqueue(redis, work)
    return len(works)


async def move_batch_cutoff_to_manual_review(session: AsyncSession, redis: Redis, settings: Settings) -> int:
    """At the final review window, stop starting new model calls and surface work to humans."""
    cutoff = utcnow() + timedelta(minutes=settings.batch_manual_review_cutoff_minutes)
    works = (await session.scalars(
        select(CheckWorkItem)
        .join(CheckRun, CheckRun.id == CheckWorkItem.run_id)
        .join(CheckBatch, CheckBatch.id == CheckRun.batch_id)
        .where(
            CheckBatch.deadline_at.is_not(None),
            CheckBatch.deadline_at <= cutoff,
            CheckWorkItem.status.in_(["queued", "blocked"]),
        )
        .with_for_update(skip_locked=True)
    )).all()
    affected_runs: set[uuid.UUID] = set()
    for work in works:
        if work.status not in {"queued", "blocked"}:
            continue
        await mark_manual_review(
            session,
            redis,
            work,
            error_code="deadline_manual_review",
            status_code=None,
            message="批次接近截止时间，已转人工复核",
        )
        affected_runs.add(work.run_id)
    for run_id in affected_runs:
        run = await session.get(CheckRun, run_id)
        if run:
            await complete_run_if_ready(session, redis, run)
    return len(affected_runs)


def run_audit_model(run: CheckRun) -> AuditModel:
    return model_from_snapshot(run.model_versions)


def work_audit_model(work: CheckWorkItem) -> AuditModel:
    snapshot = (work.payload or {}).get("model")
    if snapshot:
        return model_from_snapshot(snapshot)
    # Preserve the original provider routing for work queued before this release.
    return get_audit_model("gemini-3.1-pro-preview") if work.provider == "gemini" else get_audit_model()


def make_work(run: CheckRun, check_type: str, stage: str, attempt: int, provider: str,
              status: str = "queued", queue_owner_id: int = 0) -> CheckWorkItem:
    # 同一 CheckRun 内保持幂等；人工重检必须能创建新的工作项，不能与历史失败记录冲突。
    key = f"r:{run.id}|q:{run.question_id}|c:{check_type}|s:{stage}|a:{attempt}|v:{run.prompt_version}"
    return CheckWorkItem(run_id=run.id, question_id=run.question_id, check_type=check_type, stage=stage,
                         attempt=attempt, provider=provider, priority=run.priority, queue_owner_id=queue_owner_id, status=status,
                         idempotency_key=key,
                         payload={} if provider == "rule" else {"model": run_audit_model(run).snapshot()})


def make_check_work_items(run: CheckRun, check_types: list[str], queue_owner_id: int) -> list[CheckWorkItem]:
    works: list[CheckWorkItem] = []
    model = run_audit_model(run)
    for check_type in check_types:
        if check_type == "latex":
            works.append(make_work(run, check_type, "check", 0, "rule", queue_owner_id=queue_owner_id))
        elif check_type == "difficulty":
            works.extend(make_work(run, check_type, "solve", i, model.provider, "blocked", queue_owner_id) for i in range(1, model.pass_k + 1))
            works.append(make_work(run, check_type, "equivalence", 0, model.provider, "blocked", queue_owner_id))
        elif check_type == "answer":
            works.extend(make_work(run, check_type, "solve", i, model.provider, "blocked", queue_owner_id) for i in range(1, model.pass_k + 1))
            works.append(make_work(run, check_type, "equivalence", 0, model.provider, "blocked", queue_owner_id))
        elif check_type == "synthesis":
            works.append(make_work(run, check_type, "synthesis", 0, model.provider, "blocked", queue_owner_id))
        else:
            raise ValueError(f"unsupported check type: {check_type}")
    return works


async def add_check_work_items(session: AsyncSession, redis: Redis, run: CheckRun, check_types: list[str], queue_owner_id: int) -> None:
    works = make_check_work_items(run, check_types, queue_owner_id)
    session.add_all(works)
    await session.flush()
    latex_pending = await session.scalar(
        select(CheckWorkItem.id).where(
            CheckWorkItem.run_id == run.id,
            CheckWorkItem.check_type == "latex",
            CheckWorkItem.status.in_(["queued", "blocked", "running"]),
        ).limit(1)
    )
    if not latex_pending:
        for work in works:
            if work.status == "blocked" and work.stage != "equivalence":
                work.status = "queued"
    for work in works:
        if work.status == "queued":
            await enqueue(redis, work)


async def create_run(session: AsyncSession, redis: Redis, question_id: int, check_types: list[str],
                     idempotency_key: str, priority: str = "interactive", batch_id: Optional[uuid.UUID] = None,
                     model_id: Optional[str] = None, requested_by_user_id: Optional[int] = None) -> CheckRun:
    types = list(dict.fromkeys(check_types or DEFAULT_CHECK_TYPES))
    model = get_audit_model(model_id)
    invalid = set(types) - VALID_CHECK_TYPES
    if invalid:
        raise ValueError(f"unsupported check types: {', '.join(sorted(invalid))}")
    existing = await session.scalar(select(CheckRun).where(CheckRun.idempotency_key == idempotency_key))
    if existing:
        if run_audit_model(existing).id != model.id:
            raise ValueError("同一幂等请求已使用其他模型创建，不能切换模型")
        return existing
    question = await session.get(Question, question_id, with_for_update=True)
    if not question:
        raise LookupError("question not found")
    active = await session.scalar(select(CheckRun).where(CheckRun.question_id == question_id,
        CheckRun.status.in_(["queued", "running", "cancelling"])).limit(1))
    if active:
        if run_audit_model(active).id != model.id:
            raise ValueError("该题已有使用其他模型的进行中质检，请等待完成或取消后再切换模型")
        current_types = list(active.check_types or [])
        added_types = [check_type for check_type in types if check_type not in current_types]
        if added_types:
            active.check_types = [*current_types, *added_types]
            await add_check_work_items(session, redis, active, added_types, question.owner_id or 0)
            await emit(session, redis, active.id, "start", {
                "questionId": question_id,
                "checkTypes": added_types,
                "checkRunId": str(active.id),
                "model": model.snapshot(), "added": True,
            })
        return active
    run = CheckRun(question_id=question_id, requested_by_user_id=requested_by_user_id, batch_id=batch_id, check_types=types, priority=priority,
                   status="queued", idempotency_key=idempotency_key, model_versions=model.snapshot())
    question.status = "checking"
    session.add(run)
    await session.flush()
    await add_check_work_items(session, redis, run, types, question.owner_id or 0)
    await emit(session, redis, run.id, "start", {"questionId": question_id, "checkTypes": types,
               "checkRunId": str(run.id), "model": model.snapshot()})
    return run


async def activate_after_latex(session: AsyncSession, redis: Redis, run_id: uuid.UUID) -> None:
    works = (await session.scalars(select(CheckWorkItem).where(CheckWorkItem.run_id == run_id,
             CheckWorkItem.status == "blocked", CheckWorkItem.stage != "equivalence"))).all()
    for work in works:
        work.status = "queued"
        await enqueue(redis, work)


async def activate_equivalence_if_ready(session: AsyncSession, redis: Redis, run_id: uuid.UUID, check_type: str) -> None:
    pending = await session.scalar(select(CheckWorkItem.id).where(CheckWorkItem.run_id == run_id,
        CheckWorkItem.check_type == check_type, CheckWorkItem.stage == "solve",
        CheckWorkItem.status.not_in(["completed", "failed", "dead"])).limit(1))
    if pending:
        return
    work = await session.scalar(select(CheckWorkItem).where(CheckWorkItem.run_id == run_id,
        CheckWorkItem.check_type == check_type, CheckWorkItem.stage == "equivalence", CheckWorkItem.status == "blocked"))
    if work:
        completed_answer = await session.scalar(select(CheckWorkItem.id).where(
            CheckWorkItem.run_id == run_id,
            CheckWorkItem.check_type == check_type,
            CheckWorkItem.stage == "solve",
            CheckWorkItem.status == "completed",
            CheckWorkItem.result.is_not(None),
        ).limit(1))
        if not completed_answer:
            await mark_manual_review(
                session,
                redis,
                work,
                error_code="no_successful_answers",
                status_code=None,
                message="全部独立作答均未完成，暂无可用于结果判断的模型答案",
            )
            return
        work.status = "queued"
        await enqueue(redis, work)


async def recover_ready_dependencies(session: AsyncSession, redis: Redis) -> int:
    """补偿 Worker 中断时错过的依赖唤醒。"""
    blocked = (await session.scalars(select(CheckWorkItem).where(
        CheckWorkItem.status == "blocked", CheckWorkItem.stage == "equivalence"
    ))).all()
    activated = 0
    for work in blocked:
        pending = await session.scalar(select(CheckWorkItem.id).where(
            CheckWorkItem.run_id == work.run_id,
            CheckWorkItem.check_type == work.check_type,
            CheckWorkItem.stage == "solve",
            CheckWorkItem.status.not_in(["completed", "failed", "dead"]),
        ).limit(1))
        if not pending:
            work.status = "queued"
            work.available_at = utcnow()
            await enqueue(redis, work)
            activated += 1
    return activated


async def recover_partial_timeout_finalizations(session: AsyncSession, redis: Redis) -> int:
    """让已有成功作答的超时任务继续完成最终判断。

    旧版本在某一次独立作答超时后，会把同一检查项的全部工作项（包括
    ``equivalence``）一并转人工复核，已得到的答案因此无法展示或参与判断。
    这里只恢复“有成功答案 + 其余仅因可重试超时失败”的最新任务，并且每个
    工作项只补做一次最终判断，避免恢复循环造成额外模型调用。
    """
    works = (await session.scalars(select(CheckWorkItem).join(
        CheckRun, CheckRun.id == CheckWorkItem.run_id,
    ).where(
        CheckWorkItem.stage == "equivalence",
        CheckWorkItem.check_type.in_(["difficulty", "answer"]),
        CheckWorkItem.status == "manual_review",
    ).order_by(CheckRun.created_at.desc()).with_for_update(skip_locked=True))).all()
    restored = 0
    seen: set[tuple[int, str]] = set()
    timeout_codes = {"timeout", "http_502", "http_503", "http_504"}
    for work in works:
        key = (work.question_id, work.check_type)
        if key in seen:
            continue
        seen.add(key)
        run = await session.get(CheckRun, work.run_id)
        if not run:
            continue
        # 同一题目同一检查项仅恢复最新一次审核，不能让更早的历史任务覆盖
        # 用户刚发起的结果。
        newer_run = await session.scalar(select(CheckRun.id).where(
            CheckRun.question_id == work.question_id,
            CheckRun.created_at > run.created_at,
        ))
        if newer_run:
            newer = await session.get(CheckRun, newer_run)
            if newer and work.check_type in (newer.check_types or []):
                continue
        if (work.payload or {}).get("partialFinalizationAttempted"):
            continue
        solves = (await session.scalars(select(CheckWorkItem).where(
            CheckWorkItem.run_id == work.run_id,
            CheckWorkItem.check_type == work.check_type,
            CheckWorkItem.stage == "solve",
        ))).all()
        completed = [item for item in solves if item.status == "completed" and (item.result or {}).get("answer")]
        failed = [item for item in solves if item.status != "completed"]
        if not completed or not failed or not all(item.error_code in timeout_codes for item in failed):
            continue
        work.status = "queued"
        work.error = work.error_code = None
        work.error_status_code = None
        work.manual_review_at = work.completed_at = None
        work.payload = {**(work.payload or {}), "partialFinalizationAttempted": True}
        run = await session.get(CheckRun, work.run_id, with_for_update=True)
        question = await session.get(Question, work.question_id)
        if run:
            run.status = "queued"
            run.completed_at = None
        if question:
            question.status = "checking"
        await enqueue(redis, work)
        restored += 1
    return restored


def latex_check(text: str) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    if text.count("$") % 2:
        errors.append({"location": "分隔符", "description": "$ 数量不成对", "suggestion": "补齐数学公式分隔符"})
    if text.count("\\[") != text.count("\\]"):
        errors.append({"location": "分隔符", "description": "\\[ 与 \\] 不匹配", "suggestion": "补齐显示公式分隔符"})
    if text.count("\\begin{") != text.count("\\end{"):
        errors.append({"location": "环境", "description": "LaTeX 环境不匹配", "suggestion": "检查 begin/end"})
    return {"errors": errors}


StreamObserver = Callable[[dict[str, int]], Awaitable[None]]


async def call_chat(client: httpx.AsyncClient, base_url: str, api_key: Optional[str], body: dict[str, Any], stream: bool = False,
                    on_stream_chunk: Optional[StreamObserver] = None) -> Tuple[str, dict[str, Any]]:
    if not api_key:
        raise ValueError("provider API key not configured")
    headers = {"Authorization": f"Bearer {api_key}"}
    if not stream:
        response = await client.post(f"{base_url}/chat/completions", json=body, headers=headers)
        response.raise_for_status()
        raw = response.json()
        return raw.get("choices", [{}])[0].get("message", {}).get("content", ""), raw
    # `client.stream()` only consumes an HTTP response incrementally.  The
    # OpenAI-compatible gateway must also be told to produce SSE chunks.
    # Copy the payload so callers can safely retain it for auditing/tests.
    body = {**body, "stream": True}
    headers["Accept"] = "text/event-stream"
    content = ""
    usage: Optional[dict[str, Any]] = None
    async with client.stream("POST", f"{base_url}/chat/completions", json=body, headers=headers) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if not line.startswith("data:") or line.strip() == "data: [DONE]":
                continue
            try:
                chunk = json.loads(line[5:].strip())
                usage = chunk.get("usage") or usage
                choices = chunk.get("choices") or []
                if choices:
                    delta = choices[0].get("delta", {}) or {}
                    content_piece = delta.get("content", "") or ""
                    reasoning_piece = delta.get("reasoning_content", delta.get("reasoning", "")) or ""
                    content += content_piece
                    if on_stream_chunk:
                        try:
                            await on_stream_chunk({
                                "receivedChunks": 1,
                                "contentChars": len(content_piece),
                                "reasoningChars": len(reasoning_piece),
                            })
                        except Exception:
                            # 可观测性写入不能让已建立的模型流失败。
                            pass
            except json.JSONDecodeError:
                continue
    return content, {"choices": [{"message": {"content": content}}], "usage": usage}


def solve_prompt(question: str) -> str:
    return f"""你是一位严谨的 STEM 竞赛题解题专家。请独立求解以下题目，并只给出可用于答案比对的最终答案。

输出规则（必须严格遵守）：
1. 只输出答案本身，不要添加“最终答案：”、答案标签、推导、思考过程、解释、Markdown 标题或其他文字。
2. 所有数学公式必须完整地使用 `$...$`（行内）或 `$$...$$`（独立成行）包裹；不得输出裸 LaTeX 命令。
3. 每个公式的花括号、分隔符都必须配对；无法确定时用普通文本说明，不要输出不完整公式。
4. 若题干要求唯一对应关系、唯一选项或唯一结论，必须只输出一组确定答案；禁止输出“或 / 或者 / 任选 / 可能”等备选答案，也不得把对称或等价变换当作额外答案。仅当题干明确要求全部解集时，才完整列出所有解。

题目：
{question}"""


def difficulty_answer_prompt(question: str) -> str:
    return solve_prompt(question)


def normalized_math_answer(answer: Any) -> str:
    """Normalize harmless presentation differences before equivalence judging."""
    value = str(answer or "").strip()
    for opening, closing in (("$$", "$$"), ("$", "$"), ("\\[", "\\]"), ("\\(", "\\)")):
        if value.startswith(opening) and value.endswith(closing) and len(value) >= len(opening) + len(closing):
            value = value[len(opening):len(value) - len(closing)].strip()
            break
    value = re.sub(r"\s+", "", value)
    return value.replace("\\dfrac", "\\frac").replace("\\tfrac", "\\frac").replace("\\left", "").replace("\\right", "")


def equivalence_prompt(question: str, reference_answer: str, answers: list[str]) -> str:
    numbered_answers = "\n".join(f"{index}. {answer}" for index, answer in enumerate(answers, start=1))
    return f"""你是严谨的 STEM 答案等价判定器。结合题目上下文，比较每个模型答案与参考答案是否表达同一个正确结论。题目可能属于数学、物理、化学、生物或其他 STEM 学科。

题目：
{question}

参考答案：{reference_answer}
待判断的模型答案（顺序不可改变）：
{numbered_answers}

判断规则：
1. `$...$`、`$$...$$`、空白、`\\dfrac` 与 `\\frac` 等纯展示差异不影响等价。
2. 按答案表达的语义和题目所求的唯一结论判定，不要机械逐字比较。对于题干或参考答案已明确对象的“数量 + 计数单位/量词”，只要数值相同且没有引入其他含义，省略计数单位或量词仍然等价。例如生物题中参考答案为 `3对`、`3 个`、`3种`，模型答案仅为 `3` 时应标记为 true；反向情形也相同。
3. 不能把会改变量纲、对象、条件、正负号、范围、选项或解集的内容视为等价。尤其在物理、化学题中，带有长度、质量、时间、物质的量、浓度、温度等量纲或单位时，单位通常不可省略。例如 `3 cm` 与 `3`、`3 mol` 与 `3`、`3 或 4` 与 `3` 均为 false。
4. 只在数学值或所要求的唯一结论确实一致时标记为 true；不确定时应依据上述语义规则审慎判断，而不是因文字单位差异直接判 false。
5. 必须按输入顺序返回与模型答案数量完全一致的布尔值。

判定示例：
- 参考答案 `3对`，模型答案 `3`：true。
- 参考答案 `$\\frac{{3}}{{16}}$`，模型答案 `3/16`：true。
- 参考答案 `3 cm`，模型答案 `3`：false。

仅输出合法 JSON，不要 Markdown、解释、推导或其他文本：
{{"equivalences":[true,false]}}"""


def parsed_equivalence_flags(content: str, expected_count: int) -> list[bool]:
    """Parse structured model output, with a text fallback for older responses."""
    parsed: Any = None
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
    if isinstance(parsed, dict) and isinstance(parsed.get("equivalences"), list):
        flags = [
            value if isinstance(value, bool) else str(value).strip().lower() in {"true", "yes"}
            for value in parsed["equivalences"]
        ]
    else:
        flags = [token.lower() in {"yes", "true"} for token in re.findall(r"\b(YES|NO|TRUE|FALSE)\b", content, re.IGNORECASE)]
    return (flags + [False] * expected_count)[:expected_count]


async def judge_equivalences(client: httpx.AsyncClient, *, base_url: str, api_key: Optional[str],
                             model: AuditModel, question_text: str, reference_answer: str, answers: list[str],
                             request_options: Optional[dict[str, Any]] = None,
                             stream: bool = False, on_stream_chunk: Optional[StreamObserver] = None) -> tuple[list[bool], dict[str, Any]]:
    """Return one verdict per answer, preserving canonical matches locally."""
    canonical_reference = normalized_math_answer(reference_answer)
    flags: list[Optional[bool]] = [
        True if canonical_reference and normalized_math_answer(answer) == canonical_reference else None
        for answer in answers
    ]
    pending_indexes = [index for index, flag in enumerate(flags) if flag is None]
    if not pending_indexes:
        return [True] * len(answers), {"canonicalMatches": len(answers)}

    pending_answers = [answers[index] for index in pending_indexes]
    request_body: dict[str, Any] = {
        "model": model.id,
        "messages": [{"role": "user", "content": equivalence_prompt(question_text, reference_answer, pending_answers)}],
        "temperature": 0,
    }
    if request_options:
        request_body.update(request_options)
    content, raw = await call_chat(client, base_url, api_key, request_body, stream=stream, on_stream_chunk=on_stream_chunk)
    parsed_flags = parsed_equivalence_flags(content, len(pending_answers))
    for index, flag in zip(pending_indexes, parsed_flags):
        flags[index] = flag
    raw["equivalenceContent"] = content
    raw["canonicalMatches"] = len(answers) - len(pending_indexes)
    return [bool(flag) for flag in flags], raw


def apiroute_thinking_options(model_id: str, *, deep: bool) -> dict[str, Any]:
    """Return the gateway-specific thinking controls for the shared APIRoute models."""
    if model_id == "kimi-k3":
        return {"reasoning_effort": "max" if deep else "low"}
    if model_id == "glm-5.2":
        return {
            "thinking": {"type": "enabled" if deep else "disabled"},
            **({"reasoning_effort": "max"} if deep else {}),
        }
    if model_id == "qwen3.7-max":
        return {"enable_thinking": deep}
    return {}


async def execute_model(work: CheckWorkItem, question: Question, settings: Settings,
                        provider_api_key: Optional[str] = None,
                        on_stream_chunk: Optional[StreamObserver] = None) -> tuple[dict[str, Any], list[Any]]:
    model = work_audit_model(work)
    if work.provider == "doubao":
        read_timeout = settings.ai_doubao_read_timeout_seconds
    elif work.provider == "apiroute" and model.id == "glm-5.2":
        read_timeout = settings.ai_glm_read_timeout_seconds
    else:
        read_timeout = settings.ai_model_read_timeout_seconds
    timeout = httpx.Timeout(connect=30, read=read_timeout, write=30, pool=30)
    async with httpx.AsyncClient(timeout=timeout) as client:
        if work.provider == "doubao":
            api_key = provider_api_key or (settings.apiroute_keys[0] if settings.apiroute_keys else None)
            if work.stage == "equivalence":
                answers = work.payload.get("answers", [])
                flags, raw = await judge_equivalences(
                    client, base_url=settings.apiroute_base_url, api_key=api_key, model=model,
                    question_text=question.question, reference_answer=question.answer, answers=answers,
                    request_options={"thinking": {"type": "disabled"}},
                    stream=True,
                    on_stream_chunk=on_stream_chunk,
                )
                return {"equivalences": flags, "usage": raw.get("usage")}, [raw]
            if work.stage == "synthesis":
                prompt = f"""你是 STEM 题目来源识别审核员。判断下列题目是否疑似由生成式 AI 生成。

题目：{question.question}
参考答案：{question.answer}

只评估题干与答案中可见的 AI 生成痕迹，不评估题目是否正确、是否困难、是否专业或答案是否正确。题目复杂、专业、使用标准公式或有多个条件，均不能单独作为 AI 生成的证据。

可作为证据的信号包括：模板化或泛化的表述、上下文不自然或前后不一致、无关条件的机械堆砌、虚构出处/概念/公式、明显的语言模型套话，或不符合真实命题习惯的组合。证据不足时必须判定为 false，并说明无法仅凭文本可靠判断来源。

只输出 JSON：
{{"is_synthetic": true/false, "confidence": 0-100, "reasons": [{{"type": "...", "evidence": "..."}}]}}"""
                content, raw = await call_chat(
                    client,
                    settings.apiroute_base_url,
                    api_key,
                    {"model": model.id, "messages": [{"role": "user", "content": prompt}], "temperature": 0},
                    stream=True,
                    on_stream_chunk=on_stream_chunk,
                )
                return {"answer": content[:10000]}, [raw]
            # 难度校验只保留最终答案；答案比对阶段则在上方显式关闭思考。
            prompt = difficulty_answer_prompt(question.question) if work.check_type == "difficulty" else solve_prompt(question.question)
            content, raw = await call_chat(client, settings.apiroute_base_url, api_key, {
                "model": model.id,
                "messages": [{"role": "user", "content": prompt}],
                "thinking": {"type": "enabled"},
                "reasoning": {"effort": "high"},
                "temperature": 0,
            }, stream=True, on_stream_chunk=on_stream_chunk)
            return {"answer": content[:10000]}, [raw]
        if work.provider == "gemini":
            api_key = provider_api_key or (settings.apiroute_keys[0] if settings.apiroute_keys else None)
            if work.stage == "equivalence":
                answers = work.payload.get("answers", [])
                flags, raw = await judge_equivalences(
                    client, base_url=settings.apiroute_base_url, api_key=api_key, model=model,
                    question_text=question.question, reference_answer=question.answer, answers=answers,
                    request_options={"thinking": {"type": "enabled"}, "reasoning": {"effort": "high"}},
                )
                return {"equivalences": flags, "usage": raw.get("usage")}, [raw]
            if work.stage == "synthesis":
                prompt = f"""判断下列题目是否疑似由生成式 AI 生成。只分析 AI 生成痕迹，不能用题目难度、专业性、正确性或标准公式作为证据；证据不足时判定 false。只输出 JSON：{{"is_synthetic": true/false, "confidence": 0-100, "reasons": [{{"type": "...", "evidence": "..."}}]}}\n题目：{question.question}\n参考答案：{question.answer}"""
            else:
                prompt = difficulty_answer_prompt(question.question) if work.check_type == "difficulty" else solve_prompt(question.question)
            request_body: dict[str, Any] = {
                "model": model.id,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
            }
            if work.stage == "solve":
                # 仅返回最终答案，但允许 Gemini 在服务端完成推理；提示词会约束
                # 唯一答案题不输出备选映射。
                request_body["thinking"] = {"type": "enabled"}
                request_body["reasoning"] = {"effort": "high"}
            content, raw = await call_chat(client, settings.apiroute_base_url, api_key, request_body)
            return {"answer": content[:10000]}, [raw]
        if work.provider == "apiroute":
            api_key = provider_api_key or (settings.apiroute_keys[0] if settings.apiroute_keys else None)
            if work.stage == "equivalence":
                answers = work.payload.get("answers", [])
                flags, raw = await judge_equivalences(
                    client, base_url=settings.apiroute_base_url, api_key=api_key, model=model,
                    question_text=question.question, reference_answer=question.answer, answers=answers,
                    request_options=apiroute_thinking_options(model.id, deep=False),
                )
                return {"equivalences": flags, "usage": raw.get("usage")}, [raw]
            if work.stage == "synthesis":
                prompt = f"""判断下列题目是否疑似由生成式 AI 生成。只分析题干与答案中可见的 AI 生成痕迹，不能用题目难度、专业性、正确性或标准公式作为证据；证据不足时判定 false。只输出 JSON：{{"is_synthetic": true/false, "confidence": 0-100, "reasons": [{{"type": "...", "evidence": "..."}}]}}
题目：{question.question}
参考答案：{question.answer}"""
            else:
                prompt = difficulty_answer_prompt(question.question) if work.check_type == "difficulty" else solve_prompt(question.question)
            request_body: dict[str, Any] = {
                "model": model.id,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
            }
            if work.stage == "solve":
                request_body.update(apiroute_thinking_options(model.id, deep=True))
            else:
                request_body.update(apiroute_thinking_options(model.id, deep=False))
            content, raw = await call_chat(
                client, settings.apiroute_base_url, api_key, request_body,
                stream=work.stage == "solve",
                on_stream_chunk=on_stream_chunk if work.stage == "solve" else None,
            )
            return {"answer": content[:10000]}, [raw]
        raise ValueError(f"unsupported provider: {work.provider}")


async def finalize_check(session: AsyncSession, redis: Redis, work: CheckWorkItem, question: Question) -> None:
    model = work_audit_model(work)
    items = (await session.scalars(select(CheckWorkItem).where(CheckWorkItem.run_id == work.run_id,
        CheckWorkItem.check_type == work.check_type))).all()
    if work.check_type == "latex":
        detail = work.result or {"errors": []}
        result = "pass" if not detail.get("errors") else "fail"
        raws: list[Any] = []
    elif work.stage == "equivalence":
        solve_items = [i for i in items if i.stage == "solve"]
        completed_solve_items = [item for item in solve_items if item.status == "completed" and (item.result or {}).get("answer")]
        answers = [str((item.result or {}).get("answer", "")) for item in completed_solve_items]
        flags = (work.result or {}).get("equivalences", [])
        correct = sum(1 for value in flags if value)
        partial = len(completed_solve_items) != len(solve_items)
        result = "manual_review" if partial else ("pass" if (correct <= model.difficulty_threshold if work.check_type == "difficulty" else correct >= 1) else "fail")
        detail = {"model": model.snapshot(), "correctCount": correct, "totalCount": len(solve_items),
                  "completedCount": len(completed_solve_items), "partial": partial,
                  "threshold": model.difficulty_threshold if work.check_type == "difficulty" else None,
                  "responses": [value[:200] for value in answers],
                  "responseAttempts": [item.attempt for item in completed_solve_items],
                  "equivalences": flags,
                  "failedAttempts": [
                      {"attempt": item.attempt, "errorCode": item.error_code, "message": item.error}
                      for item in solve_items if item.status != "completed"
                  ]}
        raws = [item.result for item in completed_solve_items] + [work.result]
    elif work.check_type == "synthesis":
        answer = str((work.result or {}).get("answer", ""))
        parsed: dict[str, Any] = {}
        try:
            parsed = json.loads(answer[answer.find("{"):answer.rfind("}") + 1])
        except (ValueError, json.JSONDecodeError):
            pass
        detail = {"model": model.snapshot(), "isSynthetic": bool(parsed.get("is_synthetic", False)), "confidence": parsed.get("confidence", 0), "reasons": parsed.get("reasons", []), "ruleViolations": []}
        result = "fail" if detail["isSynthetic"] and detail["confidence"] > 70 else "warning" if detail["isSynthetic"] else "pass"
        raws = [work.result]
    else:
        answer = str((work.result or {}).get("answer", ""))
        detail = {"model": model.snapshot(), "modelAnswer": answer[:300], "isCorrect": False, "confidence": 20}
        result = "pass"
        raws = [work.result]
    started_times = [item.started_at for item in items if item.started_at]
    finished_at = work.completed_at or utcnow()
    if started_times:
        # 以该检查项第一项开始至最终判断完成的墙钟时间展示给用户；并保留
        # 工作项 CPU/网络累计时间，供运维分析并发与供应商耗时。
        detail["elapsedMs"] = int(max(0, (finished_at - min(started_times)).total_seconds() * 1_000))
    detail["executionMs"] = int(sum(item.execution_ms or 0 for item in items))
    existing = await session.scalar(select(CheckResult).where(CheckResult.question_id == question.id, CheckResult.check_type == work.check_type))
    if existing:
        existing.result, existing.detail, existing.raw_responses = result, detail, raws
    else:
        session.add(CheckResult(question_id=question.id, check_type=work.check_type, result=result, detail=detail, raw_responses=raws))
    await emit(session, redis, work.run_id, "progress", {"questionId": question.id, "checkType": work.check_type, "status": "done", "result": result, "detail": detail, "model": model.snapshot()})


async def complete_run_if_ready(session: AsyncSession, redis: Redis, run: CheckRun) -> None:
    waiting = await session.scalar(select(CheckWorkItem.id).where(CheckWorkItem.run_id == run.id,
        CheckWorkItem.status.in_(["queued", "blocked", "running"])).limit(1))
    if waiting:
        return
    manual = await session.scalar(select(CheckWorkItem.id).where(
        CheckWorkItem.run_id == run.id,
        CheckWorkItem.status.in_(["manual_review", "dead"]),
    ).limit(1))
    run.status = "manual_review" if manual else "completed"
    run.completed_at = utcnow()
    question = await session.get(Question, run.question_id)
    if question:
        question.status = "manual_review" if manual else "done"
    if run.batch_id:
        batch = await session.get(CheckBatch, run.batch_id, with_for_update=True)
        if batch:
            batch.completed_count += 1
            if manual:
                batch.failed_count += 1
                batch.manual_review_count += 1
            if batch.completed_count >= batch.total_count:
                batch.status = "manual_review" if batch.manual_review_count else "completed"
    await emit(session, redis, run.id, "complete", {"questionId": run.question_id, "checkRunId": str(run.id), "status": run.status})


async def reconcile_orphaned_runs(session: AsyncSession, redis: Redis) -> int:
    """Finish stale active runs whose work items are already all terminal.

    A Worker restart or an earlier process interruption can leave a CheckRun in
    ``queued``/``running`` after its final work item has already been written.
    Redis recovery cannot see these rows because none are runnable, so reconcile
    them from PostgreSQL during the periodic recovery pass.
    """
    has_work = select(CheckWorkItem.id).where(CheckWorkItem.run_id == CheckRun.id).exists()
    has_active_work = select(CheckWorkItem.id).where(
        CheckWorkItem.run_id == CheckRun.id,
        CheckWorkItem.status.in_(["queued", "blocked", "running"]),
    ).exists()
    runs = (await session.scalars(
        select(CheckRun)
        .where(CheckRun.status.in_(["queued", "running"]), has_work, ~has_active_work)
        .with_for_update(skip_locked=True)
    )).all()
    for run in runs:
        await complete_run_if_ready(session, redis, run)
    return len(runs)


def provider_error(exc: Exception) -> tuple[str, Optional[int], str, bool]:
    status_code = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
    if isinstance(exc, httpx.TimeoutException):
        return "timeout", status_code, str(exc) or exc.__class__.__name__, True
    if isinstance(exc, httpx.NetworkError):
        return "network_error", status_code, str(exc) or exc.__class__.__name__, True
    if isinstance(exc, httpx.HTTPStatusError):
        retryable = status_code in {429, 502, 503, 504}
        return f"http_{status_code}", status_code, str(exc) or exc.__class__.__name__, retryable
    if isinstance(exc, ValueError) and "API key" in str(exc):
        return "provider_not_configured", None, str(exc), False
    return "execution_error", status_code, str(exc) or exc.__class__.__name__, False


async def provider_circuit_open(redis: Redis, provider: str) -> bool:
    return bool(await redis.exists(f"stem:circuit:{provider}:open"))


async def record_provider_failure(redis: Redis, settings: Settings, provider: str, retryable: bool) -> None:
    if not retryable or provider == "rule":
        return
    key = f"stem:circuit:{provider}:failures"
    failures = await redis.incr(key)
    if failures == 1:
        await redis.expire(key, settings.provider_circuit_window_seconds)
    if failures >= settings.provider_circuit_failure_threshold:
        await redis.set(f"stem:circuit:{provider}:open", "1", ex=settings.provider_circuit_open_seconds)


async def clear_provider_failures(redis: Redis, provider: str) -> None:
    if provider != "rule":
        await redis.delete(f"stem:circuit:{provider}:failures")


async def mark_manual_review(session: AsyncSession, redis: Redis, work: CheckWorkItem,
                             *, error_code: str, status_code: Optional[int], message: str,
                             cascade_check_type: bool = True) -> None:
    now = utcnow()
    affected: list[CheckWorkItem] = []
    if cascade_check_type:
        affected = (await session.scalars(select(CheckWorkItem).where(
            CheckWorkItem.run_id == work.run_id,
            CheckWorkItem.check_type == work.check_type,
            CheckWorkItem.status.in_(["queued", "blocked"]),
        ).with_for_update())).all()
    for item in affected:
        item.status = "manual_review"
        item.manual_review_at = now
        item.completed_at = now
        item.error_code = error_code
        item.error_status_code = status_code
        item.error = message
    work.status = "manual_review"
    work.manual_review_at = now
    work.completed_at = now
    work.error_code = error_code
    work.error_status_code = status_code
    work.error = message
    work.lease_owner = None
    work.lease_expires_at = None

    detail: dict[str, Any] = {
        "manualReview": True,
        "provider": work.provider,
        "stage": work.stage,
        "errorCode": error_code,
        "statusCode": status_code,
        "message": message,
        "attempts": work.attempt_no,
    }
    if work.check_type in {"difficulty", "answer", "synthesis"}:
        detail["model"] = work_audit_model(work).snapshot()
    if work.started_at:
        detail["elapsedMs"] = int(max(0, (now - work.started_at).total_seconds() * 1_000))
    detail["executionMs"] = int(work.execution_ms or 0)
    if work.stage == "equivalence" and work.check_type in {"difficulty", "answer"}:
        solve_items = (await session.scalars(select(CheckWorkItem).where(
            CheckWorkItem.run_id == work.run_id,
            CheckWorkItem.check_type == work.check_type,
            CheckWorkItem.stage == "solve",
        ))).all()
        completed_solve_items = [item for item in solve_items if item.status == "completed" and (item.result or {}).get("answer")]
        if completed_solve_items:
            detail.update({
                "model": work_audit_model(work).snapshot(),
                "partial": True,
                "completedCount": len(completed_solve_items),
                "totalCount": len(solve_items),
                "threshold": work_audit_model(work).difficulty_threshold if work.check_type == "difficulty" else None,
                "responses": [str((item.result or {}).get("answer", ""))[:200] for item in completed_solve_items],
                "responseAttempts": [item.attempt for item in completed_solve_items],
                "equivalences": [],
                "failedAttempts": [
                    {"attempt": item.attempt, "errorCode": item.error_code, "message": item.error}
                    for item in solve_items if item.status != "completed"
                ],
            })
    # 独立作答失败时不能把同一检查项中已成功的答案一并覆盖。等价判断会在
    # 其余作答全部终态后继续运行，并将成功答案和失败次数一起落入最终详情。
    if cascade_check_type:
        result_row = await session.scalar(select(CheckResult).where(
            CheckResult.question_id == work.question_id,
            CheckResult.check_type == work.check_type,
        ))
        if result_row:
            result_row.result, result_row.detail, result_row.raw_responses = "manual_review", detail, []
        else:
            session.add(CheckResult(question_id=work.question_id, check_type=work.check_type,
                                    result="manual_review", detail=detail, raw_responses=[]))
    await emit(session, redis, work.run_id, "progress", {
        "questionId": work.question_id,
        "checkType": work.check_type,
        "status": "manual_review",
        "provider": work.provider,
        "stage": work.stage,
        "errorCode": error_code,
        "statusCode": status_code,
        "message": message,
    })


async def pop_ready(redis: Redis) -> Optional[str]:
    now = time.time()
    for priority in ("batch", "background", "interactive"):
        work_id = await pop_ready_queue(redis, priority, now)
        if work_id:
            return work_id
    return None


def _stream_observation(payload: dict[str, Any], now: datetime,
                        snapshot: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Merge a monotonic stream snapshot into the persisted work payload."""
    previous = payload.get("stream") if isinstance(payload.get("stream"), dict) else {}
    observation: dict[str, Any] = {
        "receivedChunks": int(previous.get("receivedChunks") or 0),
        "contentChars": int(previous.get("contentChars") or 0),
        "reasoningChars": int(previous.get("reasoningChars") or 0),
        "startedAt": previous.get("startedAt") or now.isoformat(),
        "lastChunkAt": previous.get("lastChunkAt"),
    }
    if snapshot:
        for name in ("receivedChunks", "contentChars", "reasoningChars"):
            observation[name] = max(observation[name], int(snapshot.get(name) or 0))
        if snapshot.get("lastChunkAt"):
            observation["lastChunkAt"] = snapshot["lastChunkAt"]
    observation["lastHeartbeatAt"] = now.isoformat()
    return observation


async def refresh_running_work_lease(work_id: uuid.UUID, redis: Redis, settings: Settings,
                                     stream_snapshot: Optional[dict[str, Any]] = None) -> bool:
    """Persist a model-call heartbeat without sharing the Worker transaction.

    External model calls can take a long time, especially when deep reasoning is
    enabled. A short independent transaction both extends the lease and exposes
    stream activity to the detail page and queue monitor.
    """
    async with SessionLocal() as heartbeat_session:
        async with heartbeat_session.begin():
            work = await heartbeat_session.get(CheckWorkItem, work_id, with_for_update=True)
            if not work or work.status != "running":
                return False
            now = utcnow()
            payload = dict(work.payload or {})
            stream = _stream_observation(payload, now, stream_snapshot)
            payload["stream"] = stream
            work.payload = payload
            work.lease_expires_at = now + timedelta(seconds=settings.lease_seconds)
            elapsed_ms = int(max(0, (now - work.started_at).total_seconds() * 1_000)) if work.started_at else 0
            await emit(heartbeat_session, redis, work.run_id, "progress", {
                "questionId": work.question_id,
                "checkType": work.check_type,
                "status": "running",
                "provider": work.provider,
                "stage": work.stage,
                "elapsedMs": elapsed_ms,
                "stream": stream,
            })
    return True


async def _stop_lease_heartbeat(task: Optional[asyncio.Task[None]], stop: Optional[asyncio.Event]) -> None:
    if stop:
        stop.set()
    if task:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def worker_once(session: AsyncSession, redis: Redis, settings: Settings, interactive_budget: int = 3) -> int:
    del interactive_budget
    work_id = await pop_ready(redis)
    if not work_id:
        return 0
    acquired = False
    provider = "rule"
    stage = "check"
    rate_limit_scope = provider
    provider_api_key: Optional[str] = None
    lease_heartbeat_task: Optional[asyncio.Task[None]] = None
    lease_heartbeat_stop: Optional[asyncio.Event] = None
    try:
        work = await session.get(CheckWorkItem, uuid.UUID(work_id), with_for_update=True)
        if not work or work.status != "queued":
            await session.rollback()
            return 0
        # 配置收紧后，旧策略遗留的重试工作项不应再发起额外模型调用。
        # 直接转人工复核，保留已完成的独立作答和最后一次上游错误供排查。
        if work.attempt_no > settings.ai_retry_max_attempts:
            message = (
                f"模型调用已连续失败 {work.attempt_no} 次，超过当前允许的 "
                f"{settings.ai_retry_max_attempts} 次重试上限"
            )
            await mark_manual_review(
                session,
                redis,
                work,
                error_code=work.error_code or "retry_exhausted",
                status_code=work.error_status_code,
                message=message,
                cascade_check_type=work.stage != "solve",
            )
            if work.stage == "solve":
                await activate_equivalence_if_ready(session, redis, work.run_id, work.check_type)
            run = await session.get(CheckRun, work.run_id)
            if run:
                await complete_run_if_ready(session, redis, run)
            await session.commit()
            return 1
        provider, stage = work.provider, work.stage
        rate_limit_scope = provider
        estimated_tokens = 300 if provider == "rule" else max(1_000, len(str(work.payload)) // 3 + 1_000)
        if provider in {"doubao", "apiroute"}:
            candidates = await provider_key_candidates(redis, settings, provider)
            if not candidates:
                raise ValueError("provider API key not configured")
            any_key_available = False
            for candidate in candidates:
                candidate_scope = provider_scope(provider, candidate)
                if await provider_circuit_open(redis, candidate_scope):
                    continue
                any_key_available = True
                if await acquire(redis, settings, provider, stage, int(time.time() * 1000), estimated_tokens, candidate_scope):
                    acquired = True
                    provider_api_key = candidate
                    rate_limit_scope = candidate_scope
                    break
            if not acquired and not any_key_available:
                work.available_at = utcnow() + timedelta(seconds=settings.provider_circuit_open_seconds)
        else:
            if await provider_circuit_open(redis, provider):
                work.available_at = utcnow() + timedelta(seconds=settings.provider_circuit_open_seconds)
                await session.commit()
                await enqueue(redis, work)
                return 0
            acquired = await acquire(redis, settings, provider, stage, int(time.time() * 1000), estimated_tokens)
        if not acquired:
            # Commit the read-only transaction before using the ORM object to
            # re-enqueue it; rollback would expire attributes in async mode.
            await session.commit()
            await enqueue(redis, work)
            return 0
        work.status = "running"
        work.started_at = work.started_at or utcnow()
        work.lease_owner = settings.worker_id
        work.lease_expires_at = utcnow() + timedelta(seconds=settings.lease_seconds)
        work.payload = {
            **(work.payload or {}),
            "stream": _stream_observation(dict(work.payload or {}), utcnow()),
        }
        run = await session.get(CheckRun, work.run_id)
        question = await session.get(Question, work.question_id)
        if run and run.status == "queued":
            run.status, run.started_at = "running", utcnow()
        if run and run.batch_id:
            batch = await session.get(CheckBatch, run.batch_id)
            if batch and batch.status == "queued":
                batch.status = "running"
        if work.stage == "equivalence":
            solves = (await session.scalars(select(CheckWorkItem).where(
                CheckWorkItem.run_id == work.run_id,
                CheckWorkItem.check_type == work.check_type,
                CheckWorkItem.stage == "solve",
            ))).all()
            work.payload = {**(work.payload or {}), "answers": [str((item.result or {}).get("answer", "")) for item in solves]}
        await emit(session, redis, work.run_id, "progress", {
            "questionId": work.question_id, "checkType": work.check_type, "status": "running",
            "provider": provider, "stage": stage,
        })
        await session.commit()

        # External model work is deliberately outside a PostgreSQL transaction.
        execution_started = time.perf_counter()
        stream_counters = {"receivedChunks": 0, "contentChars": 0, "reasoningChars": 0}
        last_stream_write = 0.0

        async def observe_stream(chunk: dict[str, int]) -> None:
            nonlocal last_stream_write
            for name in stream_counters:
                stream_counters[name] += int(chunk.get(name) or 0)
            now = time.monotonic()
            if now - last_stream_write < settings.ai_stream_observation_interval_seconds:
                return
            last_stream_write = now
            await refresh_running_work_lease(
                uuid.UUID(work_id), redis, settings,
                {**stream_counters, "lastChunkAt": utcnow().isoformat()},
            )

        async def renew_lease_until_finished() -> None:
            while True:
                try:
                    assert lease_heartbeat_stop is not None
                    await asyncio.wait_for(
                        lease_heartbeat_stop.wait(),
                        timeout=max(1, settings.ai_work_lease_heartbeat_seconds),
                    )
                    return
                except asyncio.TimeoutError:
                    await refresh_running_work_lease(uuid.UUID(work_id), redis, settings)

        lease_heartbeat_stop = asyncio.Event()
        lease_heartbeat_task = asyncio.create_task(renew_lease_until_finished())
        try:
            if stage == "check":
                result: dict[str, Any] = latex_check(f"{question.question}\\n{question.answer}")
            else:
                result, raw_responses = await execute_model(
                    work, question, settings, provider_api_key, on_stream_chunk=observe_stream,
                )
                if raw_responses and isinstance(raw_responses[-1], dict):
                    usage = raw_responses[-1].get("usage")
                    if isinstance(usage, dict):
                        # 每个工作项都保留上游返回的 token 用量，便于成本归因和压测统计。
                        result["usage"] = usage
        finally:
            await _stop_lease_heartbeat(lease_heartbeat_task, lease_heartbeat_stop)
            lease_heartbeat_task = None
            lease_heartbeat_stop = None
        execution_ms = (time.perf_counter() - execution_started) * 1_000

        async with session.begin():
            db_work = await session.get(CheckWorkItem, uuid.UUID(work_id), with_for_update=True)
            db_question = await session.get(Question, db_work.question_id)
            db_run = await session.get(CheckRun, db_work.run_id)
            db_work.result = result
            db_work.status, db_work.lease_owner, db_work.lease_expires_at = "completed", None, None
            db_work.completed_at = utcnow()
            db_work.execution_ms = (db_work.execution_ms or 0) + execution_ms
            if db_work.stage in ("check", "equivalence", "synthesis"):
                await finalize_check(session, redis, db_work, db_question)
            if db_work.stage == "check":
                await activate_after_latex(session, redis, db_work.run_id)
            if db_work.stage == "solve":
                await activate_equivalence_if_ready(session, redis, db_work.run_id, db_work.check_type)
            await complete_run_if_ready(session, redis, db_run)
        await clear_provider_failures(redis, rate_limit_scope)
    except asyncio.CancelledError:
        # 模型调用在 Worker 关闭时被取消。归还租约和队列，让新的 Worker
        # 能继续处理，而不是把任务永久留在 running 状态。
        async with session.begin():
            work = await session.get(CheckWorkItem, uuid.UUID(work_id), with_for_update=True)
            if work and work.status == "running":
                work.status = "queued"
                work.lease_owner = None
                work.lease_expires_at = None
                work.available_at = utcnow()
                await enqueue(redis, work)
        raise
    except Exception as exc:
        error_code, status_code, message, retryable = provider_error(exc)
        await record_provider_failure(redis, settings, rate_limit_scope, retryable)
        async with session.begin():
            work = await session.get(CheckWorkItem, uuid.UUID(work_id), with_for_update=True)
            if "execution_started" in locals():
                work.execution_ms = (work.execution_ms or 0) + (time.perf_counter() - execution_started) * 1_000
            work.attempt_no += 1
            work.error, work.error_code, work.error_status_code = message, error_code, status_code
            work.lease_owner = None
            work.lease_expires_at = None
            if retryable and work.attempt_no <= settings.ai_retry_max_attempts:
                # 受限的全抖动退避，防止多个 pass@K 工作项在网关恢复时同时重试。
                delay = random.uniform(0, min(300, 2 ** work.attempt_no))
                work.status = "queued"
                work.available_at = utcnow() + timedelta(seconds=delay)
                await enqueue(redis, work)
            else:
                await mark_manual_review(session, redis, work, error_code=error_code,
                                         status_code=status_code, message=message,
                                         cascade_check_type=work.stage != "solve")
                if work.stage == "solve":
                    await activate_equivalence_if_ready(session, redis, work.run_id, work.check_type)
                run = await session.get(CheckRun, work.run_id)
                if run:
                    await complete_run_if_ready(session, redis, run)
    finally:
        await _stop_lease_heartbeat(lease_heartbeat_task, lease_heartbeat_stop)
        if acquired:
            await release(redis, settings, provider, stage, rate_limit_scope)
    return 1
