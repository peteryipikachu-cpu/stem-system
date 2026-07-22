"use client";
import React, { useState, useCallback, useEffect } from "react";
import {
  Layout,
  Table,
  Input,
  Select,
  Button,
  Tag,
  Space,
  Typography,
  message,
  Popconfirm,
  Modal,
  DatePicker,
  Spin,
} from "antd";
import {
  SearchOutlined,
  ImportOutlined,
  ExportOutlined,
  DeleteOutlined,
  EyeOutlined,
  PlayCircleOutlined,
  CopyOutlined,
  LogoutOutlined,
  TeamOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import Link from "next/link";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import AccountManagementModal from "@/components/AccountManagementModal";
import ImportModal from "@/components/ImportModal";
import ModelSelectionModal from "@/components/ModelSelectionModal";
import LatexRenderer from "@/components/LatexRenderer";
import { CHECK_TYPE_LABELS, CheckType } from "@/types";
import {
  getCheckBatch,
  startBatch,
  startCheck,
  subscribeCheckRun,
  type CheckBatchStatus,
  type CheckRunEvent,
} from "@/lib/check-runs";
import type { AuditModelId } from "@/lib/audit-models";

const { Header, Content } = Layout;
const { Title, Text } = Typography;
const { Option } = Select;

interface CheckResult {
  checkType: string;
  result: string;
  detail: string;
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
  expertId: string;
  status: string;
  checkResults: CheckResult[];
  createdAt: string;
  updatedAt: string;
  currentVersion: number;
  versionCount: number;
  versionCreatedAt: string;
  versionAuthor?: { id: number; username: string } | null;
  changeNote?: string | null;
  owner?: { id: number; username: string } | null;
}

interface QuestionVersionSummary {
  version: number;
  title: string;
  status: string;
  versionCreatedAt: string | null;
  savedAt: string | null;
  versionAuthor?: { id: number; username: string } | null;
  changeNote?: string | null;
  checkResultCount: number;
  checkSummary: string;
}

interface CurrentUser {
  id: number;
  username: string;
  role: "user" | "admin";
}

interface ApiResponse {
  total: number;
  items: Question[];
  page: number;
  pageSize: number;
}

const fetcher = async (url: string) => {
  const response = await fetch(url);
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "请求失败");
  return data;
};

function getCheckSummary(checkResults: CheckResult[]) {
  if (!checkResults || checkResults.length === 0) return null;
  const synthesisResult = checkResults.find((r) => r.checkType === "synthesis");

  const isSynthetic =
    synthesisResult?.result === "fail" || synthesisResult?.result === "warning";

  const hasFailure = checkResults.some((r) => r.result === "fail");
  const hasWarning = checkResults.some((r) => r.result === "warning");

  return { isSynthetic, hasFailure, hasWarning, synthesisResult };
}

/** 判断是否合格：latex/difficulty/answer/synthesis 全部 pass */
function isQualified(checkResults: CheckResult[]): boolean | null {
  if (!checkResults?.length) return null;
  const required = ["latex", "difficulty", "answer", "synthesis"];
  const hasAll = required.every((t) => checkResults.some((r) => r.checkType === t));
  if (!hasAll) return null; // 尚未全部运行
  return required.every((t) =>
    checkResults.find((r) => r.checkType === t)?.result === "pass"
  );
}

function historyCheckSummary(version: QuestionVersionSummary): string {
  return version.checkSummary || (version.checkResultCount === 0 ? "未质检" : "部分质检");
}

/** 日期格式化：YYYY-MM-DD HH:mm */
function formatDate(isoStr: string): string {
  if (!isoStr) return "";
  const d = new Date(isoStr);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function getJudgmentBasis(checkResults: CheckResult[]): string {
  if (!checkResults || checkResults.length === 0) return "";
  const parts: string[] = [];
  const requiredTypes: CheckType[] = ["latex", "difficulty", "answer", "synthesis"];

  for (const r of checkResults) {
    if (r.result === "fail" || r.result === "warning") {
      try {
        const detail = JSON.parse(r.detail || "{}");
        const label = CHECK_TYPE_LABELS[r.checkType as CheckType] || r.checkType;

        if (r.checkType === "synthesis" && detail.reasons) {
          const reasons = detail.reasons
            .filter((reason: { type: string; evidence: string }) => reason.evidence && reason.evidence !== "无此问题")
            .map((reason: { type: string; evidence: string }) => `【${reason.evidence}】`)
            .slice(0, 2)
            .join(" ");
          if (reasons) parts.push(`${label}: ${reasons}`);
        } else if (r.checkType === "latex" && detail.errors?.length > 0) {
          parts.push(`${label}: ${detail.errors[0].description}`);
        } else if (r.checkType === "difficulty") {
          parts.push(`${label}: ${String((detail.model as { label?: string } | undefined)?.label || "模型")} 答对${detail.correctCount}/${detail.totalCount}次（阈值≤${detail.threshold ?? "-"}次）`);
        } else if (detail.error) {
          parts.push(`${label}: ${detail.error}`);
        }
      } catch {
        parts.push(r.checkType);
      }
    }
  }
  if (parts.length > 0) return parts.join(" - ");

  const completedTypes = requiredTypes.filter((type) =>
    checkResults.some((result) => result.checkType === type)
  );
  const pendingTypes = requiredTypes.filter((type) => !completedTypes.includes(type));

  if (pendingTypes.length > 0) {
    const completedLabels = completedTypes.map((type) => CHECK_TYPE_LABELS[type]).join("、");
    const pendingLabels = pendingTypes.map((type) => CHECK_TYPE_LABELS[type]).join("、");
    return `${completedLabels ? `已完成：${completedLabels}；` : ""}待检测：${pendingLabels}`;
  }

  return requiredTypes.every(
    (type) => checkResults.find((result) => result.checkType === type)?.result === "pass"
  )
    ? "质检通过"
    : "质检结果待确认";
}

/** 表格仅展示稳定的文本摘要；完整公式在弹窗中使用 KaTeX 渲染。 */
function getQuestionPreview(question: string, maxLength = 160): string {
  const normalized = question
    .replace(/\\text\s*\{([^{}]*)\}/g, "$1")
    .replace(/\$\$|\$/g, "")
    .replace(/\\\[|\\\]|\\\(|\\\)/g, "")
    .replace(/\s+/g, " ")
    .trim();

  return normalized.length > maxLength ? `${normalized.slice(0, maxLength)}…` : normalized;
}

export default function HomePage() {
  const router = useRouter();
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [qualified, setQualified] = useState("");
  const [createdAtRange, setCreatedAtRange] = useState<[string, string] | null>(null);
  const [updatedAtRange, setUpdatedAtRange] = useState<[string, string] | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize] = useState(20);
  const [importVisible, setImportVisible] = useState(false);
  const [batchTotal, setBatchTotal] = useState(0);
  const [batchDone, setBatchDone] = useState(0);
  const [activeBatchId, setActiveBatchId] = useState<string | null>(null);
  const [checkingIds, setCheckingIds] = useState<Set<number>>(new Set());
  const [checkProgress, setCheckProgress] = useState<Record<number, string>>({});
  const [previewQuestion, setPreviewQuestion] = useState<Question | null>(null);
  const [previewBasis, setPreviewBasis] = useState<{ title: string; content: string } | null>(null);
  const [exporting, setExporting] = useState(false);
  const [accountManagementVisible, setAccountManagementVisible] = useState(false);
  const [versionHistory, setVersionHistory] = useState<Record<number, QuestionVersionSummary[]>>({});
  const [loadingVersionHistory, setLoadingVersionHistory] = useState<Set<number>>(new Set());
  const [modelRequest, setModelRequest] = useState<{ kind: "single"; id: number; checkTypes?: string[] } | { kind: "batch" } | null>(null);
  const [startingModelCheck, setStartingModelCheck] = useState(false);

  const { data: currentUser, error: authError } = useSWR<CurrentUser>("/api/auth/me", fetcher);
  useEffect(() => {
    if (authError) router.replace("/login");
  }, [authError, router]);

  const params = new URLSearchParams({
    page: String(page),
    pageSize: String(pageSize),
    ...(search ? { search } : {}),
    ...(status ? { status } : {}),
    ...(qualified ? { qualified } : {}),
    ...(createdAtRange ? { createdAtFrom: createdAtRange[0], createdAtTo: createdAtRange[1] } : {}),
    ...(updatedAtRange ? { updatedAtFrom: updatedAtRange[0], updatedAtTo: updatedAtRange[1] } : {}),
  });

  const { data, mutate, isLoading } = useSWR<ApiResponse>(
    `/api/questions?${params}`,
    fetcher,
    { refreshInterval: 0 }
  );
  const { data: activeBatch } = useSWR<CheckBatchStatus>(
    activeBatchId ? `/api/check-batches/${activeBatchId}` : null,
    () => getCheckBatch(activeBatchId as string),
    { refreshInterval: activeBatchId ? 2500 : 0 },
  );

  const handleLogout = async () => {
    await fetch("/api/auth/logout", { method: "POST" });
    router.replace("/login");
  };

  const handleSearch = useCallback(() => {
    setPage(1);
    mutate();
  }, [mutate]);

  const handleDelete = async (id: number) => {
    try {
      await fetch(`/api/questions/${id}`, { method: "DELETE" });
      message.success("删除成功");
      mutate();
    } catch {
      message.error("删除失败");
    }
  };

  const loadVersionHistory = async (questionId: number) => {
    if (versionHistory[questionId] || loadingVersionHistory.has(questionId)) return;
    setLoadingVersionHistory((previous) => new Set(previous).add(questionId));
    try {
      const response = await fetch(`/api/questions/${questionId}/versions`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "获取历史版本失败");
      setVersionHistory((previous) => ({ ...previous, [questionId]: data.items || [] }));
    } catch (error) {
      message.error(error instanceof Error ? error.message : "获取历史版本失败");
    } finally {
      setLoadingVersionHistory((previous) => {
        const next = new Set(previous);
        next.delete(questionId);
        return next;
      });
    }
  };

  const watchRun = (runId: string, id: number) => {
    let close = () => {};
    close = subscribeCheckRun(runId, (event: CheckRunEvent) => {
      if (event.type === "progress") {
        const label = CHECK_TYPE_LABELS[event.checkType as CheckType] || event.checkType || "质检";
        if (event.status === "done") setCheckProgress((prev) => ({ ...prev, [id]: `${label} 完成` }));
        else if (event.status === "error") setCheckProgress((prev) => ({ ...prev, [id]: `${label} 失败: ${event.message || "未知错误"}` }));
        else setCheckProgress((prev) => ({ ...prev, [id]: `正在检测: ${label}...` }));
      }
      if (event.type === "complete") {
        close();
        setCheckProgress((prev) => ({ ...prev, [id]: "质检完成" }));
        setBatchDone((prev) => prev + 1);
        setCheckingIds((prev) => { const next = new Set(prev); next.delete(id); return next; });
        mutate();
      }
    });
  };

  const startSingleCheck = async (id: number, checkTypes: string[] | undefined, model: AuditModelId) => {
    setCheckingIds((prev) => new Set([...prev, id]));
    setCheckProgress((prev) => ({ ...prev, [id]: "启动质检..." }));

    try {
      const accepted = await startCheck(id, checkTypes || ["latex", "difficulty", "answer", "synthesis"], model);
      watchRun(accepted.checkRunId, id);
    } catch (err) {
      message.error("质检启动失败: " + (err instanceof Error ? err.message : "Unknown error"));
      setCheckingIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }
  };

  const openSingleModelSelection = (id: number, checkTypes?: string[]) => {
    setModelRequest({ kind: "single", id, checkTypes });
  };

  const handleCheck = (question: Question, checkTypes?: string[]) => {
    if (!question.checkResults?.length) {
      openSingleModelSelection(question.id, checkTypes);
      return;
    }

    Modal.confirm({
      title: "已有结果",
      content: "当前题目已有质检结果，是否重新检测？",
      okText: "重新检测",
      cancelText: "取消",
      onOk: () => openSingleModelSelection(question.id, checkTypes),
    });
  };

  const handleExport = async () => {
    setExporting(true);
    try {
      const exportParams = new URLSearchParams(params);
      exportParams.set("all", "true");
      const res = await fetch(`/api/questions?${exportParams}`);
      const { items } = await res.json() as { items: Question[] };

      const headers = [
        "ID", "题目类型", "领域", "难度", "知识点", "题目", "答案",
        "是否合格",
        "LaTeX格式结果", "LaTeX格式详情",
        "难度校验结果", "难度校验详情",
        "答案校验结果", "答案校验详情",
        "AI合成题结果", "AI合成题详情",
        "创建时间", "修改时间",
      ];

      const getResult = (q: Question, type: string) =>
        q.checkResults.find((r) => r.checkType === type)?.result ?? "";

      /** 提取每项质检的详情描述（类似判断依据列） */
      const getDetail = (q: Question, type: string): string => {
        const cr = q.checkResults.find((r) => r.checkType === type);
        if (!cr) return "";
        try {
          const d = JSON.parse(cr.detail || "{}");
          if (type === "latex") {
            if (!d.errors?.length) return "无错误";
            return d.errors.map((e: { description: string }) => e.description).join(";");
          }
          if (type === "difficulty") {
            return `${d.model?.label || "模型"} 答对${d.correctCount ?? "-"}/${d.totalCount ?? "-"}次（阈值≤${d.threshold ?? "-"}次）`;
          }
          if (type === "answer") {
            return `${d.model?.label || "模型"} 答对${d.correctCount ?? "-"}/${d.totalCount ?? "-"}次`;
          }
          if (type === "synthesis") {
            if (!d.reasons?.length) return d.isSynthetic ? "疑似合成" : "未发现问题";
            const hits = d.reasons
              .filter((r: { evidence: string }) => r.evidence && r.evidence !== "无此问题")
              .map((r: { evidence: string }) => r.evidence)
              .join("; ");
            return hits || (d.isSynthetic ? "疑似合成" : "未发现问题");
          }
          return "";
        } catch {
          return "";
        }
      };

      const escapeCell = (s: string | number) =>
        typeof s === "string" && (s.includes(",") || s.includes("\"") || s.includes("\n"))
          ? `"${s.replace(/"/g, "\"\"")}"`
          : String(s);

      const rows = items.map((q) => {
        const qual = isQualified(q.checkResults);
        return [
          q.id, q.type, q.domain, q.difficulty, q.knowledgePoints,
          escapeCell(q.question), escapeCell(q.answer),
          qual === true ? "合格" : qual === false ? "不合格" : "未检测",
          getResult(q, "latex"),   escapeCell(getDetail(q, "latex")),
          getResult(q, "difficulty"), escapeCell(getDetail(q, "difficulty")),
          getResult(q, "answer"),    escapeCell(getDetail(q, "answer")),
          getResult(q, "synthesis"), escapeCell(getDetail(q, "synthesis")),
          formatDate(q.createdAt), formatDate(q.updatedAt),
        ].join(",");
      });

      const csv = [headers.join(","), ...rows].join("\n");
      const blob = new Blob(["\uFEFF" + csv], { type: "text/csv;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `题目列表_${new Date().toISOString().slice(0, 10)}.csv`;
      a.click();
      URL.revokeObjectURL(url);
      message.success(`导出 ${items.length} 条记录`);
    } catch {
      message.error("导出失败");
    } finally {
      setExporting(false);
    }
  };

  const startBatchCheck = async (model: AuditModelId) => {
    const pendingIds = (data?.items || [])
      .filter((q) => q.status === "pending" && !checkingIds.has(q.id))
      .map((q) => q.id);

    if (pendingIds.length === 0) {
      message.info("没有待检测的题目");
      return;
    }

    message.info(`已提交 ${pendingIds.length} 道题目的后台质检任务`);
    setBatchTotal(pendingIds.length);
    setBatchDone(0);
    setCheckingIds(new Set(pendingIds));
    try {
      const batch = await startBatch(pendingIds, ["latex", "difficulty", "answer", "synthesis"], model);
      setActiveBatchId(batch.batchId);
      batch.runIds.forEach((runId, index) => watchRun(runId, pendingIds[index]));
    } catch (error) {
      setCheckingIds(new Set());
      message.error("批量质检启动失败: " + (error instanceof Error ? error.message : "Unknown error"));
    }
  };

  const openBatchModelSelection = () => setModelRequest({ kind: "batch" });

  const handleBatchCheck = () => {
    const pendingQuestions = (data?.items || [])
      .filter((question) => question.status === "pending" && !checkingIds.has(question.id));

    if (pendingQuestions.length === 0) {
      message.info("没有待检测的题目");
      return;
    }

    const existingResultCount = pendingQuestions.filter((question) => question.checkResults?.length).length;
    if (existingResultCount === 0) {
      openBatchModelSelection();
      return;
    }

    Modal.confirm({
      title: "已有结果",
      content: `本次待检测题目中有 ${existingResultCount} 道已有质检结果，是否重新检测？`,
      okText: "重新检测",
      cancelText: "取消",
      onOk: openBatchModelSelection,
    });
  };

  const confirmModel = async (model: AuditModelId) => {
    if (!modelRequest) return;
    setStartingModelCheck(true);
    try {
      if (modelRequest.kind === "single") await startSingleCheck(modelRequest.id, modelRequest.checkTypes, model);
      else await startBatchCheck(model);
      setModelRequest(null);
    } finally {
      setStartingModelCheck(false);
    }
  };

  const columns: ColumnsType<Question> = [
    {
      title: "序号",
      width: 60,
      render: (_, __, index) => (page - 1) * pageSize + index + 1,
      align: "center",
    },
    {
      title: "版本",
      width: 70,
      align: "center",
      render: (_: unknown, record: Question) => <Tag color="blue">v{record.currentVersion || 1}</Tag>,
    },
    {
      title: "题目类型",
      dataIndex: "type",
      width: 90,
    },
    {
      title: "领域",
      dataIndex: "domain",
      width: 130,
      render: (val) => <Text style={{ fontSize: 12 }}>{val}</Text>,
    },
    {
      title: "难度",
      dataIndex: "difficulty",
      width: 160,
      render: (val: string) => {
        if (!val) return <span>-</span>;
        const tags = val.split(/[、,，]/).map((s) => s.trim()).filter(Boolean);
        const getColor = (t: string) =>
          t.includes("博士") ? "red" : t.includes("大学") || t.includes("本科") ? "orange" : "blue";
        return (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
            {tags.map((t) => (
              <Tag key={t} color={getColor(t)} style={{ margin: 0, fontSize: 11 }}>{t}</Tag>
            ))}
          </div>
        );
      },
    },
    {
      title: "知识点",
      dataIndex: "knowledgePoints",
      width: 130,
      ellipsis: true,
      render: (val) => <Text style={{ fontSize: 12 }} ellipsis>{val}</Text>,
    },
    {
      title: "问题",
      dataIndex: "question",
      width: 320,
      className: "question-column",
      render: (val: string, record: Question) => {
        if (!val) return null;
        return (
          <div className="question-preview">
            <div className="question-preview__text">{getQuestionPreview(val)}</div>
            <Button
              className="question-preview__action"
              type="link"
              size="small"
              onClick={() => setPreviewQuestion(record)}
            >
              查看全文
            </Button>
          </div>
        );
      },
    },
    {
      title: "是否疑似 AI 生成",
      width: 100,
      align: "center" as const,
      render: (_: unknown, record: Question) => {
        if (record.status === "pending") return <Tag>待检测</Tag>;
        if (record.status === "checking") {
          const prog = checkProgress[record.id];
          return <Tag color="processing">{prog || "检测中..."}  </Tag>;
        }
        const summary = getCheckSummary(record.checkResults);
        if (!summary) return <Tag>未检测</Tag>;
        if (summary.isSynthetic) return <Tag color="red">是</Tag>;
        if (summary.hasWarning) return <Tag color="orange">疑似</Tag>;
        return <Tag color="green">否</Tag>;
      },
    },
    {
      title: "是否合格",
      width: 90,
      align: "center" as const,
      render: (_: unknown, record: Question) => {
        if (record.status !== "done") return <Tag>未检测</Tag>;
        const qual = isQualified(record.checkResults);
        if (qual === null) return <Tag>未检测</Tag>;
        return qual
          ? <Tag color="green">合格</Tag>
          : <Tag color="red">不合格</Tag>;
      },
    },
    {
      title: "判断依据",
      width: 220,
      render: (_, record) => {
        if (!record.checkResults?.length) return null;
        const basis = getJudgmentBasis(record.checkResults);
        return (
          <div className="judgment-basis">
            <div className="judgment-basis__text">{basis}</div>
            <Space size={4} wrap>
              <Button
                type="link"
                size="small"
                onClick={() => setPreviewBasis({ title: record.title || `题目 #${record.id}`, content: basis })}
              >
                查看全部
              </Button>
              <Button
                size="small"
                icon={<CopyOutlined />}
                onClick={() => {
                  navigator.clipboard.writeText(basis);
                  message.success("已复制");
                }}
              >
                复制
              </Button>
            </Space>
          </div>
        );
      },
    },
    {
      title: "创建日期",
      dataIndex: "createdAt",
      width: 130,
      render: (val: string) => <Text style={{ fontSize: 12 }}>{formatDate(val)}</Text>,
    },
    ...(currentUser?.role === "admin" ? [{
      title: "提交用户",
      width: 110,
      render: (_: unknown, record: Question) => <Text style={{ fontSize: 12 }}>{record.owner?.username || "历史题目"}</Text>,
    }] : []),
    {
      title: "修改日期",
      dataIndex: "updatedAt",
      width: 130,
      render: (val: string) => <Text style={{ fontSize: 12 }}>{formatDate(val)}</Text>,
    },
    {
      title: "操作",
      width: 260,
      fixed: "right" as const,
      render: (_: unknown, record: Question) => (
        <Space size={8} wrap={false} style={{ whiteSpace: "nowrap" }}>
          <Link href={`/questions/${record.id}`}>
            <Button size="small" icon={<EyeOutlined />}>详情</Button>
          </Link>
          <Button
            size="small"
            type="primary"
            icon={<PlayCircleOutlined />}
            loading={checkingIds.has(record.id)}
            onClick={() => handleCheck(record)}
          >
            质检
          </Button>
          <Popconfirm
            title="确认删除？"
            description="删除后将无法恢复该题目及其审核结果。"
            okText="确认"
            cancelText="取消"
            onConfirm={() => handleDelete(record.id)}
          >
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const checkingCount = checkingIds.size;

  if (!currentUser) {
    return <div style={{ minHeight: "100vh", display: "grid", placeItems: "center", color: "#666" }}>正在验证登录状态…</div>;
  }

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Header
        style={{
          background: "#fff",
          borderBottom: "1px solid #f0f0f0",
          display: "flex",
          alignItems: "center",
          padding: "0 24px",
          gap: 12,
        }}
      >
        <span style={{ fontSize: 20 }}>🔬</span>
        <Title level={4} style={{ margin: 0, color: "#1a1a1a" }}>
          AI 质检系统
        </Title>
        <Text type="secondary" style={{ fontSize: 13 }}>STEM 题目审核平台</Text>
        <div style={{ flex: 1 }} />
        <Tag color={currentUser.role === "admin" ? "purple" : "blue"}>{currentUser.role === "admin" ? "管理员" : "普通用户"}</Tag>
        <Text>{currentUser.username}</Text>
        {currentUser.role === "admin" && (
          <Button size="small" icon={<TeamOutlined />} onClick={() => setAccountManagementVisible(true)}>账号管理</Button>
        )}
        <Button size="small" icon={<LogoutOutlined />} onClick={handleLogout}>退出</Button>
      </Header>

      <Content style={{ padding: "24px", maxWidth: 1600, margin: "0 auto", width: "100%" }}>
        {/* 搜索栏 */}
        <div style={{ background: "#fff", borderRadius: 8, padding: "16px 20px", marginBottom: 16 }}>
          <Space wrap style={{ width: "100%" }}>
            <Input
              placeholder="搜索题目/领域..."
              prefix={<SearchOutlined />}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              onPressEnter={handleSearch}
              style={{ width: 300 }}
              allowClear
            />
            <Select
              placeholder="全部状态"
              value={status || undefined}
              onChange={setStatus}
              style={{ width: 120 }}
              allowClear
            >
              <Option value="pending">待检测</Option>
              <Option value="checking">检测中</Option>
              <Option value="done">已完成</Option>
            </Select>
            <Select
              placeholder="是否合格"
              value={qualified || undefined}
              onChange={(v) => setQualified(v ?? "")}
              style={{ width: 120 }}
              allowClear
            >
              <Option value="true">合格</Option>
              <Option value="false">不合格</Option>
            </Select>
            <DatePicker.RangePicker
              placeholder={["创建日期起", "创建日期止"]}
              style={{ width: 240 }}
              onChange={(_, strs) => {
                if (strs[0] && strs[1]) setCreatedAtRange([strs[0], strs[1]]);
                else setCreatedAtRange(null);
              }}
            />
            <DatePicker.RangePicker
              placeholder={["修改日期起", "修改日期止"]}
              style={{ width: 240 }}
              onChange={(_, strs) => {
                if (strs[0] && strs[1]) setUpdatedAtRange([strs[0], strs[1]]);
                else setUpdatedAtRange(null);
              }}
            />
            <Button type="primary" icon={<SearchOutlined />} onClick={handleSearch}>搜索</Button>

            <div style={{ flex: 1 }} />

            <Button
              icon={<ImportOutlined />}
              onClick={() => setImportVisible(true)}
            >
              导入
            </Button>
            <Button
              icon={<PlayCircleOutlined />}
              type="primary"
              ghost
              loading={checkingCount > 0}
              onClick={handleBatchCheck}
            >
              {checkingCount > 0 ? `质检中 (${checkingCount})` : "批量质检"}
            </Button>
            <Button icon={<ExportOutlined />} loading={exporting} onClick={handleExport}>导出</Button>
          </Space>
        </div>

        <ModelSelectionModal open={Boolean(modelRequest)} loading={startingModelCheck} onCancel={() => setModelRequest(null)} onConfirm={confirmModel} />

        {/* 进度提示 */}
        {(checkingCount > 0 || activeBatch) && (
          <div style={{ marginBottom: 12, background: "#e6f7ff", border: "1px solid #91d5ff", borderRadius: 6, padding: "8px 16px", display: "flex", alignItems: "center", gap: 16 }}>
            <Text style={{ color: "#1890ff" }}>
              {activeBatch
                ? `批量质检：排队 ${activeBatch.queuedCount}，运行 ${activeBatch.runningCount}，完成 ${activeBatch.completedCount}/${activeBatch.totalCount}，人工复核 ${activeBatch.manualReviewCount}`
                : `正在并发质检，当前运行中 ${checkingCount} 道${batchTotal > 0 ? `（批量进度 ${batchDone} / ${batchTotal} 完成）` : ""}`}
              {activeBatch?.deadlineAt && `（截止 ${formatDate(activeBatch.deadlineAt)}）`}
            </Text>
            {(activeBatch || batchTotal > 0) && (
              <div style={{ flex: 1, maxWidth: 300 }}>
                <div style={{ height: 6, background: "#bae7ff", borderRadius: 3, overflow: "hidden" }}>
                  <div
                    style={{
                      height: "100%",
                      width: `${Math.round(((activeBatch?.completedCount ?? batchDone) / (activeBatch?.totalCount ?? batchTotal)) * 100)}%`,
                      background: "#1890ff",
                      borderRadius: 3,
                      transition: "width 0.4s ease",
                    }}
                  />
                </div>
              </div>
            )}
          </div>
        )}

        {/* 统计摘要 */}
        {data && (
          <div style={{ marginBottom: 16, display: "flex", gap: 16 }}>
            {[
              { label: "全部题目", value: data.total, color: "#1890ff" },
              {
                label: "待检测",
                value: data.items.filter((q) => q.status === "pending").length,
                color: "#faad14",
              },
              {
                label: "疑似合成",
                value: data.items.filter((q) => {
                  const s = getCheckSummary(q.checkResults);
                  return s?.hasFailure || s?.isSynthetic;
                }).length,
                color: "#ff4d4f",
              },
              {
                label: "合格",
                value: data.items.filter((q) => isQualified(q.checkResults) === true).length,
                color: "#52c41a",
              },
            ].map((stat) => (
              <div
                key={stat.label}
                style={{
                  background: "#fff",
                  borderRadius: 8,
                  padding: "12px 20px",
                  flex: 1,
                  textAlign: "center",
                  border: `1px solid ${stat.color}20`,
                }}
              >
                <div style={{ fontSize: 22, fontWeight: 700, color: stat.color }}>{stat.value}</div>
                <div style={{ fontSize: 12, color: "#666", marginTop: 2 }}>{stat.label}</div>
              </div>
            ))}
          </div>
        )}

        {/* 表格 */}
        <div style={{ background: "#fff", borderRadius: 8 }}>
          <Table
            columns={columns}
            dataSource={data?.items}
            rowKey="id"
            loading={isLoading}
            scroll={{ x: 1800 }}
            expandable={{
              rowExpandable: (record) => record.versionCount > 1,
              onExpand: (expanded, record) => {
                if (expanded) void loadVersionHistory(record.id);
              },
              expandedRowRender: (record) => {
                if (loadingVersionHistory.has(record.id)) return <Spin size="small" />;
                const versions = versionHistory[record.id] || [];
                return (
                  <Table<QuestionVersionSummary>
                    size="small"
                    rowKey="version"
                    pagination={false}
                    dataSource={versions}
                    columns={[
                      { title: "历史版本", width: 100, render: (_, item) => <Tag>v{item.version}</Tag> },
                      { title: "保存时间", width: 180, render: (_, item) => item.savedAt ? formatDate(item.savedAt) : "-" },
                      { title: "修改人", width: 120, render: (_, item) => item.versionAuthor?.username || "历史用户" },
                      { title: "修改说明", dataIndex: "changeNote", render: (note) => note || "-" },
                      { title: "质检状态", width: 120, render: (_, item) => <Tag>{historyCheckSummary(item)}</Tag> },
                      {
                        title: "操作",
                        width: 110,
                        render: (_, item) => (
                          <Link href={`/questions/${record.id}?version=${item.version}`}>
                            <Button size="small" icon={<EyeOutlined />}>查看详情</Button>
                          </Link>
                        ),
                      },
                    ]}
                  />
                );
              },
            }}
            pagination={{
              total: data?.total,
              current: page,
              pageSize,
              onChange: (p) => setPage(p),
              showTotal: (total) => `共 ${total} 条`,
              showSizeChanger: false,
            }}
            size="middle"
          />
        </div>

        <Modal
          title={previewQuestion?.title || "题目全文"}
          open={Boolean(previewQuestion)}
          footer={null}
          width={760}
          onCancel={() => setPreviewQuestion(null)}
        >
          <div className="question-preview-modal">
            <LatexRenderer content={previewQuestion?.question || ""} />
          </div>
        </Modal>

        <Modal
          title={previewBasis ? `${previewBasis.title} · 判断依据` : "判断依据"}
          open={Boolean(previewBasis)}
          footer={null}
          width={640}
          onCancel={() => setPreviewBasis(null)}
        >
          <div className="judgment-basis-modal">{previewBasis?.content}</div>
        </Modal>
      </Content>

      <ImportModal
        visible={importVisible}
        onClose={() => setImportVisible(false)}
        onSuccess={() => {
          setImportVisible(false);
          mutate();
        }}
      />
      <AccountManagementModal open={accountManagementVisible} onClose={() => setAccountManagementVisible(false)} />
    </Layout>
  );
}
