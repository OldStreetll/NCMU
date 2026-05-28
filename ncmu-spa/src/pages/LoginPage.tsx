import { useEffect, useState } from "react";
import { Button, Card, Form, Select, Typography, message } from "antd";
import { useLocation, useNavigate } from "react-router-dom";
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
  const location = useLocation();
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
      // TASK-PE-01: post-login landing follows is_admin (admin → /admin,
      // employee → /staff). If RoleGuard deflected an unauthenticated visit
      // here, it appended ?returnTo=<original path> — honor that first so
      // deep-linked visits land where the user intended. URLSearchParams.get
      // already URL-decodes the value (the encoder is RoleGuard:25).
      const returnTo = new URLSearchParams(location.search).get("returnTo");
      const defaultPath = resp.user.is_admin ? "/admin" : "/staff";
      navigate(returnTo || defaultPath, { replace: true });
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
