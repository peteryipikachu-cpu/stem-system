import type { AuditModelId } from "@/lib/audit-models";

export interface CheckRunAccepted {
  checkRunId: string;
  status: string;
}

export interface ActiveCheckProgress {
  checkType: string;
  total: number;
  completed: number;
  running: number;
  queued: number;
  blocked: number;
  solveTotal: number;
  solveCompleted: number;
  solveRunning: number;
  waitingForResult: boolean;
  completedAnswers: Array<{ attempt: number; answer: string }>;
}

export interface BatchAccepted {
  batchId: string;
  runIds: string[];
  status: string;
  deadlineAt: string | null;
}

export interface CheckBatchStatus {
  id: string;
  status: string;
  priority: string;
  checkTypes: string[];
  totalCount: number;
  completedCount: number;
  failedCount: number;
  manualReviewCount: number;
  queuedCount: number;
  runningCount: number;
  providerWaiting: Record<string, Record<string, number>>;
  deadlineAt: string | null;
  estimatedCompleteAt: string | null;
  secondsToDeadline: number | null;
  createdAt: string;
}

export interface CheckRunEvent {
  id?: number;
  type: "start" | "progress" | "complete" | "error" | string;
  questionId?: number;
  checkTypes?: string[];
  checkType?: string;
  status?: string;
  result?: string;
  detail?: unknown;
  message?: string;
  model?: { id: string; label: string; provider: string; passK: number; difficultyThreshold: number };
}

export interface CheckRunStatus {
  id: string;
  questionId: number;
  batchId: string | null;
  checkTypes: string[];
  priority: string;
  status: string;
  model: { id: string; label: string; provider: string; passK: number; difficultyThreshold: number };
  createdAt: string;
  startedAt: string | null;
  completedAt: string | null;
  progress: ActiveCheckProgress[];
}

function idempotencyKey() {
  return crypto.randomUUID();
}

async function accept<T>(url: string, body: unknown): Promise<T> {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey() },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => null) as { detail?: unknown } | null;
    throw new Error(typeof data?.detail === "string" ? data.detail : `HTTP ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export function startCheck(questionId: number, checkTypes: string[], model?: AuditModelId) {
  return accept<CheckRunAccepted>(`/api/questions/${questionId}/check`, { checkTypes, model });
}

export function startBatch(questionIds: number[], checkTypes: string[], model?: AuditModelId, deadlineAt?: string) {
  return accept<BatchAccepted>("/api/check-batches", { questionIds, checkTypes, model, deadlineAt });
}

export async function getCheckBatch(batchId: string): Promise<CheckBatchStatus> {
  const response = await fetch(`/api/check-batches/${batchId}`);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json() as Promise<CheckBatchStatus>;
}

export async function getCheckRun(checkRunId: string): Promise<CheckRunStatus> {
  const response = await fetch(`/api/check-runs/${checkRunId}`);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json() as Promise<CheckRunStatus>;
}

export function subscribeCheckRun(
  checkRunId: string,
  onEvent: (event: CheckRunEvent) => void,
): () => void {
  const source = new EventSource(`/api/check-runs/${checkRunId}/events`);
  const handle = (event: MessageEvent<string>) => {
    try { onEvent(JSON.parse(event.data) as CheckRunEvent); } catch { /* ignore invalid keepalive */ }
  };
  ["start", "progress", "complete", "error", "message"].forEach((type) => source.addEventListener(type, handle));
  return () => source.close();
}
