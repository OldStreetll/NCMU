import { useEffect, useState } from "react";
import { Button, Card, Divider, Form, Select, Typography, message } from "antd";
import { useLocation, useNavigate } from "react-router-dom";
import { api, ApiError } from "@/lib/api";
import { setAuth, type AuthUser } from "@/lib/auth";
import { zh } from "@/locales/zh";

type DevLoginResponse = {
  jwt: string;
  user: AuthUser;
  expires_at: string;
};

// TASK-LOGIN-2: GET /auth/dingtalk/login response (LOGIN-1 contract). The
// state is embedded in authorize_url + an HttpOnly Set-Cookie the browser
// stores automatically — the SPA never reads it. We only need authorize_url
// to hand the browser off to DingTalk's authorization page.
type DingtalkLoginResponse = {
  authorize_url: string;
  state: string;
};

export default function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const [users, setUsers] = useState<AuthUser[]>([]);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [dingtalkLoading, setDingtalkLoading] = useState(false);

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

  // TASK-LOGIN-2 Pattern B step ①: fetch the DingTalk authorize URL, then
  // hand the whole page off to DingTalk (full-page redirect, not fetch) so
  // the QR-scan flow runs on dingtalk.com and returns to our /auth/dingtalk/
  // callback route. skipAuth: the user isn't logged in yet. The state cookie
  // is set by this same response (HttpOnly) — nothing to wire by hand.
  const onDingtalkLogin = async () => {
    setDingtalkLoading(true);
    try {
      const resp = await api<DingtalkLoginResponse>(
        "/auth/dingtalk/login",
        { method: "GET" },
        { skipAuth: true },
      );
      window.location.href = resp.authorize_url;
    } catch (e: unknown) {
      const msg = e instanceof ApiError ? `${e.status} ${e.message}` : String(e);
      message.error(`${zh.login.dingtalkLoginFailed}: ${msg}`);
      setDingtalkLoading(false);
    }
    // On success we don't clear dingtalkLoading — the page is navigating away.
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
        <Divider plain>或</Divider>
        <Button block loading={dingtalkLoading} onClick={onDingtalkLogin}>
          {zh.login.dingtalkLogin}
        </Button>
      </Card>
    </div>
  );
}
