"use client";
// @refresh reset
import React, { useCallback, useEffect, useState } from "react";
import {
  Layout,
  Breadcrumb,
  Card,
  Tag,
  Button,
  Space,
  Typography,
  Divider,
  Collapse,
  Badge,
  message,
  Spin,
  Row,
  Col,
  Alert,
  Form,
  Input,
  Modal,
} from "antd";
import {
  ArrowLeftOutlined,
  PlayCircleOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  WarningOutlined,
  ClockCircleOutlined,
  SyncOutlined,
  CopyOutlined,
  EditOutlined,
} from "@ant-design/icons";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import useSWR from "swr";
import LatexRenderer from "@/components/LatexRenderer";
import ModelSelectionModal from "@/components/ModelSelectionModal";
import { CHECK_TYPE_LABELS, CheckType } from "@/types";
import { getCheckRun, startCheck, subscribeCheckRun, type CheckRunEvent } from "@/lib/check-runs";
import { auditModelLabel, DEFAULT_AUDIT_MODEL_ID, type AuditModelId } from "@/lib/audit-models";

const { Header, Content } = Layout;
const { Title, Text } = Typography;
const RUN_POLL_INTERVAL_MS = 2_500;
const TERMINAL_RUN_STATUSES = new Set(["completed", "partial_failed", "manual_review", "failed", "cancelled"]);
const REUSABLE_CHECK_RESULT_STATUSES = new Set(["pass", "fail", "warning"]);

const fetcher = async (url: string) => {
  const response = await fetch(url);
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "请求失败");
  return data;
};

function ResultIcon({ result }: { result: string }) {
  if (result === "pass") return <CheckCircleOutlined style={{ color: "#52c41a" }} />;
  if (result === "fail") return <CloseCircleOutlined style={{ color: "#ff4d4f" }} />;
  if (result === "warning") return <WarningOutlined style={{ color: "#faad14" }} />;
  if (result === "manual_review") return <WarningOutlined style={{ color: "#fa8c16" }} />;
  if (result === "running") return <SyncOutlined spin style={{ color: "#1890ff" }} />;
  return <ClockCircleOutlined style={{ color: "#999" }} />;
}

function ResultTag({ result }: { result: string }) {
  const config = {
    pass: { color: "success", text: "通过" },
    fail: { color: "error", text: "不通过" },
    warning: { color: "warning", text: "警告" },
    manual_review: { color: "orange", text: "人工复核" },
    running: { color: "processing", text: "检测中" },
    pending: { color: "default", text: "待检测" },
  }[result] || { color: "default", text: result };

  return <Tag color={config.color}>{config.text}</Tag>;
}

interface CheckResultData {
  checkType: string;
  result: string;
  detail: string;
}

function hasReusableCheckResult(result: CheckResultData): boolean {
  // 上游接口失败会落为 manual_review；它不是可复用的质检结论，允许直接重试。
  return REUSABLE_CHECK_RESULT_STATUSES.has(result.result);
}

function finalAnswerOnly(response: string): string {
  if (response.startsWith("[ERROR]")) return response;
  const finalAnswerMarker = /(?:^|\n)\s*#{0,6}\s*最终答案\s*[:：]?\s*/i;
  const candidate = response.split(finalAnswerMarker).pop()?.trim() || response.trim();
  const boxed = candidate.match(/\\boxed\{(?:[^{}]|\{[^{}]*\})*\}/);
  if (boxed) return boxed[0];
  return candidate.split(/\n\s*\n/)[0].trim() || "(空回复)";
}

function CheckResultCard({ cr, onRecheck, duration, readOnly = false }: { cr: CheckResultData; onRecheck: () => void; duration?: number; readOnly?: boolean }) {
  let detail: Record<string, unknown> = {};
  try {
    detail = JSON.parse(cr.detail || "{}");
  } catch {
    detail = {};
  }

  const label = CHECK_TYPE_LABELS[cr.checkType as CheckType] || cr.checkType;
  const modelLabel = auditModelLabel(detail.model as { label?: unknown; id?: unknown });

  const renderDetail = () => {
    if (cr.result === "manual_review") {
      return <Alert type="warning" title="已转人工复核" description={String(detail.message || "模型调用未完成，请由人工处理该审核项。")} />;
    }
    if (detail.error) {
      return <Alert type="error" title={String(detail.error)} />;
    }

    if (cr.checkType === "latex") {
      const errors = (detail.errors || []) as Array<{ location: string; description: string; suggestion: string }>;
      if (errors.length === 0) return <Text type="success">未发现 LaTeX 格式错误</Text>;
      return (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {errors.map((err, i) => (
            <div key={i} style={{ padding: "8px 0", borderBottom: "1px solid #f0f0f0" }}>
              <Space orientation="vertical" size={2} style={{ width: "100%" }}>
                <Text strong style={{ color: "#ff4d4f" }}>[{err.location}] {err.description}</Text>
                <Text type="secondary">建议：{err.suggestion}</Text>
              </Space>
            </div>
          ))}
        </div>
      );
    }

    if (cr.checkType === "difficulty") {
      const correct = Number(detail.correctCount ?? 0);
      const total = Number(detail.totalCount ?? 8);
      const threshold = Number(detail.threshold ?? 6);
      const responses = (detail.responses || []) as string[];
      const equivalences = (detail.equivalences || []) as boolean[];
      return (
        <Space orientation="vertical" style={{ width: "100%" }}>
          <Text>{modelLabel} 答对次数：<Text strong style={{ color: correct <= threshold ? "#52c41a" : "#ff4d4f" }}>{correct}/{total}</Text>（阈值 ≤{threshold}次）</Text>
          <Text type="secondary">答对 ≤{threshold} 次说明难度足够，答对 &gt;{threshold} 次说明难度不足</Text>
          {responses.length > 0 && (
            <div style={{ marginTop: 4 }}>
              <Text type="secondary" style={{ fontSize: 12 }}>每次答题结果：</Text>
              <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 6 }}>
                {responses.map((resp, i) => {
                  const isError = resp.startsWith("[ERROR]");
                  const answer = finalAnswerOnly(resp);
                  const isCorrect = equivalences[i] === true;
                  const bgColor = isError ? "#fff1f0" : isCorrect ? "#f6ffed" : "#fafafa";
                  const borderColor = isError ? "#ffa39e" : isCorrect ? "#b7eb8f" : "#d9d9d9";
                  const label = isError ? "调用失败" : "第" + (i + 1) + "次";
                  return (
                    <div key={i} style={{ background: bgColor, border: `1px solid ${borderColor}`, borderRadius: 6, padding: "6px 10px", fontSize: 12 }}>
                      <Space align="center" size={4}>
                        <Text strong style={{ fontSize: 12 }}>{label}</Text>
                        {!isError && <Tag color={isCorrect ? "success" : "default"} style={{ marginLeft: 4, fontSize: 11 }}>{isCorrect ? "✓ 等价" : "✕ 不等价"}</Tag>}
                      </Space>
                      <div style={{ fontSize: 12, color: "#333", marginTop: 2 }}>
                        <LatexRenderer content={answer} />
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
          {cr.result === "fail" && <Alert type="warning" title={`${modelLabel} 答对 ${correct} 次，超过阈值，难度可能不足`} />}
        </Space>
      );
    }

    if (cr.checkType === "answer") {
      const correct = Number(detail.correctCount ?? 0);
      const total = Number(detail.totalCount ?? 4);
      const responses = (detail.responses || []) as string[];
      const equivalences = (detail.equivalences || []) as boolean[];
      return (
        <Space orientation="vertical" style={{ width: "100%" }}>
          <Text>{modelLabel} 答对次数：<Text strong style={{ color: correct >= 1 ? "#52c41a" : "#ff4d4f" }}>{correct}/{total}</Text></Text>
          <Text type="secondary">≥1 次答对说明答案可信</Text>
          {responses.length > 0 && (
            <div style={{ marginTop: 4 }}>
              <Text type="secondary" style={{ fontSize: 12 }}>每次答题结果：</Text>
              <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 6 }}>
                {responses.map((resp, i) => {
                  const isError = resp.startsWith("[ERROR]");
                  const isCorrect = equivalences[i] === true;
                  const bgColor = isError ? "#fff1f0" : isCorrect ? "#f6ffed" : "#fafafa";
                  const borderColor = isError ? "#ffa39e" : isCorrect ? "#b7eb8f" : "#d9d9d9";
                  const label = isError ? "调用失败" : "第" + (i + 1) + "次";
                  return (
                    <div key={i} style={{ background: bgColor, border: `1px solid ${borderColor}`, borderRadius: 6, padding: "6px 10px", fontSize: 12 }}>
                      <Space align="center" size={4}>
                        <Text strong style={{ fontSize: 12 }}>{label}</Text>
                        {!isError && <Tag color={isCorrect ? "success" : "default"} style={{ marginLeft: 4, fontSize: 11 }}>{isCorrect ? "✓ 等价" : "✕ 不等价"}</Tag>}
                      </Space>
                      <div style={{ fontSize: 12, color: "#333", marginTop: 2 }}>
                        <LatexRenderer content={resp || "(空回复)"} />
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
          {cr.result === "fail" && <Alert type="error" title={`${modelLabel} ${total} 次均未答对，答案可能有误`} />}
        </Space>
      );
    }

    if (cr.checkType === "plagiarism") {
      const isCorrect = Boolean(detail.isCorrect);
      const modelAnswer = detail.modelAnswer ? String(detail.modelAnswer) : undefined;
      return (
        <Space orientation="vertical">
          <Text>混元模型是否答对：<Text strong style={{ color: isCorrect ? "#ff4d4f" : "#52c41a" }}>{isCorrect ? "是（疑似泄题）" : "否（正常）"}</Text></Text>
          {isCorrect && <Alert type="warning" title="混元模型直接答对了此题，该题可能已在腾讯内部流出或被用于训练" />}
          {modelAnswer && (
            <div>
              <Text type="secondary">混元回答摘要：</Text>
              <div style={{ marginTop: 4, fontSize: 12, background: "#fafafa", border: "1px solid #f0f0f0", borderRadius: 6, padding: "8px 12px" }}>
                <LatexRenderer content={modelAnswer} />
              </div>
            </div>
          )}
        </Space>
      );
    }

    if (cr.checkType === "synthesis") {
      const reasons = (detail.reasons || []) as Array<{ type: string; evidence: string }>;
      const ruleViolations = (detail.ruleViolations || []) as string[];
      const typeMap: Record<string, string> = {
        numerical_trick: "数值刁难",
        template_based: "模版化",
        fabricated_formula: "编造公式",
        useless_conditions: "堆砌条件",
      };

      return (
        <Space orientation="vertical" style={{ width: "100%" }}>
          <Text>是否疑似 AI 生成题：<Text strong style={{ color: detail.isSynthetic ? "#ff4d4f" : "#52c41a" }}>{detail.isSynthetic ? "是" : "否"}</Text>，置信度：<Text strong>{Number(detail.confidence ?? 0)}%</Text></Text>
          {ruleViolations.length > 0 && (
            <Alert type="warning" title={ruleViolations.join("；")} />
          )}
          {reasons.filter((r) => r.evidence && r.evidence !== "无此问题").map((r) => (
            <div key={r.type} style={{ background: "#fff7e6", padding: "8px 12px", borderRadius: 6 }}>
              <Text strong style={{ color: "#d46b08" }}>[{typeMap[r.type] || r.type}]</Text>
              <Text style={{ marginLeft: 8, fontSize: 13 }}>{r.evidence}</Text>
            </div>
          ))}
        </Space>
      );
    }

    return <pre style={{ fontSize: 12 }}>{JSON.stringify(detail, null, 2)}</pre>;
  };

  return (
    <Card
      size="small"
      title={
        <Space>
          <ResultIcon result={cr.result} />
          <Text strong>{label}</Text>
          <ResultTag result={cr.result} />
          {duration !== undefined && (
            <Tag color="default" style={{ fontSize: 11 }}>{(duration / 1000).toFixed(1)}s</Tag>
          )}
        </Space>
      }
      extra={!readOnly ? (
        <Button size="small" style={{ width: 94 }} onClick={onRecheck} icon={<SyncOutlined />}>
          重新检测
        </Button>
      ) : null}
      style={{ marginBottom: 12 }}
      styles={{ body: { padding: "12px 16px" } }}
    >
      {renderDetail()}
    </Card>
  );
}

interface Question {
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
  subject: string;
  status: string;
  currentVersion: number;
  versionCreatedAt?: string;
  versionAuthor?: { id: number; username: string } | null;
  changeNote?: string | null;
  isHistorical?: boolean;
  checkResults: CheckResultData[];
  activeCheckRun?: {
    id: string;
    checkTypes: string[];
    status: string;
  };
}

interface EditQuestionValues {
  title: string;
  type: string;
  domain: string;
  difficulty: string;
  knowledgePoints: string;
  question: string;
  answer: string;
  solution: string;
  expertId: string;
  subject: string;
  changeNote?: string;
}

export default function QuestionDetailPage() {
  const params = useParams();
  const router = useRouter();
  const searchParams = useSearchParams();
  const id = params?.id as string;
  const versionValue = searchParams.get("version");
  const historicalVersion = versionValue && /^\d+$/.test(versionValue) ? Number(versionValue) : null;
  const isHistorical = historicalVersion !== null;
  const [checkingTypes, setCheckingTypes] = useState<Set<string>>(new Set());
  const [checkProgress, setCheckProgress] = useState<Record<string, string>>({});
  const [checkDurations] = useState<Record<string, number>>({}); // 耗时 ms
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [pendingCheckTypes, setPendingCheckTypes] = useState<string[] | null>(null);
  const [startingCheck, setStartingCheck] = useState(false);
  const [form] = Form.useForm<EditQuestionValues>();
  const [modal, modalContextHolder] = Modal.useModal();

  const handleCopy = async (text: string, label: string) => {
    try {
      await navigator.clipboard.writeText(text || "");
      message.success(`已复制${label}`);
    } catch {
      message.error("复制失败，请手动选择复制");
    }
  };

  const { data: question, mutate, isLoading } = useSWR<Question>(
    id ? (isHistorical ? `/api/questions/${id}/versions/${historicalVersion}` : `/api/questions/${id}`) : null,
    fetcher
  );
  const { data: currentUser, error: authError } = useSWR<{ username: string; role: "user" | "admin" }>("/api/auth/me", fetcher);
  const activeRun = isHistorical ? undefined : question?.activeCheckRun;
  const activeRunId = activeRun?.id;
  const activeRunTypesKey = activeRun?.checkTypes.join(",") || "";

  useEffect(() => {
    if (!question || isHistorical) return;
    form.setFieldsValue({
      title: question.title,
      type: question.type,
      domain: question.domain,
      difficulty: question.difficulty,
      knowledgePoints: question.knowledgePoints,
      question: question.question,
      answer: question.answer,
      solution: question.solution,
      expertId: question.expertId,
      subject: question.subject || "其他",
      changeNote: "",
    });
  }, [question, isHistorical, form]);

  const monitorRun = useCallback((runId: string, requestedTypes: string[]) => {
    let stopped = false;
    let finished = false;
    let initialized = false;
    let close = () => {};
    let syncStatus: () => Promise<void> = async () => {};
    const pollTimer = window.setInterval(() => { void syncStatus(); }, RUN_POLL_INTERVAL_MS);

    const finish = (status: string) => {
      if (stopped || finished) return;
      finished = true;
      close();
      window.clearInterval(pollTimer);
      setCheckingTypes(new Set());
      mutate();
      if (status === "completed") message.success("质检完成");
      else if (status === "manual_review") message.warning("质检已转人工复核，请查看失败原因");
      else if (status === "partial_failed") message.warning("质检部分失败，请查看各项结果");
      else if (status === "cancelled") message.info("质检任务已取消");
      else message.error("质检任务失败");
    };

    syncStatus = async () => {
      try {
        const run = await getCheckRun(runId);
        if (stopped || finished) return;
        if (TERMINAL_RUN_STATUSES.has(run.status)) {
          finish(run.status);
          return;
        }
        const progress = run.status === "queued" ? "排队中" : "检测中";
        if (!initialized) {
          initialized = true;
          const types = run.checkTypes.length ? run.checkTypes : requestedTypes;
          setCheckingTypes(new Set(types));
        }
        setCheckProgress((previous) => {
          const next = { ...previous };
          const types = run.checkTypes.length ? run.checkTypes : requestedTypes;
          types.forEach((type) => {
            if (!next[type] || next[type] === "启动..." || next[type] === "排队中" || next[type].startsWith("检测中:")) {
              next[type] = progress;
            }
          });
          return next;
        });
      } catch {
        // SSE 仍可继续提供进度；下一轮轮询会重新以持久化状态校准。
      }
    };

    close = subscribeCheckRun(runId, (event: CheckRunEvent) => {
      if (event.type === "start") {
        initialized = true;
        const types = event.checkTypes?.length ? event.checkTypes : requestedTypes;
        setCheckingTypes((previous) => new Set([...previous, ...types]));
        setCheckProgress((previous) => {
          const next = { ...previous };
          types.forEach((type) => {
            if (next[type] !== "完成") next[type] = "排队中";
          });
          return next;
        });
        return;
      }
      if (event.type === "progress" && event.checkType) {
        const label = CHECK_TYPE_LABELS[event.checkType as CheckType] || event.checkType;
        if (event.status === "done") {
          setCheckingTypes((previous) => {
            const next = new Set(previous);
            next.delete(event.checkType!);
            return next;
          });
          setCheckProgress((previous) => ({ ...previous, [event.checkType!]: "完成" }));
          if (event.result && event.detail !== undefined) {
            mutate((previous: Question | undefined) => {
              if (!previous) return previous;
              const filtered = (previous.checkResults || []).filter((item) => item.checkType !== event.checkType);
              return { ...previous, checkResults: [...filtered, { id: Date.now(), questionId: previous.id, checkType: event.checkType!, result: event.result!, detail: JSON.stringify(event.detail), rawResponses: "[]", createdAt: new Date().toISOString() }] };
            }, false);
          }
        } else if (event.status === "error") {
          setCheckingTypes((previous) => {
            const next = new Set(previous);
            next.delete(event.checkType!);
            return next;
          });
          setCheckProgress((previous) => ({ ...previous, [event.checkType!]: `失败: ${event.message || "未知错误"}` }));
          mutate();
        } else if (event.status === "queued") {
          setCheckProgress((previous) => ({ ...previous, [event.checkType!]: "排队中" }));
        } else {
          setCheckProgress((previous) => ({ ...previous, [event.checkType!]: `检测中: ${label}` }));
        }
      }
      if (event.type === "complete") {
        finish(event.status || "completed");
      }
    });

    void syncStatus();
    return () => {
      stopped = true;
      close();
      window.clearInterval(pollTimer);
    };
  }, [mutate]);

  useEffect(() => {
    if (!activeRunId) return;
    return monitorRun(activeRunId, activeRunTypesKey ? activeRunTypesKey.split(",") : []);
  }, [activeRunId, activeRunTypesKey, monitorRun]);

  useEffect(() => {
    if (authError) router.replace("/login");
  }, [authError, router]);

  const startCheckWithModel = async (checkTypes: string[], model: AuditModelId) => {
    if (isHistorical) return;
    setCheckingTypes((previous) => new Set([...previous, ...checkTypes]));
    setCheckProgress((previous) => {
      const next = { ...previous };
      checkTypes.forEach((type) => { next[type] = "启动..."; });
      return next;
    });

    try {
      await startCheck(Number(id), checkTypes, model);
      await mutate();
    } catch (err) {
      const label = checkTypes.length === 1
        ? (CHECK_TYPE_LABELS[checkTypes[0] as CheckType] || checkTypes[0])
        : "全量质检";
      message.error(`${label}失败: ${err instanceof Error ? err.message : "Unknown"}`);
      setCheckingTypes((previous) => {
        const next = new Set(previous);
        checkTypes.forEach((type) => next.delete(type));
        return next;
      });
    }
  };

  const openModelSelection = (checkTypes: string[]) => {
    if (checkTypes.length > 0 && checkTypes.every((checkType) => checkType === "latex")) {
      void startCheckWithModel(checkTypes, DEFAULT_AUDIT_MODEL_ID);
      return;
    }
    setPendingCheckTypes(checkTypes);
  };

  const handleCheck = (checkTypes: string[]) => {
    if (isHistorical) return;

    // 单项检测只关心该检查项是否已有结果；不能因为其他项已完成而误提示重检。
    const existingRequestedResults = question?.checkResults?.filter(
      (result) => checkTypes.includes(result.checkType) && hasReusableCheckResult(result),
    ) || [];
    if (existingRequestedResults.length === 0) {
      openModelSelection(checkTypes);
      return;
    }

    const label = checkTypes.length === 1
      ? (CHECK_TYPE_LABELS[checkTypes[0] as CheckType] || checkTypes[0])
      : "当前选择的检查项";

    modal.confirm({
      title: "已有结果",
      content: `${label}已有质检结果，是否重新检测？`,
      okText: "重新检测",
      cancelText: "取消",
      okButtonProps: { style: { width: 94 } },
      onOk: () => openModelSelection(checkTypes),
    });
  };

  const confirmModel = async (model: AuditModelId) => {
    if (!pendingCheckTypes) return;
    setStartingCheck(true);
    try {
      await startCheckWithModel(pendingCheckTypes, model);
      setPendingCheckTypes(null);
    } finally {
      setStartingCheck(false);
    }
  };

  const handleSaveVersion = async () => {
    if (!question || isHistorical) return;
    try {
      const values = await form.validateFields();
      setSaving(true);
      const response = await fetch(`/api/questions/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        // 标题和专家 ID 不是题目编辑项；显式保留，避免后端的完整更新契约将它们置空。
        body: JSON.stringify({ ...values, title: question.title, expertId: question.expertId, currentVersion: question.currentVersion }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "保存失败");
      setEditing(false);
      message.success(`已保存为 v${data.currentVersion}`);
      await mutate(data, false);
    } catch (error) {
      if (error instanceof Error) message.error(error.message);
    } finally {
      setSaving(false);
    }
  };

  if (!currentUser || isLoading) {
    return (
      <div style={{ display: "flex", justifyContent: "center", alignItems: "center", minHeight: "100vh" }}>
        <Spin size="large" />
      </div>
    );
  }

  if (!question) {
    return (
      <div style={{ padding: 40, textAlign: "center" }}>
        <Text type="danger">题目不存在或已被删除</Text>
        <br />
        <Link href="/"><Button style={{ marginTop: 16 }}>返回列表</Button></Link>
      </div>
    );
  }

  // 页面刷新时本地状态为空；直接由持久化的活动任务派生状态，直到 SSE 回放接管。
  const recoveredCheckingTypes = checkingTypes.size === 0 && Object.keys(checkProgress).length === 0
    ? new Set(activeRun?.checkTypes || [])
    : checkingTypes;
  const recoveredProgress = activeRun?.status === "queued" ? "排队中" : "检测中";
  const activeProgress = Array.from(recoveredCheckingTypes).map((type) => {
    const label = CHECK_TYPE_LABELS[type as CheckType] || type;
    return `${label}：${checkProgress[type] || recoveredProgress}`;
  });

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Header style={{ background: "#fff", borderBottom: "1px solid #f0f0f0", padding: "0 24px", display: "flex", alignItems: "center", gap: 12 }}>
        <span style={{ fontSize: 20 }}>🔬</span>
        <Title level={4} style={{ margin: 0 }}>AI 质检系统</Title>
        <Text type="secondary" style={{ fontSize: 13 }}>STEM 题目审核平台</Text>
      </Header>

      <Content style={{ padding: 24 }}>
        {modalContextHolder}
        {isHistorical && (
          <Alert
            type="info"
            title={`正在查看历史版本 v${question.currentVersion}`}
            description="历史版本为只读快照，可查看当时的题目和质检结果，不能编辑或发起质检。"
            style={{ marginBottom: 16 }}
          />
        )}
        <Breadcrumb
          items={[
            { title: <Link href="/">题目列表</Link> },
            { title: isHistorical ? `历史版本 v${question.currentVersion}` : question.title || `题目 #${question.id}` },
          ]}
          style={{ marginBottom: 16 }}
        />

        <Row gutter={24}>
          {/* 左侧：题目内容 */}
          <Col span={14}>
            <Card
              title={
                <Space>
                  <Link href="/"><ArrowLeftOutlined /></Link>
                  <Text strong style={{ fontSize: 16 }}>{question.title || `题目 #${question.id}`}</Text>
                  <Tag color="blue">v{question.currentVersion}</Tag>
                </Space>
              }
              extra={
                <Space wrap>
                  <Tag color="blue">{question.type}</Tag>
                  {question.difficulty
                    .split(/[、,，]/)
                    .map((s) => s.trim())
                    .filter(Boolean)
                    .map((t) => (
                      <Tag
                        key={t}
                        color={t.includes("博士") ? "red" : t.includes("大学") || t.includes("本科") ? "orange" : "blue"}
                      >
                        {t}
                      </Tag>
                    ))}
                  {question.expertId && <Tag>{question.expertId}</Tag>}
                  {!isHistorical && (
                    <Button size="small" icon={<EditOutlined />} onClick={() => setEditing(true)}>
                      编辑
                    </Button>
                  )}
                </Space>
              }
            >
              <Space wrap style={{ marginBottom: 12 }}>
                <Text type="secondary">领域：</Text><Text>{question.domain || "-"}</Text>
                <Divider orientation="vertical" />
                <Text type="secondary">知识点：</Text><Text>{question.knowledgePoints || "-"}</Text>
              </Space>

              <Divider style={{ margin: "12px 0" }} />

              <div>
                <Space style={{ width: "100%", justifyContent: "space-between" }} align="center">
                  <Text strong style={{ fontSize: 15 }}>题目</Text>
                  <Button
                    size="small"
                    style={{ width: 64 }}
                    icon={<CopyOutlined />}
                    onClick={() => handleCopy(question.question, "题目")}
                  >复制</Button>
                </Space>
                <div style={{ background: "#fafafa", border: "1px solid #f0f0f0", borderRadius: 6, padding: 16, marginTop: 8 }}>
                  <LatexRenderer content={question.question} />
                </div>
              </div>

              <Collapse
                style={{ marginTop: 16 }}
                defaultActiveKey={["answer", "solution"]}
                items={[
                  {
                    key: "answer",
                    label: (
                      <Space style={{ width: "100%", justifyContent: "space-between" }} align="center">
                        <span>参考答案</span>
                        <Button
                          size="small"
                          style={{ width: 64 }}
                          icon={<CopyOutlined />}
                          onClick={(e) => { e.stopPropagation(); handleCopy(question.answer, "参考答案"); }}
                        >复制</Button>
                      </Space>
                    ),
                    children: (
                      <div style={{ background: "#f6ffed", border: "1px solid #b7eb8f", borderRadius: 6, padding: 12 }}>
                        <LatexRenderer content={question.answer} />
                      </div>
                    ),
                  },
                  {
                    key: "solution",
                    label: (
                      <Space style={{ width: "100%", justifyContent: "space-between" }} align="center">
                        <span>解题思路</span>
                        <Button
                          size="small"
                          style={{ width: 64 }}
                          icon={<CopyOutlined />}
                          onClick={(e) => { e.stopPropagation(); handleCopy(question.solution, "解题思路"); }}
                        >复制</Button>
                      </Space>
                    ),
                    children: (
                      <div style={{ background: "#e6f7ff", border: "1px solid #91d5ff", borderRadius: 6, padding: 12 }}>
                        <LatexRenderer content={question.solution} />
                      </div>
                    ),
                  },
                ]}
              />
            </Card>
          </Col>

          {/* 右侧：质检结果 */}
          <Col span={10}>
            <Card
              title={
                <Space>
                  <Text strong>质检结果</Text>
                  <Badge
                    count={question.checkResults?.filter((r) => r.result === "fail").length || 0}
                    style={{ backgroundColor: "#ff4d4f" }}
                  />
                </Space>
              }
              extra={!isHistorical ? (
                <Space>
                  <Button
                    type="primary"
                    size="small"
                    style={{ width: 94 }}
                    icon={<PlayCircleOutlined />}
                    loading={recoveredCheckingTypes.size > 0}
                    onClick={() => handleCheck(["latex", "difficulty", "answer", "synthesis"])}
                  >
                    全量质检
                  </Button>
                </Space>
              ) : null}
            >
              {!isHistorical && recoveredCheckingTypes.size > 0 && (
                <Alert
                  type="info"
                  title={`正在质检：${activeProgress.join(" · ") || recoveredProgress}`}
                  style={{ marginBottom: 12 }}
                />
              )}
              {!isHistorical && checkingTypes.size === 0 && checkDurations["__total"] && (
                <div style={{ marginBottom: 12, padding: "6px 12px", background: "#f6ffed", border: "1px solid #b7eb8f", borderRadius: 6, fontSize: 12 }}>
                  <Text type="secondary">总耗时：</Text>
                  <Text strong style={{ color: "#52c41a", marginRight: 16 }}>{(checkDurations["__total"] / 1000).toFixed(1)}s</Text>
                  {["latex", "difficulty", "answer", "synthesis"].map(t =>
                    checkDurations[t] !== undefined ? (
                      <span key={t} style={{ marginRight: 12 }}>
                        <Text type="secondary">{CHECK_TYPE_LABELS[t as CheckType]}：</Text>
                        <Text style={{ fontSize: 12 }}>{(checkDurations[t] / 1000).toFixed(1)}s</Text>
                      </span>
                    ) : null
                  )}
                </div>
              )}

              {["latex", "difficulty", "answer", "synthesis"].map((type) => {
                const cr = question.checkResults?.find((r) => r.checkType === type);
                const label = CHECK_TYPE_LABELS[type as CheckType];
                const isChecking = !isHistorical && recoveredCheckingTypes.has(type);

                if (!cr) {
                  return (
                    <Card
                      key={type}
                      size="small"
                      title={
                        <Space>
                          <ClockCircleOutlined style={{ color: "#999" }} />
                          <Text>{label}</Text>
                          {isChecking ? (
                            <Tag color="processing">
                              <SyncOutlined spin /> {checkProgress[type] || recoveredProgress}
                            </Tag>
                          ) : (
                            <Tag>待检测</Tag>
                          )}
                          {checkDurations[type] !== undefined && (
                            <Tag color="default" style={{ fontSize: 11 }}>{(checkDurations[type] / 1000).toFixed(1)}s</Tag>
                          )}
                        </Space>
                      }
                      extra={!isHistorical ? (
                        <Button size="small" style={{ width: 94 }} onClick={() => handleCheck([type])} disabled={isChecking}>
                          检测
                        </Button>
                      ) : null}
                      style={{ marginBottom: 12 }}
                      styles={{ body: { padding: "8px 16px" } }}
                    >
                      <Text type="secondary" style={{ fontSize: 12 }}>尚未运行此项质检</Text>
                    </Card>
                  );
                }

                return (
                  <CheckResultCard
                    key={type}
                    cr={cr}
                    onRecheck={() => handleCheck([type])}
                    duration={checkDurations[type]}
                    readOnly={isHistorical}
                  />
                );
              })}
            </Card>
          </Col>
        </Row>

        <ModelSelectionModal open={Boolean(pendingCheckTypes)} loading={startingCheck} onCancel={() => setPendingCheckTypes(null)} onConfirm={confirmModel} />

        <Modal
          title={`编辑题目 · 将保存为 v${(question.currentVersion || 1) + 1}`}
          open={editing && !isHistorical}
          width={920}
          okText="保存新版本"
          cancelText="取消"
          confirmLoading={saving}
          onCancel={() => setEditing(false)}
          onOk={() => void handleSaveVersion()}
          forceRender
        >
          <Form form={form} layout="vertical">
            <Row gutter={16}>
              <Col span={12}><Form.Item name="type" label="题目类型"><Input /></Form.Item></Col>
              <Col span={12}><Form.Item name="domain" label="领域"><Input /></Form.Item></Col>
              <Col span={12}><Form.Item name="difficulty" label="难度"><Input /></Form.Item></Col>
              <Col span={12}><Form.Item name="knowledgePoints" label="知识点"><Input /></Form.Item></Col>
              <Col span={12}><Form.Item name="subject" label="学科"><Input /></Form.Item></Col>
            </Row>
            <Form.Item name="question" label="题目" rules={[{ required: true, whitespace: true, message: "题目不能为空" }]}>
              <Input.TextArea rows={8} />
            </Form.Item>
            <Form.Item name="answer" label="参考答案"><Input.TextArea rows={4} /></Form.Item>
            <Form.Item name="solution" label="解题思路"><Input.TextArea rows={5} /></Form.Item>
            <Form.Item name="changeNote" label="修改说明" extra="可选；将随该版本一同保留。">
              <Input.TextArea rows={2} maxLength={1000} showCount />
            </Form.Item>
          </Form>
        </Modal>
      </Content>
    </Layout>
  );
}
