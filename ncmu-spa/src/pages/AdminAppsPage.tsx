// TASK-PE-07 — admin App 同步管理页.
//
// antd Table + "立即同步" 按钮（POST /admin/sync_apps / 5s 防误点）+
// 行级 is_active 切换（停用二次确认："员工将失去访问"）+ 搜索框 +
// "显示已停用" 开关。同步反馈 Modal 显示后端**真实** shape
// {upserted, total_console}（非 plan 字面的 added/updated/deactivated/
// errors —— Boss 2026-05-29 拍板适配真实 sync_apps 返回）。
//
// 路径 B only：PE-07 Step 0 spike 实证 Dify v1.13.3 无 outbound webhook，
// 同步是轮询式（手动点 [立即同步]）。webhook 路径 A 已砍。
//
// 标签绑定 UI（apps↔tags Transfer）推 PE-08；本页 tag_count 仅展示
// （后端固定 0 占位）。

import { useState } from "react";
import {
  Button,
  Input,
  message,
  Modal,
  Popconfirm,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  ApiError,
  type AdminAppOut,
  type SyncAppsResponse,
  syncApps,
  updateAdminApp,
} from "@/lib/api";
import { useAdminApps } from "@/hooks/useAdminApps";

// last_synced_at 渲染：null → "尚未同步"；否则把 ISO 串裁成
// "YYYY-MM-DD HH:MM"（确定性 / 不依赖 locale，便于测试断言）。
function formatSyncedAt(iso: string | null): string {
  if (!iso) return "尚未同步";
  return iso.slice(0, 16).replace("T", " ");
}

export function AdminAppsPage() {
  const [includeInactive, setIncludeInactive] = useState(false);
  const [search, setSearch] = useState<string>("");
  const { data, isLoading, refetch } = useAdminApps({
    includeInactive,
    search: search || undefined,
  });

  // 5s 防误点：同步进行中或刚点过都禁用按钮（后端 30s 冷却属 sync_apps
  // 禁区，本 task 不实现 / 见 [DONE] 备注）。
  const [syncing, setSyncing] = useState(false);
  const [syncResult, setSyncResult] = useState<SyncAppsResponse | null>(null);

  async function handleSync() {
    setSyncing(true);
    try {
      const res = await syncApps();
      setSyncResult(res); // 弹反馈 Modal
      refetch();
    } catch (err) {
      message.error(formatError(err));
    } finally {
      // 5s 防误点：成功/失败后都再锁 5s，避免连点打爆 Dify Console。
      setTimeout(() => setSyncing(false), 5000);
    }
  }

  async function handleToggleActive(row: AdminAppOut, next: boolean) {
    try {
      await updateAdminApp(row.dify_app_id, { is_active: next });
      message.success(next ? `已启用：${row.name}` : `已停用：${row.name}`);
      refetch();
    } catch (err) {
      message.error(formatError(err));
    }
  }

  const columns: ColumnsType<AdminAppOut> = [
    {
      title: "名称",
      dataIndex: "name",
      key: "name",
    },
    {
      title: "模式",
      dataIndex: "mode",
      key: "mode",
      width: 140,
      render: (mode: string) => <Tag>{mode}</Tag>,
    },
    {
      title: "状态",
      dataIndex: "is_active",
      key: "is_active",
      width: 96,
      render: (active: boolean) =>
        active ? (
          <Tag color="green">启用</Tag>
        ) : (
          <Tag color="default">已停用</Tag>
        ),
    },
    {
      title: "上次同步",
      dataIndex: "last_synced_at",
      key: "last_synced_at",
      width: 180,
      render: (val: string | null) =>
        val ? (
          formatSyncedAt(val)
        ) : (
          <Typography.Text type="secondary">尚未同步</Typography.Text>
        ),
    },
    {
      title: "标签数",
      dataIndex: "tag_count",
      key: "tag_count",
      width: 96,
    },
    {
      title: "操作",
      key: "actions",
      width: 120,
      render: (_v, row) =>
        row.is_active ? (
          <Popconfirm
            title="确认停用该 App？"
            description="停用后员工将无法访问该 App（缓存与标签绑定保留）"
            okText="确定"
            cancelText="取消"
            onConfirm={() => handleToggleActive(row, false)}
          >
            <Button size="small" danger>
              停用
            </Button>
          </Popconfirm>
        ) : (
          <Button size="small" onClick={() => handleToggleActive(row, true)}>
            启用
          </Button>
        ),
    },
  ];

  return (
    <div>
      <Space style={{ marginBottom: 16 }} wrap>
        <Button type="primary" loading={syncing} onClick={handleSync}>
          立即同步
        </Button>
        <Input.Search
          allowClear
          placeholder="按名称搜索"
          style={{ width: 240 }}
          onSearch={(v) => setSearch(v.trim())}
        />
        <Space size="small">
          <span>显示已停用</span>
          <Switch checked={includeInactive} onChange={setIncludeInactive} />
        </Space>
      </Space>

      <Table<AdminAppOut>
        rowKey="dify_app_id"
        loading={isLoading}
        columns={columns}
        dataSource={data ?? []}
        pagination={false}
      />

      {/* 同步反馈 Modal — 真实 shape {upserted, total_console}。 */}
      <Modal
        title="同步完成"
        open={syncResult !== null}
        onCancel={() => setSyncResult(null)}
        onOk={() => setSyncResult(null)}
        okText="确定"
        cancelButtonProps={{ style: { display: "none" } }}
      >
        {syncResult && (
          <div>
            <p>
              UPSERT 命中：<strong>{syncResult.upserted}</strong> 条
            </p>
            <p>
              Dify Console 返回总数：
              <strong>{syncResult.total_console}</strong> 条
            </p>
            {syncResult.total_console - syncResult.upserted > 0 && (
              <Typography.Text type="warning">
                有 {syncResult.total_console - syncResult.upserted}{" "}
                条被跳过（通常因缺少 id）
              </Typography.Text>
            )}
          </div>
        )}
      </Modal>
    </div>
  );
}

// ---- helpers --------------------------------------------------------------

function formatError(err: unknown): string {
  if (err instanceof ApiError) {
    return (
      err.message ||
      `操作失败（HTTP ${err.status}${err.code ? ` / code ${err.code}` : ""}）`
    );
  }
  if (err instanceof Error) return err.message;
  return String(err);
}

export default AdminAppsPage;
