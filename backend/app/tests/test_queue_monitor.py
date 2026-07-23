from datetime import datetime, timedelta, timezone
import uuid

from app.main import queue_run_diagnosis
from app.models import CheckWorkItem


NOW = datetime(2026, 7, 23, 8, 0, tzinfo=timezone.utc)


def work(*, status: str, check_type: str = "answer", stage: str = "solve", **values: object) -> CheckWorkItem:
    defaults: dict[str, object] = {
        "run_id": uuid.uuid4(), "question_id": 1, "check_type": check_type, "stage": stage,
        "status": status, "provider": "doubao", "available_at": NOW,
        "created_at": NOW, "updated_at": NOW,
    }
    defaults.update(values)
    return CheckWorkItem(**defaults)


def test_worker_offline_marks_active_run_stuck() -> None:
    diagnosis = queue_run_diagnosis([work(status="queued")], NOW, False, 900)

    assert diagnosis["health"] == "stuck"
    assert diagnosis["label"] == "Worker 离线"


def test_expired_lease_marks_running_work_stuck() -> None:
    diagnosis = queue_run_diagnosis(
        [work(status="running", lease_expires_at=NOW - timedelta(seconds=1))], NOW, True, 900,
    )

    assert diagnosis["health"] == "stuck"
    assert diagnosis["label"] == "运行租约过期"


def test_ready_work_waiting_too_long_needs_attention() -> None:
    diagnosis = queue_run_diagnosis(
        [work(status="queued", created_at=NOW - timedelta(seconds=901))], NOW, True, 900,
    )

    assert diagnosis["health"] == "attention"


def test_retry_backoff_is_normal_waiting() -> None:
    diagnosis = queue_run_diagnosis(
        [work(status="queued", available_at=NOW + timedelta(minutes=1), created_at=NOW - timedelta(hours=1))], NOW, True, 900,
    )

    assert diagnosis["health"] == "normal"
    assert diagnosis["label"] == "重试等待"


def test_ready_dependency_left_blocked_is_stuck() -> None:
    run_id = uuid.uuid4()
    latex = work(status="completed", check_type="latex", stage="check", run_id=run_id)
    blocked = work(status="blocked", run_id=run_id, updated_at=NOW - timedelta(seconds=31))

    diagnosis = queue_run_diagnosis([latex, blocked], NOW, True, 900)

    assert diagnosis["health"] == "stuck"
    assert diagnosis["label"] == "依赖未唤醒"
