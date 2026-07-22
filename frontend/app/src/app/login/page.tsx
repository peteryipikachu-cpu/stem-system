"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button, Card, Form, Input, Typography, message } from "antd";
import { LockOutlined, UserOutlined } from "@ant-design/icons";

const { Title, Text } = Typography;

export default function LoginPage() {
  const router = useRouter();
  const [submitting, setSubmitting] = useState(false);

  const submit = async (values: { username: string; password: string }) => {
    setSubmitting(true);
    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(values),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "登录失败");
      message.success(`欢迎回来，${data.username}`);
      router.replace("/");
    } catch (error) {
      message.error(error instanceof Error ? error.message : "登录失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", padding: 24, background: "linear-gradient(135deg, #eef7ff, #f8fbff)" }}>
      <Card style={{ width: "100%", maxWidth: 400, boxShadow: "0 12px 36px rgba(22, 119, 255, .12)" }}>
        <div style={{ textAlign: "center", marginBottom: 28 }}>
          <div style={{ fontSize: 36 }}>🔬</div>
          <Title level={3} style={{ margin: "8px 0 4px" }}>STEM 题目审核系统</Title>
          <Text type="secondary">请使用管理员下发的账号登录</Text>
        </div>
        <Form layout="vertical" onFinish={submit} autoComplete="on">
          <Form.Item name="username" label="用户名" rules={[{ required: true, message: "请输入用户名" }]}>
            <Input prefix={<UserOutlined />} autoComplete="username" size="large" />
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true, message: "请输入密码" }]}>
            <Input.Password prefix={<LockOutlined />} autoComplete="current-password" size="large" />
          </Form.Item>
          <Button type="primary" htmlType="submit" size="large" block loading={submitting}>登录</Button>
        </Form>
      </Card>
    </main>
  );
}
