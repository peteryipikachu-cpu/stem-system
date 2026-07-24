import uuid

from app.models import CheckRun
from app.services import overlapping_check_types


def make_run(check_types: list[str]) -> CheckRun:
    return CheckRun(
        id=uuid.uuid4(),
        question_id=1,
        idempotency_key=str(uuid.uuid4()),
        check_types=check_types,
        model_versions={"id": "doubao-seed-2-1-pro-260628"},
    )


def test_active_run_only_conflicts_for_overlapping_check_types() -> None:
    run = make_run(["difficulty"])

    assert overlapping_check_types(run, ["answer"]) == []
    assert overlapping_check_types(run, ["difficulty", "answer"]) == ["difficulty"]
