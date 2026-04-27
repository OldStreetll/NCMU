// TASK-32 — Admin debug page rendering the two read-only admin tables
// (dify_external_kb_configs + dify_app_kb_bindings) shipped by TASK-28.
//
// Backend contract (real, verified against ncmu_backend/admin/routes.py +
// schemas/admin.py):
//   GET /api/v1/ncmu/admin/kb-configs → list[KbConfigOut]
//   GET /api/v1/ncmu/admin/dify-app-bindings → list[BindingOut]
// Both gated by require_admin → 403 + code 1201 for non-admin JWT subjects.
//
// AC#5 「页面内权限校验」 — server-driven: SPA AuthUser has no is_admin field
// (Phase 1 dev profile keeps it simple), so the page calls the API and renders
// a "权限不足" alert when the backend returns 403. This matches how the dev
// seed actually works: only the ZHANGSAN UUID is in NCMU_ADMIN_USER_IDS by
// default, every other dev seed user gets 403 here.

import { useEffect, useState } from "react";
import { Alert, Spin, Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { ApiError, api } from "@/lib/api";

interface KbConfig {
  id: string;
  external_kb_name: string;
  fastgpt_dataset_id: string;
  api_key_label: string;
  upstream_endpoint: string | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

interface AppBinding {
  id: string;
  dify_app_id: string;
  external_kb_config_id: string;
  bound_at: string;
}

const kbColumns: ColumnsType<KbConfig> = [
  { title: "Name", dataIndex: "external_kb_name", key: "external_kb_name", width: 180 },
  { title: "FastGPT dataset", dataIndex: "fastgpt_dataset_id", key: "fastgpt_dataset_id", width: 240, ellipsis: true },
  { title: "API key label", dataIndex: "api_key_label", key: "api_key_label", width: 160 },
  {
    title: "Upstream",
    dataIndex: "upstream_endpoint",
    key: "upstream_endpoint",
    render: (v: string | null) => v ?? <Typography.Text type="secondary">—</Typography.Text>,
  },
  {
    title: "Notes",
    dataIndex: "notes",
    key: "notes",
    ellipsis: true,
    render: (v: string | null) => v ?? <Typography.Text type="secondary">—</Typography.Text>,
  },
  { title: "Updated", dataIndex: "updated_at", key: "updated_at", width: 200 },
];

const bindingColumns: ColumnsType<AppBinding> = [
  { title: "Dify app id", dataIndex: "dify_app_id", key: "dify_app_id", width: 320 },
  { title: "KB config id", dataIndex: "external_kb_config_id", key: "external_kb_config_id", width: 320 },
  { title: "Bound at", dataIndex: "bound_at", key: "bound_at", width: 220 },
];

interface LoadError {
  status: number;
  message: string;
}

export default function AdminKbConfigsPage() {
  const [kbConfigs, setKbConfigs] = useState<KbConfig[] | null>(null);
  const [bindings, setBindings] = useState<AppBinding[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<LoadError | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [k, b] = await Promise.all([
          api<KbConfig[]>("/admin/kb-configs"),
          api<AppBinding[]>("/admin/dify-app-bindings"),
        ]);
        if (cancelled) return;
        setKbConfigs(k);
        setBindings(b);
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError) {
          setError({ status: err.status, message: err.message });
        } else {
          setError({ status: 0, message: err instanceof Error ? err.message : "加载失败" });
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) {
    return (
      <div data-testid="admin-loading" style={{ display: "flex", justifyContent: "center", padding: 48 }}>
        <Spin />
      </div>
    );
  }

  if (error) {
    if (error.status === 403) {
      return (
        <Alert
          data-testid="admin-403"
          type="error"
          showIcon
          message="权限不足"
          description="该页面仅 admin 用户可访问。请联系管理员将你的账号加入 NCMU_ADMIN_USER_IDS。"
          style={{ margin: 24 }}
        />
      );
    }
    return (
      <Alert
        data-testid="admin-error"
        type="error"
        showIcon
        message={`加载失败 (HTTP ${error.status})`}
        description={error.message}
        style={{ margin: 24 }}
      />
    );
  }

  return (
    <div data-testid="admin-kb-configs-page" style={{ padding: 24 }}>
      <Typography.Title level={3} style={{ marginTop: 0 }}>
        Admin · KB Configs
      </Typography.Title>
      <Typography.Paragraph type="secondary">
        Phase 1 read-only debug surface. POST/PUT/DELETE 在 Phase 3 admin UI 上线后开放。
      </Typography.Paragraph>

      <Typography.Title level={4} style={{ marginTop: 24 }}>
        dify_external_kb_configs
      </Typography.Title>
      <Table<KbConfig>
        rowKey="id"
        size="small"
        pagination={false}
        columns={kbColumns}
        dataSource={kbConfigs ?? []}
        locale={{ emptyText: "无 KB config 记录" }}
      />

      <Typography.Title level={4} style={{ marginTop: 32 }}>
        dify_app_kb_bindings
      </Typography.Title>
      <Table<AppBinding>
        rowKey="id"
        size="small"
        pagination={false}
        columns={bindingColumns}
        dataSource={bindings ?? []}
        locale={{ emptyText: "无 app-binding 记录" }}
      />
    </div>
  );
}
