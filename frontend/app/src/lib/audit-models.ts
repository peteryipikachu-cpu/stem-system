export const AUDIT_MODELS = [
  { id: "doubao-seed-2-0-pro-260215", label: "doubao-2.0-pro", passK: 8, difficultyThreshold: 6 },
  { id: "doubao-seed-2-1-pro-260628", label: "doubao-2.1-pro", passK: 4, difficultyThreshold: 2 },
  { id: "gemini-3.1-pro-preview", label: "gemini-3.1-pro", passK: 8, difficultyThreshold: 6 },
] as const;

export type AuditModelId = (typeof AUDIT_MODELS)[number]["id"];
export const DEFAULT_AUDIT_MODEL_ID: AuditModelId = "doubao-seed-2-0-pro-260215";

export function auditModelLabel(model?: { label?: unknown; id?: unknown }): string {
  if (typeof model?.label === "string") return model.label;
  return AUDIT_MODELS.find((item) => item.id === model?.id)?.label || "默认模型";
}
