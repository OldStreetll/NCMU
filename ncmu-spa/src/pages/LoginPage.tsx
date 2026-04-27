import { useEffect, useState } from "react";
import { Button, Card, Form, Select, Typography, message } from "antd";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "@/lib/api";
import { setAuth, type AuthUser } from "@/lib/auth";
import { zh } from "@/locales/zh";

type DevLoginResponse = {
  jwt: string;
  user: AuthUser;
  expires_at: string;
};

export default function LoginPage() {
  const navigate = useNavigate();
  const [users, setUsers] = useState<AuthUser[]>([]);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api<AuthUser[]>("/dev/users", { method: "GET" }, { skipAuth: true })
      .then((rows) => {
        if (!cancelled) setUsers(rows);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        const msg = e instanceof ApiError ? `${e.status} ${e.message}` : String(e);
        message.error(`load dev users failed: ${msg}`);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const onFinish = async (values: { userId: string }) => {
    setSubmitting(true);
    try {
      const resp = await api<DevLoginResponse>(
        "/auth/dev-login",
        {
          method: "POST",
          body: JSON.stringify({ user_id: values.userId }),
        },
        { skipAuth: true },
      );
      setAuth(resp.jwt, resp.user);
      navigate("/chat", { replace: true });
    } catch (e: unknown) {
      const msg = e instanceof ApiError ? `${e.status} ${e.message}` : String(e);
      message.error(`${zh.login.failed}: ${msg}`);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div style={{ display: "flex", justifyContent: "center", paddingTop: 96 }}>
      <Card style={{ width: 420 }} title={zh.app.title}>
        <Typography.Paragraph type="secondary">{zh.login.heading}</Typography.Paragraph>
        <Form layout="vertical" onFinish={onFinish}>
          <Form.Item
            label="测试账号"
            name="userId"
            rules={[{ required: true, message: zh.login.placeholder }]}
          >
            <Select
              loading={loading}
              placeholder={zh.login.placeholder}
              options={users.map((u) => ({
                value: u.id,
                label: `${u.name}${u.dept_path ? ` · ${u.dept_path}` : ""}`,
              }))}
              showSearch
              optionFilterProp="label"
            />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" block loading={submitting}>
              {zh.login.submit}
            </Button>
          </Form.Item>
        </Form>
      </Card>
    </div>
  );
}
