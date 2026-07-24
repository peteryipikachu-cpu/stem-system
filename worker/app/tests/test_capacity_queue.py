import uuid

import httpx
import pytest

from app.config import Settings
from app.models import CheckRun, CheckWorkItem, Question
from app.queue import provider_limit, provider_scope
from app.services import execute_model, make_check_work_items, provider_error


def test_full_check_creates_sixteen_work_items_with_batch_owner() -> None:
    run = CheckRun(id=uuid.uuid4(), question_id=42, priority="batch", prompt_version="v1")
    items = make_check_work_items(run, ["latex", "difficulty", "answer", "synthesis"], queue_owner_id=7)

    assert len(items) == 16
    assert sum(item.provider == "doubao" for item in items) == 11
    assert sum(item.provider == "gemini" for item in items) == 4
    assert sum(item.provider == "rule" for item in items) == 1
    assert {item.queue_owner_id for item in items} == {7}


def test_stable_default_provider_limits_match_capacity_plan() -> None:
    settings = Settings(
        ai_limit_doubao_concurrency=3,
        ai_limit_doubao_deep_concurrency=2,
        ai_limit_doubao_fast_concurrency=1,
        ai_limit_gemini_concurrency=2,
        ai_limit_gemini_answer_concurrency=2,
    )
    deep = provider_limit(settings, "doubao", "solve")
    fast = provider_limit(settings, "doubao", "equivalence")
    gemini = provider_limit(settings, "gemini", "solve")

    assert (deep.total_concurrency, deep.lane_concurrency) == (3, 2)
    assert (fast.total_concurrency, fast.lane_concurrency) == (3, 1)
    assert (gemini.total_concurrency, gemini.lane_concurrency) == (2, 2)


def test_doubao_key_pool_is_deduplicated_and_secret_safe_in_redis_scope() -> None:
    settings = Settings(doubao_api_keys="first, second, first", doubao_api_key="legacy")

    assert settings.doubao_keys == ["first", "second", "legacy"]
    assert provider_scope("doubao", "first") != provider_scope("doubao", "second")
    assert "first" not in provider_scope("doubao", "first")


def test_doubao_deep_inference_timeout_is_covered_by_worker_lease() -> None:
    settings = Settings()

    assert settings.ai_model_read_timeout_seconds == 600
    assert settings.ai_doubao_read_timeout_seconds == 3_600
    assert settings.lease_seconds > settings.ai_doubao_read_timeout_seconds
    assert settings.ai_retry_max_attempts == 1


def test_gateway_error_is_structured_and_retryable() -> None:
    request = httpx.Request("POST", "https://example.test/v1/chat/completions")
    response = httpx.Response(504, request=request)
    code, status, message, retryable = provider_error(httpx.HTTPStatusError("gateway timeout", request=request, response=response))

    assert code == "http_504"
    assert status == 504
    assert "gateway" in message
    assert retryable is True


def test_missing_provider_key_is_not_retryable() -> None:
    code, status, _, retryable = provider_error(ValueError("provider API key not configured"))

    assert code == "provider_not_configured"
    assert status is None
    assert retryable is False


@pytest.mark.asyncio
async def test_doubao_solve_uses_final_answer_format_without_max_tokens(monkeypatch) -> None:
    request_bodies = []

    async def fake_call_chat(_, __, ___, body, stream=False):
        request_bodies.append(body)
        return "YES", {"usage": {}}

    monkeypatch.setattr("app.services.call_chat", fake_call_chat)
    settings = Settings(doubao_api_key="test-key")
    question = Question(question="解方程 x^2 = 1", answer="x=1 或 x=-1")

    await execute_model(CheckWorkItem(provider="doubao", stage="solve", payload={}), question, settings)
    await execute_model(
        CheckWorkItem(provider="doubao", stage="equivalence", payload={"answers": ["x=1 或 x=-1"]}),
        question,
        settings,
    )

    assert request_bodies[0]["thinking"] == {"type": "enabled"}
    assert request_bodies[0]["reasoning"] == {"effort": "high"}
    assert "max_tokens" not in request_bodies[0]
    assert "只输出答案本身" in request_bodies[0]["messages"][0]["content"]
    assert "$...$" in request_bodies[0]["messages"][0]["content"]
    assert request_bodies[1]["thinking"] == {"type": "disabled"}
    assert "reasoning" not in request_bodies[1]
    assert "max_tokens" not in request_bodies[1]


@pytest.mark.asyncio
async def test_difficulty_solve_requests_only_the_final_answer(monkeypatch) -> None:
    request_bodies = []

    async def fake_call_chat(_, __, ___, body, stream=False):
        request_bodies.append(body)
        return "42", {"usage": {}}

    monkeypatch.setattr("app.services.call_chat", fake_call_chat)
    settings = Settings(doubao_api_key="test-key")
    question = Question(question="求 6 乘 7", answer="42")
    work = CheckWorkItem(provider="doubao", check_type="difficulty", stage="solve", payload={})

    await execute_model(work, question, settings)

    assert "只输出答案本身" in request_bodies[0]["messages"][0]["content"]
    assert "不得输出裸 LaTeX 命令" in request_bodies[0]["messages"][0]["content"]


@pytest.mark.asyncio
async def test_gemini_final_answer_enables_thinking_without_max_tokens(monkeypatch) -> None:
    request_bodies = []

    async def fake_call_chat(_, __, ___, body, stream=False):
        request_bodies.append(body)
        return "$-7/8$", {"usage": {}}

    monkeypatch.setattr("app.services.call_chat", fake_call_chat)
    settings = Settings(gemini_api_key="test-key")
    question = Question(question="求常数 C", answer="-7/8")

    await execute_model(CheckWorkItem(provider="gemini", check_type="answer", stage="solve", payload={}), question, settings)

    assert request_bodies[0]["thinking"] == {"type": "enabled"}
    assert request_bodies[0]["reasoning"] == {"effort": "high"}
    assert "max_tokens" not in request_bodies[0]
    assert "只输出答案本身" in request_bodies[0]["messages"][0]["content"]
    assert "唯一对应关系" in request_bodies[0]["messages"][0]["content"]
