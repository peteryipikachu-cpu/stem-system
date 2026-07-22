"use client";

import { useState } from "react";
import useSWR from "swr";
import type { Dayjs } from "dayjs";
import { Alert, Button, DatePicker, Form, Input, Modal, Select, Space, Table, Tag, message } from "antd";
import { PlusOutlined } from "@ant-design/icons";

interface ManagedUser {
  id: number;
  username: string;
  role: "user" | "admin";
  isActive: boolean;
  isExpired: boolean;
  expiresAt: string | null;
  createdAt: string | null;
}

interface CreateUserValues {
  username: string;
  password: string;
  role: "user" | "admin";
  expiresAt?: Dayjs;
}

interface AccountManagementModalProps {
  open: boolean;
  onClose: () => void;
}

const fetcher = async (url: string) => {
  const response = await fetch(url);
  if (!response.ok) throw new Error("无法读取账号列表");
  return response.json() as Promise<ManagedUser[]>;
};

export default function AccountManagementModal({ open, onClose }: AccountManagementModalProps) {
  const [createOpen, setCreateOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [issued, setIssued] = useState<{ username: string; password: string; role: string } | null>(null);
  const [form] = Form.useForm<CreateUserValues>();
  const { data: users, isLoading, mutate } = useSWR(open ? "/api/users" : null, fetcher);

  const createAccount = async ({ expiresAt, ...values }: CreateUserValues) => {
    setCreating(true);
    try {
      const response = await fetch("/api/users", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...values, expiresAt: expiresAt?.toISOString() }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "账号创建失败");
      setIssued({ username: data.username, password: data.initialPassword, role: data.role });
      form.resetFields();
      setCreateOpen(false);
      void mutate();
      message.success("账号已创建，请安全下发凭据");
    } catch (error) {
      message.error(error instanceof Error ? error.message : "账号创建失败");
    } finally {
      setCreating(false);
    }
  };

  return (
    <>
      <Modal
        title="账号管理"
        open={open}
        onCancel={onClose}
        width={760}
        footer={<Button onClick={onClose}>关闭</Button>}
      >
        {issued && (
          <Alert
            type="success"
            showIcon
            closable
            onClose={() => setIssued(null)}
            title="账号已创建，请仅通过安全渠道下发以下凭据"
            description={<span>用户名：<b>{issued.username}</b>　初始密码：<b>{issued.password}</b>　角色：{issued.role === "admin" ? "管理员" : "普通用户"}</span>}
            style={{ marginBottom: 16 }}
          />
        )}
        <Space style={{ marginBottom: 16 }}>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>下发新账号</Button>
        </Space>
        <Table<ManagedUser>
          dataSource={users}
          loading={isLoading}
          rowKey="id"
          pagination={{ pageSize: 8, showTotal: (total) => `共 ${total} 个账号` }}
          columns={[
            { title: "用户名", dataIndex: "username" },
            { title: "角色", dataIndex: "role", render: (role: string) => <Tag color={role === "admin" ? "purple" : "blue"}>{role === "admin" ? "管理员" : "普通用户"}</Tag> },
            { title: "状态", dataIndex: "isActive", render: (active: boolean, user) => <Tag color={active ? "success" : user.isExpired ? "error" : "default"}>{active ? "启用" : user.isExpired ? "已到期" : "停用"}</Tag> },
            { title: "使用期限", dataIndex: "expiresAt", render: (date: string | null) => date ? new Date(date).toLocaleString("zh-CN", { hour12: false }) : "长期有效" },
            { title: "创建时间", dataIndex: "createdAt", render: (date: string | null) => date ? new Date(date).toLocaleString("zh-CN", { hour12: false }) : "-" },
          ]}
        />
      </Modal>

      <Modal
        title="下发新账号"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => form.submit()}
        okText="创建并显示凭据"
        cancelText="取消"
        confirmLoading={creating}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" initialValues={{ role: "user" }} onFinish={createAccount}>
          <Form.Item name="username" label="用户名" rules={[{ required: true, min: 3, message: "用户名至少 3 位" }, { pattern: /^[A-Za-z0-9_.-]+$/, message: "仅支持字母、数字、下划线、点和连字符" }]}>
            <Input autoComplete="off" placeholder="例如：zhangsan" />
          </Form.Item>
          <Form.Item name="password" label="初始密码" rules={[{ required: true, min: 8, message: "密码至少 8 位" }]}>
            <Input.Password autoComplete="new-password" placeholder="至少 8 位" />
          </Form.Item>
          <Form.Item name="role" label="账号角色">
            <Select options={[{ value: "user", label: "普通用户（仅查看自己的题目）" }, { value: "admin", label: "管理员（查看全部题目并管理账号）" }]} />
          </Form.Item>
          <Form.Item name="expiresAt" label="使用期限" extra="留空表示长期有效；到期后该账号将无法登录。">
            <DatePicker showTime style={{ width: "100%" }} placeholder="选择到期日期和时间（可选）" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
