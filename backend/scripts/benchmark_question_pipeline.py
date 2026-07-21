#!/usr/bin/env python3
"""运行一题真实审核链路基准，并输出不含题目内容的 Markdown 报告。

该脚本通过 FastAPI 创建全量质检任务，再启动一个临时 Worker 消费 Redis 队列；
因此统计包含 API 入队、依赖解锁、全局并发限制和结果落库这条实际链路。

示例：
  DATABASE_URL='postgresql+asyncpg://USER@localhost/stem_audit' \
  REDIS_URL='redis://localhost:6379/0' \
  python backend/scripts/benchmark_question_pipeline.py --question-id 1

执行会覆盖该题当前版本的质检结果，历史版本不会被修改，并会产生真实模型调用费用。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import signal
import subprocess
import sys
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"

# 估算单价，单位为人民币/千 token。请按实际 APIRoute 账单更新，不作为账单金额。
PRICING_PER_K = {
    "doubao": (0.0096, 0.0240),
    "doubao_fast": (0.0032, 0.0160),
    "gemini": (0.0020, 0.0120),
    "rule": (0.0, 0.0),
}
DISPLAY_NAMES = {
    "latex": "LaTeX 格式",
    "difficulty": "难度校验",
    "answer": "答案校验",
    "synthesis": "AI 合成题检测",
    "plagiarism": "重复题检测",
}


def load_local_environment() -> None:
    """让临时 Worker 使用本地已有凭据，但绝不输出凭据内容。"""
    for path in (REPOSITORY_ROOT / ".env.local", REPOSITORY_ROOT / ".env.backend"):
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("\"'")
            if key and key not in os.environ:
                os.environ[key] = value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="执行单题真实质检链路基准")
    parser.add_argument("--question-id", type=int, default=1, help="待测当前题目 ID（默认：1）")
    parser.add_argument("--run-id", help="仅从数据库读取既有运行并生成报告，不创建新任务")
    parser.add_argument("--api-url", default=os.getenv("BENCHMARK_API_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--redis-url", default=os.getenv("REDIS_URL"))
    parser.add_argument("--timeout-seconds", type=int, default=14_400, help="整题等待上限（默认：4 小时）")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--output-dir", type=Path, default=REPOSITORY_ROOT / "reports")
    parser.add_argument("--no-spawn-worker", action="store_true", help="不启动临时 Worker，使用已运行的 Worker")
    return parser.parse_args()


def as_number(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def seconds_between(start: Optional[datetime], end: Optional[datetime]) -> Optional[float]:
    if not start or not end:
        return None
    return max(0.0, (end - start).total_seconds())


def display_seconds(value: Optional[float]) -> str:
    if value is None:
        return "—"
    if value < 60:
        return f"{value:.2f} 秒"
    return f"{int(value // 60)} 分 {value % 60:.1f} 秒"


def work_pricing_key(provider: str, stage: str) -> str:
    return "doubao_fast" if provider == "doubao" and stage == "equivalence" else provider


async def select_benchmark_user(question_id: int) -> str:
    """为真实 API 请求签发本地会话；请求仍由 FastAPI 完整鉴权与入队。"""
    sys.path.insert(0, str(BACKEND_ROOT))
    from sqlalchemy import select

    from app.auth import create_session_token
    from app.db import SessionLocal
    from app.models import Question, User

    async with SessionLocal() as session:
        question = await session.get(Question, question_id)
        if not question:
            raise RuntimeError(f"题目 ID {question_id} 不存在")
        user = await session.get(User, question.owner_id) if question.owner_id else None
        if not user:
            user = await session.scalar(select(User).where(User.role == "admin", User.is_active.is_(True)).limit(1))
        if not user:
            raise RuntimeError("找不到可访问该题目的有效所有者或管理员")
        return create_session_token(user)


async def collect_run(run_id: str) -> tuple[Any, list[Any]]:
    sys.path.insert(0, str(BACKEND_ROOT))
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import CheckRun, CheckWorkItem

    async with SessionLocal() as session:
        run = await session.get(CheckRun, uuid.UUID(run_id))
        if not run:
            raise RuntimeError(f"找不到刚创建的运行 {run_id}")
        works = (await session.scalars(
            select(CheckWorkItem)
            .where(CheckWorkItem.run_id == run.id)
            .order_by(CheckWorkItem.check_type, CheckWorkItem.stage, CheckWorkItem.attempt)
        )).all()
        return run, works


async def stop_worker(process: Optional[asyncio.subprocess.Process]) -> None:
    if not process or process.returncode is not None:
        return
    process.send_signal(signal.SIGINT)
    try:
        await asyncio.wait_for(process.wait(), timeout=15)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()


def render_report(
    *, question_id: int, run: Any, works: list[Any], api_submit_seconds: float,
    observed_wall_seconds: float, report_created_at: datetime,
) -> str:
    module_rows: dict[str, list[Any]] = defaultdict(list)
    provider_totals: dict[str, dict[str, float]] = defaultdict(lambda: {
        "calls": 0, "input": 0, "output": 0, "total": 0, "cost": 0.0,
    })
    work_lines: list[str] = []
    for work in works:
        module_rows[work.check_type].append(work)
        usage = (work.result or {}).get("usage") if isinstance(work.result, dict) else None
        usage = usage if isinstance(usage, dict) else {}
        input_tokens = as_number(usage.get("prompt_tokens"))
        output_tokens = as_number(usage.get("completion_tokens"))
        total_tokens = as_number(usage.get("total_tokens")) or input_tokens + output_tokens
        pricing_key = work_pricing_key(work.provider, work.stage)
        input_price, output_price = PRICING_PER_K.get(pricing_key, (0.0, 0.0))
        cost = input_tokens / 1_000 * input_price + output_tokens / 1_000 * output_price
        totals = provider_totals[pricing_key]
        totals["calls"] += 1 if work.provider != "rule" else 0
        totals["input"] += input_tokens
        totals["output"] += output_tokens
        totals["total"] += total_tokens
        totals["cost"] += cost
        work_lines.append(
            "| {module} | {stage} | {provider} | {status} | {queue} | {execute} | {model} | {prompt} | {completion} | {cost:.5f} |".format(
                module=DISPLAY_NAMES.get(work.check_type, work.check_type),
                stage=work.stage,
                provider=pricing_key,
                status=work.status,
                queue=display_seconds(seconds_between(work.created_at, work.started_at)),
                execute=display_seconds((work.execution_ms or 0) / 1_000),
                model=display_seconds(seconds_between(work.started_at, work.completed_at)),
                prompt=input_tokens or "—",
                completion=output_tokens or "—",
                cost=cost,
            )
        )

    module_lines: list[str] = []
    for check_type, items in sorted(module_rows.items()):
        starts = [item.started_at for item in items if item.started_at]
        ends = [item.completed_at for item in items if item.completed_at]
        span = seconds_between(min(starts), max(ends)) if starts and ends else None
        execution_seconds = sum((item.execution_ms or 0) / 1_000 for item in items)
        statuses = "/".join(sorted({item.status for item in items}))
        module_lines.append(
            f"| {DISPLAY_NAMES.get(check_type, check_type)} | {len(items)} | {statuses} | "
            f"{display_seconds(span)} | {display_seconds(execution_seconds)} |"
        )

    provider_lines: list[str] = []
    for provider, totals in sorted(provider_totals.items()):
        provider_lines.append(
            "| {provider} | {calls:.0f} | {input:.0f} | {output:.0f} | {total:.0f} | ¥{cost:.5f} |".format(
                provider=provider,
                **totals,
            )
        )
    total_input = sum(item["input"] for item in provider_totals.values())
    total_output = sum(item["output"] for item in provider_totals.values())
    total_tokens = sum(item["total"] for item in provider_totals.values())
    total_cost = sum(item["cost"] for item in provider_totals.values())
    run_queue_wait = seconds_between(run.created_at, run.started_at)

    return f"""# 单题质检链路基准报告

- 测试题目：当前版本题目 ID `{question_id}`
- 审核运行：`{run.id}`
- 生成时间：{report_created_at.astimezone(timezone.utc).isoformat()}
- 最终状态：**{run.status}**
- 本次 API 创建请求耗时：{display_seconds(api_submit_seconds)}
- 运行排队至首个工作项开始：{display_seconds(run_queue_wait)}
- 从 API 提交到终态的观测总耗时：**{display_seconds(observed_wall_seconds)}**

> 本报告通过 `POST /api/questions/{{id}}/check` 创建任务，并由 Redis 队列和独立 Worker 执行。
> “模块跨度”包含依赖等待、排队和重试；“累计实际执行”仅累计规则/模型调用时间，因此并发工作项会大于模块跨度。

## 模块耗时

| 模块 | 工作项数 | 最终状态 | 模块跨度 | 累计实际执行 |
| --- | ---: | --- | ---: | ---: |
{chr(10).join(module_lines) or "| 无 | 0 | — | — | — |"}

## Token 与估算成本

| 计费模型 | 调用数 | 输入 token | 输出 token | 合计 token | 估算成本 |
| --- | ---: | ---: | ---: | ---: |
{chr(10).join(provider_lines) or "| 无上游调用 | 0 | 0 | 0 | 0 | ¥0.00000 |"}
| **合计** | **{sum(item['calls'] for item in provider_totals.values()):.0f}** | **{total_input:.0f}** | **{total_output:.0f}** | **{total_tokens:.0f}** | **¥{total_cost:.5f}** |

计价假设为本地基准配置（人民币/千 token）：Doubao 深度输入/输出 `0.0096/0.0240`，Doubao 等价判断 `0.0032/0.0160`，Gemini `0.0020/0.0120`。请以 APIRoute 实际账单为准；上游未返回 `usage` 时 token 与成本显示为 0。

## 工作项明细

| 模块 | 阶段 | 计费模型 | 状态 | 首次领取前等待 | 累计实际执行 | 首次开始至终态 | 输入 token | 输出 token | 估算成本 |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
{chr(10).join(work_lines)}
"""


async def main() -> int:
    load_local_environment()
    args = parse_args()
    if not args.database_url:
        raise RuntimeError("缺少 DATABASE_URL；请传入 --database-url 或设置环境变量")
    if not args.redis_url:
        raise RuntimeError("缺少 REDIS_URL；请传入 --redis-url 或设置环境变量")
    os.environ["DATABASE_URL"] = args.database_url
    os.environ["REDIS_URL"] = args.redis_url

    if args.run_id:
        run, works = await collect_run(args.run_id)
        report = render_report(
            question_id=run.question_id,
            run=run,
            works=works,
            api_submit_seconds=0,
            observed_wall_seconds=seconds_between(run.created_at, run.completed_at or datetime.now(timezone.utc)) or 0,
            report_created_at=datetime.now(timezone.utc),
        )
        args.output_dir.mkdir(parents=True, exist_ok=True)
        path = args.output_dir / f"质检基准-运行{run.id}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
        path.write_text(report, encoding="utf-8")
        print(f"运行 {run.id} 当前状态：{run.status}")
        print(f"报告：{path}")
        return 0

    cookie = await select_benchmark_user(args.question_id)
    api_url = args.api_url.rstrip("/")
    worker: Optional[asyncio.subprocess.Process] = None
    started = time.monotonic()
    submit_started = time.monotonic()
    try:
        async with httpx.AsyncClient(base_url=api_url, timeout=30) as client:
            health = await client.get("/healthz")
            health.raise_for_status()
            cookies = {"stem_session": cookie}
            question_response = await client.get(f"/api/questions/{args.question_id}", cookies=cookies)
            question_response.raise_for_status()
            active = question_response.json().get("activeCheckRun")
            if active and active.get("status") in {"queued", "running", "cancelling"}:
                raise RuntimeError(
                    f"题目 {args.question_id} 已有进行中运行 {active.get('id')}，为避免重复计费，本次未创建测试。"
                )
            create = await client.post(
                f"/api/questions/{args.question_id}/check",
                headers={"Idempotency-Key": f"benchmark:{args.question_id}:{uuid.uuid4()}"},
                cookies=cookies,
                json={"checkTypes": ["latex", "difficulty", "answer", "synthesis"]},
            )
            create.raise_for_status()
            run_id = str(create.json()["checkRunId"])
            api_submit_seconds = time.monotonic() - submit_started

            if not args.no_spawn_worker:
                worker_env = os.environ.copy()
                worker_env["WORKER_ID"] = f"benchmark-{uuid.uuid4().hex[:8]}"
                worker = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    "app.worker",
                    cwd=str(BACKEND_ROOT),
                    env=worker_env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

            deadline = time.monotonic() + args.timeout_seconds
            terminal_statuses = {"completed", "manual_review", "cancelled", "failed", "dead"}
            latest_status = "queued"
            while time.monotonic() < deadline:
                response = await client.get(f"/api/check-runs/{run_id}", cookies=cookies)
                response.raise_for_status()
                latest_status = response.json()["status"]
                if latest_status in terminal_statuses:
                    break
                await asyncio.sleep(args.poll_seconds)
            else:
                raise TimeoutError(f"运行 {run_id} 在 {args.timeout_seconds} 秒内未到达终态（当前：{latest_status}）")
    finally:
        await stop_worker(worker)

    run, works = await collect_run(run_id)
    observed_wall_seconds = time.monotonic() - started
    report = render_report(
        question_id=args.question_id,
        run=run,
        works=works,
        api_submit_seconds=api_submit_seconds,
        observed_wall_seconds=observed_wall_seconds,
        report_created_at=datetime.now(timezone.utc),
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    path = args.output_dir / f"质检基准-题目{args.question_id}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
    path.write_text(report, encoding="utf-8")
    print(f"运行 {run_id} 已结束，状态：{run.status}")
    print(f"报告：{path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
