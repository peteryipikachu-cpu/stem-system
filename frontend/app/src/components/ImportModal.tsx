"use client";
import React, { useState, useCallback } from "react";
import {
  Modal,
  Upload,
  Table,
  Button,
  message,
  Typography,
  Alert,
  Steps,
  Progress,
} from "antd";
import { InboxOutlined } from "@ant-design/icons";
import * as XLSX from "xlsx";

const { Dragger } = Upload;
const { Text } = Typography;

interface ImportModalProps {
  visible: boolean;
  onClose: () => void;
  onSuccess: () => void;
}

// 固定列映射：Excel 表头 → 数据库字段
const HEADER_MAP: Record<string, string> = {
  标题: "title",
  题目类型: "type",
  领域: "domain",
  难度: "difficulty",
  知识点: "knowledgePoints",
  问题: "question",
  答案: "answer",
  解题思路: "solution",
};

interface ParsedRow {
  previewKey: string;
  title: string;
  type: string;
  domain: string;
  difficulty: string;
  knowledgePoints: string;
  question: string;
  answer: string;
  solution: string;
}

export default function ImportModal({ visible, onClose, onSuccess }: ImportModalProps) {
  const [step, setStep] = useState(0);
  const [rows, setRows] = useState<ParsedRow[]>([]);
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [fileName, setFileName] = useState("");

  const reset = () => {
    setStep(0);
    setRows([]);
    setProgress(0);
    setFileName("");
    setUploading(false);
  };

  const handleClose = () => {
    reset();
    onClose();
  };

  const handleFile = useCallback((file: File) => {
    setFileName(file.name);
    const reader = new FileReader();
    reader.onload = (e) => {
      try {
        const data = e.target?.result;
        const wb = XLSX.read(data, { type: "binary" });
        const ws = wb.Sheets[wb.SheetNames[0]];
        const json = XLSX.utils.sheet_to_json<unknown[]>(ws, { header: 1 });

        if (!json.length) {
          message.error("Excel 文件为空");
          return;
        }

        const rawHeaders = (json[0] as unknown[]).map((h) => String(h || "").trim());
        // HEADER_MAP 的所有键（即表头名称集合）
        const headerNames = new Set(Object.keys(HEADER_MAP));

        const dataRows = (json.slice(1) as unknown[][])
          .filter((row) => row.some((cell) => cell !== undefined && cell !== ""))
          .map((row, index) => {
            const obj: Record<string, string> = {};
            rawHeaders.forEach((h, i) => {
              const field = HEADER_MAP[h];
              if (field) obj[field] = String(row[i] ?? "").trim();
            });
            return { ...obj, previewKey: `row-${index + 2}` } as ParsedRow;
          })
          // 过滤：问题为空，或问题内容本身就是表头名（如嵌入的第二行表头）
          .filter((row) => {
            const q = row.question?.trim();
            if (!q) return false;
            if (headerNames.has(q)) return false; // 排除内嵌表头行
            return true;
          });

        if (!dataRows.length) {
          message.error("未找到有效题目（「问题」列为空）");
          return;
        }

        setRows(dataRows);
        setStep(1);
        message.success(`解析成功，共 ${dataRows.length} 道题目`);
      } catch (err) {
        message.error("文件解析失败: " + (err instanceof Error ? err.message : "Unknown"));
      }
    };
    reader.readAsBinaryString(file);
    return false;
  }, []);

  const handleImport = async () => {
    setUploading(true);
    setStep(2);

    const batchSize = 20;
    let totalCreated = 0;
    let totalSkipped = 0;

    try {
      for (let i = 0; i < rows.length; i += batchSize) {
        const batch = rows.slice(i, i + batchSize);
        const items = batch.map(({ previewKey, ...row }) => {
          void previewKey;
          return row;
        });
        const res = await fetch("/api/questions", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ items }),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        totalCreated += data.created ?? batch.length;
        totalSkipped += data.skipped ?? 0;
        setProgress(Math.round(((i + batch.length) / rows.length) * 100));
      }

      const skipMsg = totalSkipped > 0 ? `，跳过重复 ${totalSkipped} 道` : "";
      message.success(`成功导入 ${totalCreated} 道题目${skipMsg}`);
      onSuccess();
      reset();
    } catch (err) {
      message.error("导入失败: " + (err instanceof Error ? err.message : "Unknown"));
      setUploading(false);
      setStep(1);
    }
  };

  const previewColumns = [
    { title: "标题", dataIndex: "title", width: 120, ellipsis: true },
    { title: "题目类型", dataIndex: "type", width: 80 },
    { title: "领域", dataIndex: "domain", width: 100, ellipsis: true },
    { title: "难度", dataIndex: "difficulty", width: 70 },
    { title: "知识点", dataIndex: "knowledgePoints", width: 120, ellipsis: true },
    {
      title: "问题（截断）",
      dataIndex: "question",
      ellipsis: true,
      render: (v: string) => <Text style={{ fontSize: 12 }}>{v?.slice(0, 60)}{v?.length > 60 ? "…" : ""}</Text>,
    },
  ];

  return (
    <Modal
      title="导入题目"
      open={visible}
      onCancel={handleClose}
      width={860}
      footer={
        step === 1 ? (
          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
            <Button onClick={() => setStep(0)}>重新上传</Button>
            <Button type="primary" onClick={handleImport}>
              确认导入（{rows.length} 道题）
            </Button>
          </div>
        ) : (
          <Button onClick={handleClose}>关闭</Button>
        )
      }
    >
      <Steps
        current={step}
        items={[{ title: "上传文件" }, { title: "预览确认" }, { title: "导入中" }]}
        style={{ marginBottom: 24 }}
      />

      {step === 0 && (
        <Dragger
          accept=".xlsx,.xls,.csv"
          beforeUpload={handleFile}
          showUploadList={false}
          style={{ padding: "20px 0" }}
        >
          <p className="ant-upload-drag-icon">
            <InboxOutlined style={{ fontSize: 48, color: "#1890ff" }} />
          </p>
          <p className="ant-upload-text">点击或拖拽 Excel 文件到此处</p>
          <p className="ant-upload-hint">
            支持 .xlsx、.xls、.csv 格式，第一行须为固定表头：
            标题、题目类型、领域、难度、知识点、问题、答案、解题思路
          </p>
        </Dragger>
      )}

      {step === 1 && (
        <>
          <Alert
            title={`已解析「${fileName}」，共 ${rows.length} 道题目，请确认后导入`}
            type="info"
            style={{ marginBottom: 12 }}
          />
          <Table
            columns={previewColumns}
            dataSource={rows}
            rowKey="previewKey"
            pagination={{ pageSize: 10, showTotal: (t) => `共 ${t} 条` }}
            size="small"
            scroll={{ x: 700, y: 300 }}
          />
        </>
      )}

      {step === 2 && (
        <div style={{ textAlign: "center", padding: "40px 0" }}>
          <Progress type="circle" percent={progress} />
          <div style={{ marginTop: 16 }}>
            <Text>{uploading ? `正在导入... ${progress}%` : "导入完成！"}</Text>
          </div>
        </div>
      )}
    </Modal>
  );
}
