// TASK-PC3-B — admin landing for personal-KB applications.
//
// Pulls the full list once (no `?status=` filter, per r3 INDEP-2 I-INDEP2-3
// path-b) and polls every 30s (Q6-A). PendingBadge counts pending+in_progress
// from the same client-side list. Segmented control filters the rendered
// table without re-fetching. Row click navigates into the detail view.

import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Alert, Segmented, Spin, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { ApiError, listPendingApplications } from "@/lib/api";
import type { ApplicationOut, ApplicationStatus } from "@/lib/api";
import { PendingBadge } from "./AdminPersonalKbPage/components/PendingBadge";

const POLL_INTERVAL_MS = 30_000;

type StatusFilter = "all" | ApplicationStatus;

const STATUS_COLOR: Record<ApplicationStatus, string> = {
  pending: "orange",
  in_progress: "blue",
  done: "green",
  rejected: "red",
  cancelled: "default",
};

const STATUS_LABEL: Record<ApplicationStatus, string> = {
  pending: "待处理",
  in_progress: "处理中",
  done: "已完成",
  rejected: "已拒绝",
  cancelled: "已取消",
};

const FILTER_OPTIONS: { label: string; value: StatusFilter }[] = [
  { label: "全部", value: "all" },
  { label: "待处理", value: "pending" },
  { label: "处理中", value: "in_progress" },
  { label: "已完成", value: "done" },
  { label: "已拒绝", value: "rejected" },
  { label: "已取消", value: "cancelled" },
];

const columns: ColumnsType<ApplicationOut> = [
  {
    title: "状态",
    dataIndex: "status",
    key: "status",
    width: 120,
    render: (s: ApplicationStatus) => (
      <Tag color={STATUS_COLOR[s]}>{STATUS_LABEL[s]}</Tag>
    ),
  },
  { title: "申请人 ID", dataIndex: "user_id", key: "user_id", width: 280, ellipsis: true },
  {
    title: "建议名",
    dataIndex: "kb_name_suggested",
    key: "kb_name_suggested",
    ellipsis: true,
    render: (v: string | null) => v ?? <Typography.Text type="secondary">—</Typography.Text>,
  },
  { title: "提交时间", dataIndex: "created_at", key: "created_at", width: 200 },
];

export function AdminPersonalKbPage() {
  const navigate = useNavigate();
  const [applications, setApplications] = useState<ApplicationOut[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<ApiError | null>(null);
  const [filter, setFilter] = useState<StatusFilter>("pending");

  useEffect(() => {
    let cancelled = false;
    const fetchOnce = async (): Promise<void> => {
      try {
        const res = await listPendingApplications();
        if (cancelled) return;
        setApplications(res);
        setError(null);
      } catch (err) {
        if (cancelled) return;
        setError(
          err instanceof ApiError
            ? err
            : new ApiError(0, undefined, err instanceof Error ? err.message : String(err)),
        );
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    };
    void fetchOnce();
    const handle = setInterval(() => void fetchOnce(), POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(handle);
    };
  }, []);

  const filtered = useMemo(() => {
    if (filter === "all") return applications;
    return applications.filter((a) => a.status === filter);
  }, [applications, filter]);

  if (isLoading) {
    return (
      <div
        data-testid="admin-personal-kb-loading"
        style={{ display: "flex", justifyContent: "center", padding: 48 }}
      >
        <Spin />
      </div>
    );
  }

  return (
    <div data-testid="admin-personal-kb-page" style={{ padding: 24 }}>
      <Typography.Title level={3} style={{ marginTop: 0 }}>
        Admin · 个人知识库审核
      </Typography.Title>

      <PendingBadge applications={applications} />

      {error && (
        <Alert
          data-testid="admin-personal-kb-error"
          type="error"
          showIcon
          message={`加载失败 HTTP ${error.status}`}
          description={error.message}
          style={{ marginBottom: 16 }}
          closable
          onClose={() => setError(null)}
        />
      )}

      <Segmented
        data-testid="admin-personal-kb-filter"
        value={filter}
        onChange={(v) => setFilter(v as StatusFilter)}
        options={FILTER_OPTIONS}
        style={{ marginBottom: 16 }}
      />

      <Table<ApplicationOut>
        rowKey="id"
        size="middle"
        pagination={{ pageSize: 20, showSizeChanger: false }}
        columns={columns}
        dataSource={filtered}
        locale={{ emptyText: "无申请" }}
        onRow={(record) => ({
          "data-testid": `admin-personal-kb-row-${record.id}`,
          onClick: () => navigate(`/admin/personal-kb/applications/${record.id}`),
          style: { cursor: "pointer" },
        })}
      />
    </div>
  );
}

export default AdminPersonalKbPage;
