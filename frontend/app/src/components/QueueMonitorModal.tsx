"use client";

import { useState } from "react";
import useSWR from "swr";
import Link from "next/link";
import { Alert, Button, Col, message, Modal, Row, Select, Space, Statistic, Table, Tag, Typography } from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import { CHECK_TYPE_LABELS, type CheckType } from "@/types";
import { auditModelLabel } from "@/lib/audit-models";

const { Text } = Typography;

type QueueStatus = "active" | "all" | "queued" | "running" | "blocked" | "manual_review" | "manual_review_completed" | "manual_review_archived";
type QueueHealth = "all" | "normal" | "attention" | "stuck";

interface QueueUser {
  id: number;
  username: string;
}

interface QueueWorkItem {
  id: string;
  checkType: CheckType;
  stage: string;
  provider: string;
  status: string;
  attemptNo: number;
  createdAt: string | null;
  updatedAt: string | null;
  availableAt: string | null;
  startedAt: string | null;
  completedAt: string | null;
  leaseOwner: string | null;
  leaseExpiresAt: string | null;
  error: string | null;
  errorCode: string | null;
  errorStatusCode: number | null;
}

interface QueueRun {
  id: string;
  question: { id: number; title: string };
  requestedBy: QueueUser | null;
  questionOwner: QueueUser | null;
  status: string;
  priority: string;
  checkTypes: CheckType[];
  model: { id?: string; label?: string };
  createdAt: string | null;
  startedAt: string | null;
  diagnosis: { health: Exclude<QueueHealth, "all">; label: string; reason: string };
  workSummary: Record<string, number>;
  workItems: QueueWorkItem[];
}

interface QueueResponse {
  generatedAt: string;
  summary: {
    workerOnline: boolean;
    workers: { id: string; seenAt: string }[];
    queuedCount: number;
    runningCount: number;
    blockedCount: number;
    manualReviewCount: number;
    attentionCount: number;
    stuckCount: number;
    oldestReadyWaitSeconds: number;
  };
  total: number;
  items: QueueRun[];
  page: number;
  pageSize: number;
}

interface QueueMonitorModalProps {
  open: boolean;
  onClose: () => void;
}

async function fetcher(url: string): Promise<QueueResponse> {
  const response = await fetch(url);
  const text = await response.text();
  let data: { detail?: string } | QueueResponse = {};
  try { data = text ? JSON.parse(text) : {}; } catch { /* 保留服务端非 JSON 错误文本 */ }
  if (!response.ok) throw new Error(("detail" in data && data.detail) || text || "无法读取队列监控数据");
  return data as QueueResponse;
}

async function resolveManualReview(runId: string, action: "completed" | "archived"): Promise<void> {
  const response = await fetch(`/api/admin/queue/check-runs/${runId}/manual-review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action }),
  });
  const text = await response.text();
  let data: { detail?: string } = {};
  try { data = text ? JSON.parse(text) : {}; } catch { /* 保留服务端非 JSON 错误文本 */ }
  if (!response.ok) throw new Error(data.detail || text || "人工复核状态更新失败");
}

function formatDate(value: string | null): string {
  if (!value) return "-";
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

function formatDuration(seconds: number): string {
  if (!seconds) return "0 秒";
  if (seconds < 60) return `${seconds} 秒`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟`;
  return `${Math.floor(seconds / 3600)} 小时 ${Math.floor((seconds % 3600) / 60)} 分钟`;
}

function healthTag(health: QueueHealth, label: string) {
  const color = health === "stuck" ? "error" : health === "attention" ? "warning" : "success";
  return <Tag color={color}>{label}</Tag>;
}

function workStatusTag(status: string) {
  const color = status === "running" ? "processing" : status === "queued" ? "blue" : status === "blocked" ? "default" : status === "manual_review" ? "warning" : status === "manual_review_archived" ? "default" : "success";
  const label: Record<string, string> = {
    queued: "排队中", running: "执行中", blocked: "等待依赖", manual_review: "待人工复核",
    manual_review_completed: "人工复核已完成", manual_review_archived: "人工复核已归档",
    completed: "已完成", cancelled: "已取消",
  };
  return <Tag color={color}>{label[status] || status}</Tag>;
}

export default function QueueMonitorModal({ open, onClose }: QueueMonitorModalProps) {
  const [modal, modalContextHolder] = Modal.useModal();
  const [messageApi, messageContextHolder] = message.useMessage();
  const [status, setStatus] = useState<QueueStatus>("active");
  const [health, setHealth] = useState<QueueHealth>("all");
  const [provider, setProvider] = useState<string>();
  const [page, setPage] = useState(1);
  const [resolvingRunId, setResolvingRunId] = useState<string>();
  const params = new URLSearchParams({ status, health, page: String(page), pageSize: "20" });
  if (provider) params.set("provider", provider);
  const { data, error, isLoading, mutate } = useSWR<QueueResponse>(
    open ? `/api/admin/queue?${params.toString()}` : null,
    fetcher,
    { refreshInterval: open ? 5000 : 0, revalidateOnFocus: true },
  );

  const requestManualReviewResolution = (run: QueueRun, action: "completed" | "archived") => {
    const label = action === "completed" ? "完成人工复核" : "归档人工复核";
    modal.confirm({
      title: label,
      content: action === "completed"
        ? "将该质检任务的待人工复核工作项标记为已完成，并从当前待处理统计中移除。不会更改题目的质检结论。"
        : "将该质检任务的待人工复核工作项归档，并从当前待处理统计中移除。不会更改题目的质检结论。",
      okText: label,
      cancelText: "取消",
      onOk: async () => {
        setResolvingRunId(run.id);
        try {
          await resolveManualReview(run.id, action);
          messageApi.success(`${label}成功`);
          await mutate();
        } catch (requestError) {
          messageApi.error(requestError instanceof Error ? requestError.message : `${label}失败`);
          throw requestError;
        } finally {
          setResolvingRunId(undefined);
        }
      },
    });
  };

  const columns: ColumnsType<QueueRun> = [
    {
      title: "题目", width: 210,
      render: (_, run) => <Link href={`/questions/${run.question.id}`}>{run.question.title || `题目 #${run.question.id}`}</Link>,
    },
    { title: "质检发起人", width: 120, render: (_, run) => run.requestedBy?.username || <Text type="secondary">历史未知</Text> },
    { title: "题目提交人", width: 120, render: (_, run) => run.questionOwner?.username || <Text type="secondary">历史题目</Text> },
    { title: "模型", width: 135, render: (_, run) => auditModelLabel(run.model) },
    { title: "检查项", width: 190, render: (_, run) => run.checkTypes.map((type) => CHECK_TYPE_LABELS[type] || type).join("、") },
    { title: "创建时间", width: 175, render: (_, run) => formatDate(run.createdAt) },
    {
      title: "进度", width: 135,
      render: (_, run) => <Space size={3} wrap>{Object.entries(run.workSummary).map(([name, count]) => <Tag key={name}>{name}: {count}</Tag>)}</Space>,
    },
    {
      title: "诊断", width: 220,
      render: (_, run) => <span>{healthTag(run.diagnosis.health, run.diagnosis.label)}<Text type="secondary">{run.diagnosis.reason}</Text></span>,
    },
    {
      title: "操作", width: 200,
      render: (_, run) => (run.workSummary.manual_review || 0) > 0 ? (
        <Space size={6}>
          <Button size="small" type="primary" loading={resolvingRunId === run.id} onClick={() => requestManualReviewResolution(run, "completed")}>完成复核</Button>
          <Button size="small" disabled={resolvingRunId === run.id} onClick={() => requestManualReviewResolution(run, "archived")}>归档</Button>
        </Space>
      ) : "-",
    },
  ];

  const workColumns: ColumnsType<QueueWorkItem> = [
    { title: "检查项", dataIndex: "checkType", width: 120, render: (type: CheckType) => CHECK_TYPE_LABELS[type] || type },
    { title: "阶段", dataIndex: "stage", width: 105 },
    { title: "Provider", dataIndex: "provider", width: 90 },
    { title: "状态", dataIndex: "status", width: 110, render: workStatusTag },
    { title: "重试", dataIndex: "attemptNo", width: 75, render: (value: number) => `${value} 次` },
    { title: "可执行时间", dataIndex: "availableAt", width: 175, render: formatDate },
    { title: "租约到期", dataIndex: "leaseExpiresAt", width: 175, render: formatDate },
    {
      title: "错误摘要", width: 280,
      render: (_, item) => item.error ? <Text type="danger" ellipsis={{ tooltip: item.error }}>{item.errorCode ? `${item.errorCode}: ${item.error}` : item.error}</Text> : "-",
    },
  ];

  const summary = data?.summary;
  const workerState = !summary ? "未知" : summary.workerOnline ? "在线" : "离线";
  const workerStateColor = !summary ? "#8c8c8c" : summary.workerOnline ? "#3f8600" : "#cf1322";
  return (
    <Modal title="队列监控" open={open} onCancel={onClose} width={1480} footer={<Button onClick={onClose}>关闭</Button>} destroyOnHidden>
      {modalContextHolder}
      {messageContextHolder}
      <Space wrap style={{ width: "100%", justifyContent: "space-between", marginBottom: 16 }}>
        <Space wrap>
          <Select value={status} onChange={(value) => { setStatus(value); setPage(1); }} style={{ width: 140 }} options={[
            { value: "active", label: "当前活跃任务" }, { value: "queued", label: "排队中" },
            { value: "running", label: "执行中" }, { value: "blocked", label: "等待依赖" },
            { value: "manual_review", label: "待人工复核" }, { value: "manual_review_completed", label: "人工复核已完成" },
            { value: "manual_review_archived", label: "人工复核已归档" }, { value: "all", label: "全部监控任务" },
          ]} />
          <Select value={health} onChange={(value) => { setHealth(value); setPage(1); }} style={{ width: 130 }} options={[
            { value: "all", label: "全部健康状态" }, { value: "normal", label: "正常" },
            { value: "attention", label: "需关注" }, { value: "stuck", label: "卡住" },
          ]} />
          <Select value={provider} onChange={(value) => { setProvider(value); setPage(1); }} allowClear placeholder="全部 Provider" style={{ width: 145 }} options={[
            { value: "doubao", label: "Doubao" }, { value: "gemini", label: "Gemini" }, { value: "rule", label: "本地规则" },
          ]} />
        </Space>
        <Space>
          <Text type="secondary">最近刷新：{data ? formatDate(data.generatedAt) : "-"}</Text>
          <Button icon={<ReloadOutlined />} loading={isLoading} onClick={() => void mutate()}>刷新</Button>
        </Space>
      </Space>

      {error && <Alert type="error" showIcon title={error.message || "读取队列监控数据失败"} style={{ marginBottom: 16 }} />}
      {summary && (summary.stuckCount > 0 || !summary.workerOnline || summary.attentionCount > 0) && (
        <Alert
          type={summary.stuckCount > 0 || !summary.workerOnline ? "error" : "warning"}
          showIcon
          title={!summary.workerOnline ? "Worker 离线" : summary.stuckCount > 0 ? `发现 ${summary.stuckCount} 条卡住任务` : `发现 ${summary.attentionCount} 条需关注任务`}
          description={!summary.workerOnline ? "存在活跃任务时，Worker 离线会导致队列无法继续消费。" : "可展开任务查看租约、依赖和错误详情。"}
          style={{ marginBottom: 16 }}
        />
      )}

      <Row gutter={[12, 12]} style={{ marginBottom: 16 }}>
        <Col span={3}><Statistic title="Worker" value={workerState} styles={{ content: { color: workerStateColor } }} suffix={summary?.workerOnline ? `${summary.workers.length} 个` : undefined} /></Col>
        <Col span={3}><Statistic title="排队中" value={summary?.queuedCount || 0} /></Col>
        <Col span={3}><Statistic title="执行中" value={summary?.runningCount || 0} /></Col>
        <Col span={3}><Statistic title="等待依赖" value={summary?.blockedCount || 0} /></Col>
        <Col span={3}><Statistic title="当前未解决的人工复核" value={summary?.manualReviewCount || 0} /></Col>
        <Col span={3}><Statistic title="需关注" value={summary?.attentionCount || 0} styles={{ content: { color: "#d48806" } }} /></Col>
        <Col span={3}><Statistic title="卡住" value={summary?.stuckCount || 0} styles={{ content: { color: "#cf1322" } }} /></Col>
        <Col span={3}><Statistic title="最长可执行等待" value={formatDuration(summary?.oldestReadyWaitSeconds || 0)} /></Col>
      </Row>

      <Table<QueueRun>
        rowKey="id" loading={isLoading} columns={columns} dataSource={data?.items || []} scroll={{ x: 1650 }}
        expandable={{
          expandedRowRender: (run) => <Table<QueueWorkItem> rowKey="id" size="small" pagination={false} columns={workColumns} dataSource={run.workItems} scroll={{ x: 1130 }} />,
          rowExpandable: (run) => run.workItems.length > 0,
        }}
        pagination={{
          current: data?.page || page, pageSize: data?.pageSize || 20, total: data?.total || 0,
          showSizeChanger: false, showTotal: (total) => `共 ${total} 条质检任务`, onChange: setPage,
        }}
      />
    </Modal>
  );
}
