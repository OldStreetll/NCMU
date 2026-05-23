// TASK-PC3-B — left column of the admin detail view.
//
// Shows applicant + suggested KB name + description + file list. Each file
// renders a [⬇ 下载] button that fetches the admin download endpoint with the
// session JWT in the Authorization header, then triggers a browser download
// from a temporary blob URL. Authorization on `<a href>` won't carry the
// header, so we must go through fetch.

import { Button, Descriptions, List, Tag, Typography, message } from "antd";
import { API_BASE } from "@/lib/api";
import type { ApplicationDetailOut, ApplicationStatus, FileMeta } from "@/lib/api";
import { getJwt } from "@/lib/auth";

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

function renderStatus(detail: ApplicationDetailOut): string {
  if (detail.status === "in_progress" && detail.admin_processed_by) {
    return `处理中（${detail.admin_processed_by} 占坑）`;
  }
  return STATUS_LABEL[detail.status];
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
}

interface ApplicationDetailPanelProps {
  detail: ApplicationDetailOut;
}

export function ApplicationDetailPanel({ detail }: ApplicationDetailPanelProps) {
  async function handleDownload(file: FileMeta) {
    const jwt = getJwt();
    if (!jwt) {
      message.error("会话已过期，请重新登录");
      return;
    }
    const url = `${API_BASE}/admin/personal-kb/applications/${detail.id}/files/${file.id}/download`;
    try {
      const resp = await fetch(url, {
        headers: { Authorization: `Bearer ${jwt}` },
      });
      if (!resp.ok) {
        message.error(`下载失败 HTTP ${resp.status}`);
        return;
      }
      const blob = await resp.blob();
      const blobUrl = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = blobUrl;
      a.download = file.original_filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(blobUrl);
    } catch (err) {
      message.error(err instanceof Error ? err.message : "下载失败");
    }
  }

  return (
    <div data-testid="admin-detail-left-panel">
      <Descriptions
        title="申请信息"
        column={1}
        size="small"
        bordered
        items={[
          {
            // The canonical, testable `admin-status-badge` lives on the
            // DispatchFormPanel side — keep this view-only label without a
            // testid so getByTestId stays unambiguous in the suite.
            key: "status",
            label: "状态",
            children: (
              <Tag color={STATUS_COLOR[detail.status]}>{renderStatus(detail)}</Tag>
            ),
          },
          { key: "user", label: "申请人 ID", children: detail.user_id },
          { key: "created", label: "提交时间", children: detail.created_at },
          {
            key: "suggested",
            label: "建议名",
            children: detail.kb_name_suggested ?? (
              <Typography.Text type="secondary">—</Typography.Text>
            ),
          },
          {
            key: "desc",
            label: "申请说明",
            children: detail.description ?? (
              <Typography.Text type="secondary">—</Typography.Text>
            ),
          },
        ]}
      />

      <Typography.Title level={5} style={{ marginTop: 24 }}>
        文档清单（{detail.files.length}）
      </Typography.Title>
      <List
        data-testid="admin-detail-file-list"
        size="small"
        bordered
        dataSource={detail.files}
        locale={{ emptyText: "无附件" }}
        renderItem={(file) => (
          <List.Item
            actions={[
              <Button
                key="download"
                data-testid={`admin-download-${file.id}`}
                size="small"
                onClick={() => handleDownload(file)}
              >
                ⬇ 下载
              </Button>,
            ]}
          >
            <List.Item.Meta
              title={file.original_filename}
              description={`${formatBytes(file.file_size_bytes)} · ${file.content_type ?? "unknown"}`}
            />
          </List.Item>
        )}
      />
    </div>
  );
}

export default ApplicationDetailPanel;
