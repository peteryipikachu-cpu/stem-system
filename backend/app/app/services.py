from __future__ import annotations

import json
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any
from typing import Optional

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .audit_models import SYNTHESIS_AUDIT_MODEL_ID, AuditModel, get_audit_model, model_from_snapshot
from .models import CheckEvent, CheckResult, CheckRun, CheckWorkItem, Question, QuestionNgram, QuestionSimilarityMatch, QuestionVersion
from .schemas import DEFAULT_CHECK_TYPES, VALID_CHECK_TYPES


DIFFICULTY_ASSESSMENT_TYPE = "difficulty_assessment"
DIFFICULTY_LEVELS = ("L1", "L2", "L3")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def question_is_qualified(question: Question) -> bool:
    """合格判定：当前版本的分级评测完成（pass）且难度达到 L2/L3。

    LaTeX 与 AI 合成题检测已并入分级评测 L0 层：分级评测完成（pass）
    即视为两项基础检测通过；历史独立 latex/synthesis 结果仍兼容按同 run 判定。
    """
    runs: dict[uuid.UUID | None, dict[str, str]] = {}
    for result in question.check_results:
        if result.question_version != question.current_version:
            continue
        runs.setdefault(result.check_run_id, {})[result.check_type] = result.result
    base_checks_ok = any(
        values.get("latex") == "pass" and values.get("synthesis") == "pass"
        or values.get(DIFFICULTY_ASSESSMENT_TYPE) == "pass"
        for values in runs.values()
    )
    return base_checks_ok and question.difficulty_status == "completed" and question.difficulty_level in {"L2", "L3"}


def question_json(question: Question, include_results: bool = True) -> dict[str, Any]:
    assessment_results = [item for item in question.check_results if item.check_type == DIFFICULTY_ASSESSMENT_TYPE]
    assessment_results.sort(key=lambda item: item.created_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    current_assessment = next((item for item in assessment_results if item.check_run_id == question.difficulty_run_id), assessment_results[0] if assessment_results else None)
    data = {
        "id": question.id, "title": question.title, "type": question.type, "domain": question.domain,
        "knowledgeLevel": question.knowledge_level or question.difficulty, "knowledgePoints": question.knowledge_points,
        "difficultyLevel": question.difficulty_level, "difficultyStatus": question.difficulty_status,
        "question": question.question, "answer": question.answer, "solution": question.solution,
        "expertId": question.expert_id, "subject": question.subject, "status": question.status,
        "batchId": question.batch_id, "createdAt": question.created_at.isoformat(),
        "updatedAt": question.updated_at.isoformat(),
        "projectId": question.project_id,
        "currentVersion": question.current_version,
        "versionCount": question.current_version,
        "versionCreatedAt": question.current_version_created_at.isoformat() if question.current_version_created_at else question.created_at.isoformat(),
        "versionAuthor": {"id": question.current_version_author.id, "username": question.current_version_author.username} if question.current_version_author else None,
        "changeNote": question.current_version_note,
        "similarityStatus": question.similarity_status,
        "similarityCheckedAt": question.similarity_checked_at.isoformat() if question.similarity_checked_at else None,
        "owner": {"id": question.owner.id, "username": question.owner.username} if question.owner else None,
        "difficultyAssessment": assessment_result_json(current_assessment) if current_assessment else (
            {
                "checkRunId": str(question.difficulty_run_id) if question.difficulty_run_id else None,
                "status": question.difficulty_status,
                "difficultyLevel": question.difficulty_level,
                "currentLayer": "L0" if question.difficulty_status == "validating_format" else None,
                "layers": [],
                "format": None,
                "failure": None,
                "policy": None,
                "createdAt": None,
                "updatedAt": None,
            }
            if question.difficulty_run_id else None
        ),
        "difficultyAssessmentHistory": [assessment_result_json(item) for item in assessment_results],
    }
    if include_results:
        data["checkResults"] = [check_result_json(item) for item in question.check_results
                                if item.question_version in {None, question.current_version}]
    return data


def assessment_result_json(item: CheckResult) -> dict[str, Any]:
    detail = item.detail or {}
    return {
        "checkRunId": str(item.check_run_id) if item.check_run_id else None,
        "questionVersion": item.question_version,
        "status": detail.get("status", item.result),
        "difficultyLevel": detail.get("difficultyLevel"),
        "currentLayer": detail.get("currentLayer"),
        "layers": detail.get("layers", []),
        "format": detail.get("format"),
        "failure": detail.get("failure"),
        "policy": detail.get("policy"),
        "createdAt": item.created_at.isoformat() if item.created_at else None,
        "updatedAt": item.updated_at.isoformat() if item.updated_at else None,
    }


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
    required_types = {DIFFICULTY_ASSESSMENT_TYPE}
    if not result_values:
        check_summary = "未质检"
    elif not required_types.issubset(result_values):
        check_summary = "部分质检"
    else:
        # LaTeX 格式与 AI 合成题检测已并入分级评测 L0 层；历史版本快照中
        # 若仍存在独立结果，一并纳入汇总判定。
        relevant = {check_type: value for check_type, value in result_values.items()
                    if check_type in {"latex", "synthesis", DIFFICULTY_ASSESSMENT_TYPE}}
        if all(value == "pass" for value in relevant.values()):
            check_summary = "质检通过"
        elif "manual_review" in relevant.values():
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
            "checkRunId": str(item.check_run_id) if item.check_run_id else None,
            "questionVersion": item.question_version, "modelId": item.model_id,
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


class ActiveModelConflictError(ValueError):
    pass


def run_audit_model(run: CheckRun) -> AuditModel:
    return model_from_snapshot(run.model_versions)


def run_stage_model(run: CheckRun, check_type: str, stage: str) -> AuditModel:
    models = (run.model_versions or {}).get("models", {})
    key = "answerComparison" if check_type == "answer" and stage == "equivalence" else check_type
    return model_from_snapshot(models.get(key)) if isinstance(models, dict) and key in models else run_audit_model(run)


def overlapping_check_types(run: CheckRun, check_types: list[str]) -> list[str]:
    """Return requested check types that an active run is already responsible for."""
    active_types = set(run.check_types or [])
    return [check_type for check_type in check_types if check_type in active_types]


def question_ngrams(value: str, size: int = 3) -> set[str]:
    normalized = re.sub(r"\s+", "", value.lower())
    normalized = re.sub(r"[\W_]+", "", normalized, flags=re.UNICODE)
    if not normalized:
        return set()
    if len(normalized) <= size:
        return {normalized}
    return {normalized[index:index + size] for index in range(len(normalized) - size + 1)}


def quota_violation_message(
    daily_limit: int, monthly_limit: int, monthly_budget: float,
    day_count: int, month_count: int, month_cost: float,
) -> Optional[str]:
    """账号配额判定：与 Worker 派发门控同口径，日/月上限统计的是用户发起的质检任务数（CheckRun），未超限返回 None。"""
    if daily_limit and day_count >= daily_limit:
        return f"账号已达日上限 {daily_limit} 次质检，无法发起新任务，次日自动恢复。"
    if monthly_limit and month_count >= monthly_limit:
        return f"账号已达月上限 {monthly_limit} 次质检，无法发起新任务，下月自动恢复。"
    if monthly_budget and month_cost >= monthly_budget:
        return f"账号已达月预算 ￥{monthly_budget:g}，无法发起新任务，调高预算或下月自动恢复。"
    return None


def ngram_lexical_score(grams: set[str], other: set[str]) -> float:
    """两组 n-gram 的词汇相似度（共享数 / 较大集合大小），与相似题召回同口径。"""
    if not grams or not other:
        return 0.0
    return len(grams & other) / max(len(grams), len(other))


async def create_similarity_run(session: AsyncSession, redis: Redis, question: Question,
                                model: AuditModel, requested_by_user_id: Optional[int]) -> CheckRun:
    grams = question_ngrams(question.question)
    if grams:
        existing_grams = set((await session.scalars(select(QuestionNgram.gram).where(
            QuestionNgram.question_id == question.id,
        ))).all())
        session.add_all(QuestionNgram(question_id=question.id, gram=gram) for gram in grams - existing_grams)
        await session.flush()
        candidate_rows = (await session.execute(
            select(QuestionNgram.question_id, func.count(QuestionNgram.gram).label("shared"))
            .where(QuestionNgram.gram.in_(grams), QuestionNgram.question_id != question.id)
            .group_by(QuestionNgram.question_id)
            .order_by(func.count(QuestionNgram.gram).desc())
            .limit(12)
        )).all()
        matches: list[QuestionSimilarityMatch] = []
        for candidate_id, shared in candidate_rows:
            candidate_grams = await session.scalar(
                select(func.count()).select_from(QuestionNgram).where(QuestionNgram.question_id == candidate_id)
            ) or 1
            score = shared / max(len(grams), candidate_grams)
            if score >= 0.18:
                match = QuestionSimilarityMatch(question_id=question.id, candidate_question_id=candidate_id, lexical_score=score)
                session.add(match)
                matches.append(match)
        await session.flush()
    else:
        matches = []
    if not matches:
        question.similarity_status = "clear"
        question.similarity_checked_at = utcnow()
        run = CheckRun(
            question_id=question.id, requested_by_user_id=requested_by_user_id,
            check_types=["similarity"], priority="background", status="completed",
            idempotency_key=f"similarity:{question.id}:v{question.current_version}",
            completed_at=utcnow(), model_versions={**model.snapshot(), "models": {"similarity": model.snapshot()}},
        )
        session.add(run)
        await session.flush()
        await emit(session, redis, run.id, "complete", {"questionId": question.id, "checkRunId": str(run.id), "status": "completed", "similarityStatus": "clear"})
        return run
    run = CheckRun(
        question_id=question.id,
        requested_by_user_id=requested_by_user_id,
        check_types=["similarity"],
        priority="background",
        status="queued",
        idempotency_key=f"similarity:{question.id}:v{question.current_version}",
        model_versions={**model.snapshot(), "models": {"similarity": model.snapshot()}},
    )
    session.add(run)
    await session.flush()
    candidates = {
        candidate.id: candidate
        for candidate in (await session.scalars(select(Question).where(Question.id.in_([item.candidate_question_id for item in matches])))).all()
    }
    works = []
    for index, match in enumerate(matches, start=1):
        candidate = candidates.get(match.candidate_question_id)
        if not candidate:
            continue
        works.append(make_work(
            run, "similarity", "similarity", index, model.provider,
            queue_owner_id=requested_by_user_id or question.owner_id or 0,
            audit_model=model, extra_payload={
                "matchId": match.id,
                "candidate": {"id": candidate.id, "question": candidate.question, "answer": candidate.answer, "subject": candidate.subject},
            },
        ))
    for work in works:
        work.project_id = question.project_id
    session.add_all(works)
    await session.flush()
    for work in works:
        await enqueue(redis, work)
    await emit(session, redis, run.id, "start", {"questionId": question.id, "checkTypes": ["similarity"], "checkRunId": str(run.id), "model": model.snapshot()})
    return run


def make_work(run: CheckRun, check_type: str, stage: str, attempt: int, provider: str,
              status: str = "queued", queue_owner_id: int = 0,
              audit_model: Optional[AuditModel] = None,
              extra_payload: Optional[dict[str, Any]] = None) -> CheckWorkItem:
    # 同一 CheckRun 内保持幂等；人工重检必须能创建新的工作项，不能与历史失败记录冲突。
    key = f"r:{run.id}|q:{run.question_id}|c:{check_type}|s:{stage}|a:{attempt}|v:{run.prompt_version}"
    selected_model = audit_model or run_audit_model(run)
    return CheckWorkItem(run_id=run.id, question_id=run.question_id, project_id=0, check_type=check_type, stage=stage,
                         attempt=attempt, provider=provider, priority=run.priority, queue_owner_id=queue_owner_id, status=status,
                         idempotency_key=key,
                         payload=({} if provider == "rule" else {"model": selected_model.snapshot()}) | (extra_payload or {}))


def make_assessment_work(run: CheckRun, *, level: str, stage: str, attempt: int,
                         provider: str, audit_model: Optional[AuditModel] = None,
                         status: str = "queued", queue_owner_id: int = 0,
                         extra_payload: Optional[dict[str, Any]] = None) -> CheckWorkItem:
    """Create an immutable, policy-snapshotted L0-L3 work item."""
    model_payload = audit_model.snapshot() if audit_model else None
    model_marker = audit_model.id if audit_model else "rule"
    key = f"r:{run.id}|q:{run.question_id}|assessment:{level}|s:{stage}|m:{model_marker}|a:{attempt}|v:{run.prompt_version}"
    payload: dict[str, Any] = {
        "difficultyAssessment": True,
        "level": level,
        "questionVersion": run.question_version,
    }
    if model_payload:
        payload["model"] = model_payload
    payload.update(extra_payload or {})
    return CheckWorkItem(
        run_id=run.id, question_id=run.question_id, project_id=0, check_type=DIFFICULTY_ASSESSMENT_TYPE,
        stage=stage, attempt=attempt, provider=provider, priority=run.priority,
        queue_owner_id=queue_owner_id, status=status, idempotency_key=key, payload=payload,
    )


async def create_difficulty_assessment(session: AsyncSession, redis: Redis, question: Question,
                                       policy: dict[str, Any], requested_by_user_id: Optional[int],
                                       *, force: bool = False, idempotency_key: Optional[str] = None) -> CheckRun:
    """Create the L0 work items; Worker fans out L1→L3 only after each decision.

    L0 包含 LaTeX 格式校验（本地规则）与 AI 合成题检测（固定模型）两项，
    两者均到达终态后 Worker 才会晋级首个作答层级。
    """
    if idempotency_key:
        existing = await session.scalar(select(CheckRun).where(CheckRun.idempotency_key == idempotency_key).limit(1))
        if existing:
            return existing
    active = await session.scalar(select(CheckRun).where(
        CheckRun.question_id == question.id,
        CheckRun.check_types == [DIFFICULTY_ASSESSMENT_TYPE],
        CheckRun.status.in_(["queued", "running"]),
    ).order_by(CheckRun.created_at.desc()).limit(1))
    if active and not force:
        return active
    run = CheckRun(
        question_id=question.id, question_version=question.current_version,
        requested_by_user_id=requested_by_user_id,
        check_types=[DIFFICULTY_ASSESSMENT_TYPE], priority="background", status="queued",
        idempotency_key=idempotency_key or f"difficulty-assessment:{question.id}:v{question.current_version}:{uuid.uuid4()}",
        model_versions={"difficultyPolicy": policy},
    )
    question.difficulty_status = "validating_format"
    question.difficulty_level = None
    question.difficulty_evaluated_at = None
    session.add(run)
    await session.flush()
    question.difficulty_run_id = run.id
    l0 = make_assessment_work(run, level="L0", stage="assessment_format", attempt=0,
                              provider="rule", queue_owner_id=requested_by_user_id or question.owner_id or 0)
    synthesis_model = get_audit_model(SYNTHESIS_AUDIT_MODEL_ID)
    l0_synthesis = make_assessment_work(run, level="L0", stage="assessment_synthesis", attempt=0,
                                        provider=synthesis_model.provider, audit_model=synthesis_model,
                                        queue_owner_id=requested_by_user_id or question.owner_id or 0)
    l0.project_id = question.project_id
    l0_synthesis.project_id = question.project_id
    session.add_all([l0, l0_synthesis])
    await session.flush()
    # L0 两项也参与增量复用：同版本题目最近一次评测的格式校验与合成题检测结果直接继承；
    # 两项全部继承时队列里没有任何工作项，由 Worker 恢复循环兜底触发 L0 收尾晋级。
    await inherit_successful_solves(session, redis, run, [l0, l0_synthesis], question)
    for work in (l0, l0_synthesis):
        if work.status == "queued":
            await enqueue(redis, work)
    await emit(session, redis, run.id, "start", {
        "questionId": question.id, "checkRunId": str(run.id), "checkTypes": [DIFFICULTY_ASSESSMENT_TYPE],
        "difficultyStatus": "validating_format", "currentLayer": "L0", "policy": policy,
    })
    return run


def make_check_work_items(run: CheckRun, check_types: list[str], queue_owner_id: int) -> list[CheckWorkItem]:
    works: list[CheckWorkItem] = []
    for check_type in check_types:
        if check_type == "latex":
            works.append(make_work(run, check_type, "check", 0, "rule", queue_owner_id=queue_owner_id))
        elif check_type == "difficulty":
            model = run_stage_model(run, check_type, "solve")
            works.extend(make_work(run, check_type, "solve", i, model.provider, "blocked", queue_owner_id, model) for i in range(1, model.pass_k + 1))
            works.append(make_work(run, check_type, "equivalence", 0, model.provider, "blocked", queue_owner_id, model))
        elif check_type == "answer":
            model = run_stage_model(run, check_type, "solve")
            comparison_model = run_stage_model(run, check_type, "equivalence")
            works.extend(make_work(run, check_type, "solve", i, model.provider, "blocked", queue_owner_id, model) for i in range(1, model.pass_k + 1))
            works.append(make_work(run, check_type, "equivalence", 0, comparison_model.provider, "blocked", queue_owner_id, comparison_model))
        elif check_type == "synthesis":
            # 合成题检测固定使用 SYNTHESIS_AUDIT_MODEL_ID，不随任务所选模型变化。
            synthesis_model = get_audit_model(SYNTHESIS_AUDIT_MODEL_ID)
            works.append(make_work(
                run, check_type, "synthesis", 0, synthesis_model.provider, "blocked", queue_owner_id,
                audit_model=synthesis_model,
            ))
        elif check_type == "similarity":
            model = run_stage_model(run, check_type, "similarity")
            works.append(make_work(run, check_type, "similarity", 0, model.provider, "queued", queue_owner_id, model))
        else:
            raise ValueError(f"unsupported check type: {check_type}")
    return works


def _reuse_ordinal_items(items: list[CheckWorkItem]) -> list[tuple[CheckWorkItem, Optional[str], str, int]]:
    """解析复用匹配要素：(层级, 模型, 该模型在本层的第几次作答)。

    payload 已固化 modelAttempt 时直接使用；旧数据回退为同层同模型内按
    attempt 升序的序号，这样策略里模型增减或顺序变化后仍能按“同模型
    第 n 次作答”对齐，而不是按层内累计序号错位失配。
    """
    counters: dict[tuple[Optional[str], str], int] = {}
    resolved: list[tuple[CheckWorkItem, Optional[str], str, int]] = []
    for item in sorted(items, key=lambda candidate: candidate.attempt):
        payload = item.payload if isinstance(item.payload, dict) else {}
        level = payload.get("level")
        model_id = (payload.get("model") or {}).get("id") or item.provider
        model_attempt = payload.get("modelAttempt")
        if model_attempt is None:
            group = (level, model_id)
            counters[group] = counters.get(group, 0) + 1
            model_attempt = counters[group]
        resolved.append((item, level, model_id, model_attempt))
    return resolved


REUSABLE_WORK_STAGES = ("solve", "assessment_solve", "assessment_format", "assessment_synthesis")


async def fetch_reusable_solves(session: AsyncSession, run: CheckRun, question: Optional[Question],
                                check_type: str) -> list[tuple[CheckWorkItem, Optional[str], str, int]]:
    """检索同题历史同检查项 CheckRun 中成功完成的工作项（作答与 L0 检测），
    返回 (工作项, 层级, 模型, 序号) 元组；序号必须在包含失败/取消项的全量
    集合内计算，调用方不得对返回值重新编号。

    空答案作答同样可复用（继承后按既有“空答案=答错”口径计入判定），
    避免输出截断导致的无谓重跑；仅要求 result 为非空 dict。

    复用源从最近一次历史任务向前追溯（限同题同版本，最多回看 5 次），
    累计各 run 已完成的可复用项：同一匹配键取最新 run 的结果；取消时作答
    尚未落库的 run 贡献不了作答，就继续向前找更早的完成记录。版本隔离不变：
    题目版本不一致或历史任务早于当前版本创建时不复用。
    """
    candidates = (await session.scalars(
        select(CheckRun).where(
            CheckRun.question_id == run.question_id,
            CheckRun.id != run.id,
            CheckRun.check_types.contains([check_type]),
        ).order_by(CheckRun.created_at.desc()).limit(5)
    )).all()
    version_created_at = question.current_version_created_at if question else None
    collected: dict[tuple[Optional[str], str, int], CheckWorkItem] = {}
    for previous_run in candidates:
        if previous_run.question_version != run.question_version:
            continue
        if version_created_at and previous_run.created_at and previous_run.created_at < version_created_at:
            continue
        all_items = (await session.scalars(
            select(CheckWorkItem).where(
                CheckWorkItem.run_id == previous_run.id,
                CheckWorkItem.check_type == check_type,
                CheckWorkItem.stage.in_(REUSABLE_WORK_STAGES),
            ).order_by(CheckWorkItem.created_at.desc())
        )).all()
        # 序号按同层同模型的全部作答项计算（含失败/取消项），否则已完成
        # 子集内重新编号会与展开侧的 attempt/modelAttempt 错位；结果只收
        # 集已完成且 result 非空的项。
        for item, level, model_id, ordinal in _reuse_ordinal_items(all_items):
            if item.status != "completed":
                continue
            if not isinstance(item.result, dict) or not item.result:
                continue
            collected.setdefault((level, model_id, ordinal), item)
    return [(item, *key) for key, item in collected.items()]


async def activate_equivalence_if_ready(session: AsyncSession, redis: Redis, run_id: uuid.UUID, check_type: str) -> None:
    """全部作答已到终态时解除答案比对阻塞；复用继承后可能不再有新作答完成事件来触发。"""
    pending = await session.scalar(select(CheckWorkItem.id).where(
        CheckWorkItem.run_id == run_id,
        CheckWorkItem.check_type == check_type,
        CheckWorkItem.stage == "solve",
        CheckWorkItem.status.not_in(["completed", "failed", "dead", "manual_review", "manual_review_archived"]),
    ).limit(1))
    if pending:
        return
    work = await session.scalar(select(CheckWorkItem).where(
        CheckWorkItem.run_id == run_id,
        CheckWorkItem.check_type == check_type,
        CheckWorkItem.stage == "equivalence",
        CheckWorkItem.status == "blocked",
    ))
    if not work:
        return
    completed_answer = await session.scalar(select(CheckWorkItem.id).where(
        CheckWorkItem.run_id == run_id,
        CheckWorkItem.check_type == check_type,
        CheckWorkItem.stage == "solve",
        CheckWorkItem.status == "completed",
        CheckWorkItem.result.is_not(None),
    ).limit(1))
    if not completed_answer:
        # 没有任何成功答案时保持阻塞，交由 Worker 的人工复核语义处理。
        return
    work.status = "queued"
    await session.flush()
    await enqueue(redis, work)


async def inherit_successful_solves(session: AsyncSession, redis: Redis, run: CheckRun,
                                    works: list[CheckWorkItem], question: Optional[Question] = None) -> None:
    """对即将入队的作答/L0 检测工作项，复用最近一次历史任务中已成功的结果，跳过重复模型调用。"""
    solve_works = [
        item for item in works
        if item.stage in REUSABLE_WORK_STAGES and item.status in ("queued", "blocked")
    ]
    if not solve_works:
        return

    cached_solves: dict[tuple[str, tuple[Optional[str], str, int]], CheckWorkItem] = {}
    for check_type in sorted({item.check_type for item in solve_works}):
        # fetch 已在全量集合内算好序号，这里不得重新编号。
        for prev, level, model_id, ordinal in await fetch_reusable_solves(session, run, question, check_type):
            key = (check_type, (level, model_id, ordinal))
            if key not in cached_solves:
                cached_solves[key] = prev

    work_keys = {
        item.id: (level, model_id, ordinal)
        for item, level, model_id, ordinal in _reuse_ordinal_items(solve_works)
    }
    inherited_count = 0
    for work in solve_works:
        cached = cached_solves.get((work.check_type, work_keys[work.id]))
        if cached:
            work.status = "completed"
            work.result = cached.result
            work.execution_ms = cached.execution_ms
            work.started_at = cached.started_at
            work.completed_at = cached.completed_at
            work.payload = {**(work.payload or {}), "inheritedFrom": str(cached.id)}
            inherited_count += 1

    if inherited_count > 0:
        await session.flush()
        # 全部作答都被继承时不会再有新的作答完成事件，这里主动唤醒比对任务。
        for check_type in sorted({item.check_type for item in solve_works}):
            if check_type != DIFFICULTY_ASSESSMENT_TYPE:
                await activate_equivalence_if_ready(session, redis, run.id, check_type)


async def add_check_work_items(session: AsyncSession, redis: Redis, run: CheckRun, check_types: list[str], queue_owner_id: int) -> None:
    works = make_check_work_items(run, check_types, queue_owner_id)
    question = await session.get(Question, run.question_id)
    if not question:
        raise LookupError("question not found")
    for work in works:
        work.project_id = question.project_id
    session.add_all(works)
    await session.flush()
    await inherit_successful_solves(session, redis, run, works, question)
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
                     model_id: Optional[str] = None, model_ids: Optional[dict[str, str]] = None,
                     requested_by_user_id: Optional[int] = None) -> CheckRun:
    types = list(dict.fromkeys(check_types or DEFAULT_CHECK_TYPES))
    # 不含难度/答案校验的任务没有可选模型语义；AI 合成题固定使用 deepseek-v4-flash。
    model = get_audit_model(model_id)
    selected_models = {key: get_audit_model(value) for key, value in (model_ids or {}).items()}
    invalid = set(types) - VALID_CHECK_TYPES
    if invalid:
        raise ValueError(f"unsupported check types: {', '.join(sorted(invalid))}")
    existing = await session.scalar(select(CheckRun).where(CheckRun.idempotency_key == idempotency_key))
    if existing:
        if run_audit_model(existing).id != model.id:
            raise ActiveModelConflictError("同一幂等请求已使用其他模型创建，不能切换模型")
        return existing
    question = await session.get(Question, question_id, with_for_update=True)
    if not question:
        raise LookupError("question not found")
    if question.similarity_status != "clear":
        raise ActiveModelConflictError("题目正在进行相似性校验或已标记为疑似套题，暂不能质检")
    # 同一版本、同一模型的 Pass@K 未完成轮次只补齐缺失的成功作答。
    # 失败工作项保持人工复核记录；补充工作项以新的执行序号追加，结果详情同时展示两者。
    if len(types) == 1 and types[0] in {"difficulty", "answer"}:
        check_type = types[0]
        stage_model = selected_models.get(check_type, model)
        previous = await session.scalar(
            select(CheckRun).where(
                CheckRun.question_id == question_id,
                CheckRun.status == "manual_review",
                CheckRun.check_types == [check_type],
            ).order_by(CheckRun.created_at.desc()).limit(1)
        )
        if previous and run_stage_model(previous, check_type, "solve").id == stage_model.id:
            works = (await session.scalars(select(CheckWorkItem).where(
                CheckWorkItem.run_id == previous.id,
                CheckWorkItem.check_type == check_type,
                CheckWorkItem.stage == "solve",
            ))).all()
            successful = [work for work in works if work.status == "completed" and (work.result or {}).get("answer")]
            missing = max(0, stage_model.pass_k - len(successful))
            if missing:
                previous.status = "queued"
                previous.completed_at = None
                for work in works:
                    if work.status in {"manual_review", "dead"}:
                        work.status = "manual_review_archived"
                next_attempt = max((work.attempt for work in works), default=0)
                supplements = [make_work(
                    previous, check_type, "solve", next_attempt + index, stage_model.provider,
                    "queued", requested_by_user_id or previous.requested_by_user_id or question.owner_id or 0, stage_model,
                    {"supplement": True, "targetPassK": stage_model.pass_k},
                ) for index in range(1, missing + 1)]
                comparison_model = run_stage_model(previous, check_type, "equivalence")
                prior_equivalences = (await session.scalars(select(CheckWorkItem).where(
                    CheckWorkItem.run_id == previous.id,
                    CheckWorkItem.check_type == check_type,
                    CheckWorkItem.stage == "equivalence",
                ))).all()
                for work in prior_equivalences:
                    if work.status in {"manual_review", "dead"}:
                        work.status = "manual_review_archived"
                equivalence_attempt = max((work.attempt for work in prior_equivalences), default=0) + 1
                supplements.append(make_work(
                    previous, check_type, "equivalence", equivalence_attempt, comparison_model.provider,
                    "blocked", requested_by_user_id or previous.requested_by_user_id or question.owner_id or 0, comparison_model,
                    {"supplement": True, "targetPassK": stage_model.pass_k},
                ))
                for work in supplements:
                    work.project_id = question.project_id
                session.add_all(supplements)
                await session.flush()
                for work in supplements:
                    if work.status == "queued":
                        await enqueue(redis, work)
                question.status = "checking"
                await emit(session, redis, previous.id, "start", {
                    "questionId": question_id, "checkTypes": types, "checkRunId": str(previous.id),
                    "model": stage_model.snapshot(), "supplemented": missing,
                })
                return previous
    active_runs = (await session.scalars(
        select(CheckRun).where(
            CheckRun.question_id == question_id,
            CheckRun.status.in_(["queued", "running", "cancelling", "paused"]),
        )
    )).all()
    conflicting_types: list[str] = []
    same_model_runs: list[CheckRun] = []
    for active in active_runs:
        # 难度分级使用完整策略快照而不是单一 AuditModel；它与常规
        # LaTeX/合成题质检可并行，不能参与原有的模型冲突判断。
        if DIFFICULTY_ASSESSMENT_TYPE in (active.check_types or []):
            continue
        overlap = overlapping_check_types(active, types)
        if run_audit_model(active).id == model.id:
            same_model_runs.append(active)
        elif overlap:
            conflicting_types.extend(overlap)
    if conflicting_types:
        labels = {"latex": "LaTeX 格式", "difficulty": "难度校验", "answer": "答案校验", "synthesis": "AI 合成题检测"}
        names = "、".join(labels.get(check_type, check_type) for check_type in dict.fromkeys(conflicting_types))
        raise ActiveModelConflictError(f"该题的{names}已有使用其他模型的进行中质检，请等待完成或取消后再切换模型")

    # 同模型沿用既有“补充未运行检查项”的行为；不同模型但检查项不重叠时，
    # 创建独立 CheckRun，以便答案、难度等可以并行使用不同模型。
    if same_model_runs:
        active = next((run for run in same_model_runs if overlapping_check_types(run, types)), same_model_runs[0])
        current_types = list(active.check_types or [])
        added_types = [check_type for check_type in types if check_type not in current_types]
        if added_types:
            active.check_types = [*current_types, *added_types]
            await add_check_work_items(
                session, redis, active, added_types,
                requested_by_user_id or active.requested_by_user_id or question.owner_id or 0,
            )
            await emit(session, redis, active.id, "start", {
                "questionId": question_id,
                "checkTypes": added_types,
                "checkRunId": str(active.id),
                "model": model.snapshot(), "added": True,
            })
        return active
    run = CheckRun(question_id=question_id, requested_by_user_id=requested_by_user_id, batch_id=batch_id, check_types=types, priority=priority,
                   status="queued", idempotency_key=idempotency_key,
                   model_versions={**model.snapshot(), "models": {key: selected.snapshot() for key, selected in selected_models.items()}})
    question.status = "checking"
    session.add(run)
    await session.flush()
    await add_check_work_items(session, redis, run, types, requested_by_user_id or question.owner_id or 0)
    await emit(session, redis, run.id, "start", {"questionId": question_id, "checkTypes": types,
               "checkRunId": str(run.id), "model": model.snapshot()})
    return run
