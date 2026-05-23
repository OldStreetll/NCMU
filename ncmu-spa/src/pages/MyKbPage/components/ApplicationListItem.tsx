// TASK-PC3-A — single application row for MyKbPage list.
//
// Status badge palette literal mirrors `RunHistoryList.tsx:33` STATUS_COLOR
// (antd preset names + `default` for neutral grey — `cancelled` here uses
// `default` so antd renders the standard grey Tag, matching plan §4.6 AC#3
// "cancelled=gray + 与 RunHistoryList STATUS_COLOR 风格一致"). Grounding
// against the real RunHistoryList file rather than the plan literal is the
// fix from `feedback_plan_tech_stack_grounding` (NCMU 2026-05-19 TASK-F
// 实证 — plan 字面"灰色"vs RunHistoryList "default" 范式优先).
//
// Action buttons (AC#3 字面):
//   - pending   → [撤回] (Popconfirm → DELETE)
//   - done      → [前往对话] (navigate to /apps/{dify_app_id})
//   - other     → no action button

import { Alert, Button, List, Popconfirm, Tag, Typography } from "antd";
import { useNavigate } from "react-router-dom";
import type {
  PersonalKbApplication,
  PersonalKbApplicationStatus,
} from "@/lib/api";

const STATUS_COLOR: Record<PersonalKbApplicationStatus, string> = {
  pending: "orange",
  in_progress: "blue",
  done: "green",
  rejected: "red",
  cancelled: "default",
};

const STATUS_LABEL: Record<PersonalKbApplicationStatus, string> = {
  pending: "待处理",
  in_progress: "处理中",
  done: "已完成",
  rejected: "已拒绝",
  cancelled: "已撤回",
};

// Lightweight relative-time formatter — "X 秒 / 分钟 / 小时 / 天前".
// Locale is hard zh-CN; no need for Intl.RelativeTimeFormat across the
// SPA (Phase 1 keeps a single zh-CN bundle).
export function formatRelative(iso: string, now: number = Date.now()): string {
  const diffMs = now - new Date(iso).getTime();
  if (Number.isNaN(diffMs) || diffMs < 0) return new Date(iso).toLocaleString();
  const sec = Math.floor(diffMs / 1000);
  if (sec < 60) return "刚刚";
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min} 分钟前`;
  const hour = Math.floor(min / 60);
  if (hour < 24) return `${hour} 小时前`;
  const day = Math.floor(hour / 24);
  return `${day} 天前`;
}

export interface ApplicationListItemProps {
  app: PersonalKbApplication;
  onCancel: (id: string) => void | Promise<void>;
}

export function ApplicationListItem({
  app,
  onCancel,
}: ApplicationListItemProps) {
  const navigate = useNavigate();
  const status = app.status;

  let action: React.ReactNode = null;
  if (status === "pending") {
    action = (
      <Popconfirm
        title="确认撤回此申请？相关上传文件将一并删除"
        okText="确认"
        cancelText="取消"
        onConfirm={() => onCancel(app.id)}
      >
        <Button
          danger
          size="small"
          data-testid={`my-kb-cancel-button-${app.id}`}
        >
          撤回
        </Button>
      </Popconfirm>
    );
  } else if (status === "done" && app.dify_app_id) {
    action = (
      <Button
        type="primary"
        size="small"
        onClick={() => navigate(`/apps/${app.dify_app_id}`)}
      >
        前往对话
      </Button>
    );
  }

  return (
    <List.Item
      data-testid={`my-kb-application-item-${app.id}`}
      actions={action ? [action] : []}
    >
      <List.Item.Meta
        title={
          <span>
            <Typography.Text strong>
              {app.kb_name_suggested || "(未命名)"}
            </Typography.Text>{" "}
            <Tag color={STATUS_COLOR[status] ?? "default"}>
              {STATUS_LABEL[status]}
            </Tag>
          </span>
        }
        description={
          <>
            {app.description && (
              <Typography.Paragraph
                style={{ marginBottom: 4 }}
                ellipsis={{ rows: 2 }}
              >
                {app.description}
              </Typography.Paragraph>
            )}
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {formatRelative(app.created_at)}
            </Typography.Text>
            {status === "rejected" && app.rejection_reason && (
              <Alert
                type="warning"
                showIcon
                style={{ marginTop: 8 }}
                message={`拒绝原因：${app.rejection_reason}`}
              />
            )}
          </>
        }
      />
    </List.Item>
  );
}

export default ApplicationListItem;
