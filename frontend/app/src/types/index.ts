export type CheckType = "latex" | "difficulty" | "answer" | "synthesis";
export type CheckResult = "pass" | "fail" | "warning" | "pending" | "running" | "manual_review";
export type QuestionStatus = "pending" | "checking" | "done" | "manual_review";

export interface LatexError {
  location: string;
  description: string;
  suggestion: string;
}

export interface LatexCheckDetail {
  errors: LatexError[];
}

export interface DifficultyCheckDetail {
  correctCount: number;
  totalCount: number;
  threshold: number;
  responses: string[];
}

export interface AnswerCheckDetail {
  correctCount: number;
  totalCount: number;
  responses: string[];
}

export interface PlagiarismCheckDetail {
  modelAnswer: string;
  isCorrect: boolean;
  confidence: number;
}

export interface SynthesisReason {
  type: "numerical_trick" | "template_based" | "fabricated_formula" | "useless_conditions";
  evidence: string;
}

export interface SynthesisCheckDetail {
  isSynthetic: boolean;
  confidence: number;
  reasons: SynthesisReason[];
  ruleViolations: string[];
}

export interface CheckResultRecord {
  id: number;
  questionId: number;
  checkType: CheckType;
  result: CheckResult;
  detail: LatexCheckDetail | DifficultyCheckDetail | AnswerCheckDetail | PlagiarismCheckDetail | SynthesisCheckDetail;
  rawResponses: unknown[];
  createdAt: string;
  updatedAt: string;
}

export interface QuestionRecord {
  id: number;
  title: string;
  type: string;
  domain: string;
  difficulty: string;
  knowledgePoints: string;
  question: string;
  answer: string;
  solution: string;
  expertId: string;
  status: QuestionStatus;
  batchId?: string;
  currentVersion: number;
  versionCount: number;
  versionCreatedAt: string;
  versionAuthor?: { id: number; username: string } | null;
  changeNote?: string | null;
  isHistorical?: boolean;
  createdAt: string;
  updatedAt: string;
  checkResults?: CheckResultRecord[];
}

export const CHECK_TYPES: CheckType[] = ["latex", "difficulty", "answer", "synthesis"];

export const CHECK_TYPE_LABELS: Record<CheckType, string> = {
  latex: "LaTeX 格式",
  difficulty: "难度校验",
  answer: "答案校验",
  synthesis: "AI 合成题检测",
};
