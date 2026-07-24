import uuid
from types import SimpleNamespace

import httpx
import pytest

import app.main as main
from app.config import Settings
from app.models import CheckRun
from app.queue import provider_limit
from app.schemas import CheckRequest
from app.services import make_check_work_items, provider_error


def test_full_check_creates_default_model_work_items_with_batch_owner() -> None:
    run = CheckRun(id=uuid.uuid4(), question_id=42, priority="batch", prompt_version="v1")
    items = make_check_work_items(run, ["latex", "difficulty", "answer", "synthesis"], queue_owner_id=7)

    assert len(items) == 20
    assert sum(item.provider == "doubao" for item in items) == 19
    assert sum(item.provider == "gemini" for item in items) == 0
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


def test_deep_inference_timeout_is_covered_by_worker_lease() -> None:
    settings = Settings()

    assert settings.ai_model_read_timeout_seconds == 3_600
    assert settings.lease_seconds > settings.ai_model_read_timeout_seconds


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
async def test_start_check_does_not_require_backend_provider_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The API only validates the model ID; the Worker owns provider credentials."""
    created: dict[str, object] = {}

    async def visible_question(*_: object, **__: object) -> object:
        return SimpleNamespace(id=42)

    async def create_check_run(*_: object, **kwargs: object) -> object:
        created.update(kwargs)
        return SimpleNamespace(id=uuid.uuid4(), status="queued")

    class Session:
        async def commit(self) -> None:
            return None

    monkeypatch.setattr(main, "get_visible_question", visible_question)
    monkeypatch.setattr(main, "create_run", create_check_run)

    response = await main.start_check(
        42,
        CheckRequest(checkTypes=["answer"], model="doubao-seed-2-1-pro-260628"),
        current_user=SimpleNamespace(id=7),
        idempotency_key="test-without-backend-key",
        session=Session(),
    )

    assert response["status"] == "queued"
    assert created["model_id"] == "doubao-seed-2-1-pro-260628"
