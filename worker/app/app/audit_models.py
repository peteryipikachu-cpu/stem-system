from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class AuditModel:
    id: str
    label: str
    provider: str
    pass_k: int
    difficulty_threshold: int

    def snapshot(self) -> dict[str, str | int]:
        return {"id": self.id, "label": self.label, "provider": self.provider,
                "passK": self.pass_k, "difficultyThreshold": self.difficulty_threshold}


DEFAULT_AUDIT_MODEL_ID = "doubao-seed-2-0-pro-260215"
AUDIT_MODELS = {
    "doubao-seed-2-0-pro-260215": AuditModel("doubao-seed-2-0-pro-260215", "doubao-2.0-pro", "doubao", 8, 6),
    "doubao-seed-2-1-pro-260628": AuditModel("doubao-seed-2-1-pro-260628", "doubao-2.1-pro", "doubao", 4, 2),
    "gemini-3.1-pro-preview": AuditModel("gemini-3.1-pro-preview", "gemini-3.1-pro", "gemini", 8, 6),
}


def get_audit_model(model_id: Optional[str] = None) -> AuditModel:
    resolved_id = model_id or DEFAULT_AUDIT_MODEL_ID
    try:
        return AUDIT_MODELS[resolved_id]
    except KeyError as exc:
        raise ValueError(f"不支持的审核模型：{resolved_id}") from exc


def model_from_snapshot(snapshot: object) -> AuditModel:
    if isinstance(snapshot, dict) and isinstance(snapshot.get("id"), str):
        return get_audit_model(snapshot["id"])
    return get_audit_model()
