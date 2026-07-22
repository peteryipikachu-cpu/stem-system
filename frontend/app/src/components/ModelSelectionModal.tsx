"use client";

import { Modal, Radio, Space, Tag, Typography } from "antd";
import { useState } from "react";
import { AUDIT_MODELS, DEFAULT_AUDIT_MODEL_ID, type AuditModelId } from "@/lib/audit-models";

export default function ModelSelectionModal({ open, loading, onCancel, onConfirm }: {
  open: boolean; loading?: boolean; onCancel: () => void; onConfirm: (model: AuditModelId) => void;
}) {
  const [model, setModel] = useState<AuditModelId>(DEFAULT_AUDIT_MODEL_ID);
  return <Modal open={open} title="选择质检模型" okText="开始质检" cancelText="取消" confirmLoading={loading}
    onCancel={onCancel} onOk={() => onConfirm(model)} destroyOnHidden>
    <Typography.Paragraph type="secondary">本次任务的难度、答案、AI 合成题与答案等价判断均使用同一模型；LaTeX 仍由本地规则校验。</Typography.Paragraph>
    <Radio.Group value={model} onChange={(event) => setModel(event.target.value)} style={{ width: "100%" }}>
      <Space orientation="vertical" style={{ width: "100%" }}>
        {AUDIT_MODELS.map((item) => <Radio key={item.id} value={item.id} style={{ padding: "8px 0" }}>
          <Space><strong>{item.label}</strong><Tag>Pass@{item.passK}</Tag><Typography.Text type="secondary">难度通过：答对 ≤{item.difficultyThreshold} 次</Typography.Text></Space>
        </Radio>)}
      </Space>
    </Radio.Group>
  </Modal>;
}
