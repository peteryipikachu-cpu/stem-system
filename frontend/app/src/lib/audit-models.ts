export const AUDIT_MODELS = [
  { id: "doubao-seed-2-0-pro-260215", label: "doubao-2.0-pro", passK: 8, difficultyThreshold: 6 },
  { id: "doubao-seed-2-1-pro-260628", label: "doubao-2.1-pro", passK: 4, difficultyThreshold: 2 },
  { id: "gemini-3.1-pro-preview", label: "gemini-3.1-pro", passK: 8, difficultyThreshold: 6 },
  { id: "glm-5.2", label: "glm-5.2", passK: 3, difficultyThreshold: 2 },
  { id: "qwen3.7-max", label: "qwen3.7-max", passK: 3, difficultyThreshold: 2 },
  { id: "kimi-k3", label: "kimi-k3", passK: 3, difficultyThreshold: 2 },
] as const;

export type AuditModelId = (typeof AUDIT_MODELS)[number]["id"];
export const DEFAULT_AUDIT_MODEL_ID: AuditModelId = "doubao-seed-2-0-pro-260215";

export function auditModelLabel(model?: { label?: unknown; id?: unknown }): string {
  if (typeof model?.label === "string") return model.label;
  return AUDIT_MODELS.find((item) => item.id === model?.id)?.label || "默认模型";
}
