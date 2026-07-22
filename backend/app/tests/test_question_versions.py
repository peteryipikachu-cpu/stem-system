from datetime import datetime, timezone

from app.models import CheckResult, Question, QuestionVersion, User
from app.services import question_snapshot_json, question_version_json


def test_question_snapshot_preserves_content_and_check_results() -> None:
    now = datetime(2026, 7, 17, tzinfo=timezone.utc)
    author = User(id=9, username="editor", password_hash="hash")
    question = Question(
        id=12,
        title="旧题目",
        question="x + 1 = 2",
        answer="1",
        status="done",
        current_version=2,
        current_version_created_at=now,
        current_version_author=author,
        current_version_note="修正题干",
        created_at=now,
        updated_at=now,
    )
    question.check_results = [
        CheckResult(
            check_type="latex",
            result="pass",
            detail={"errors": []},
            raw_responses=[],
            created_at=now,
            updated_at=now,
        ),
    ]

    snapshot = question_snapshot_json(question)

    assert snapshot["currentVersion"] == 2
    assert snapshot["versionAuthor"] == {"id": 9, "username": "editor"}
    assert snapshot["checkResults"][0]["checkType"] == "latex"
    assert snapshot["checkResults"][0]["result"] == "pass"


def test_historical_version_response_is_read_only_snapshot() -> None:
    now = datetime(2026, 7, 17, tzinfo=timezone.utc)
    author = User(id=9, username="editor", password_hash="hash")
    version = QuestionVersion(
        question_id=12,
        version_number=2,
        snapshot={"title": "旧题目", "status": "done", "checkResults": []},
        version_created_at=now,
        author=author,
        change_note="修正题干",
        created_at=now,
    )

    data = question_version_json(version, include_snapshot=True)

    assert data["isHistorical"] is True
    assert data["currentVersion"] == 2
    assert data["versionAuthor"] == {"id": 9, "username": "editor"}
    assert data["changeNote"] == "修正题干"
    assert data["checkSummary"] == "未质检"
