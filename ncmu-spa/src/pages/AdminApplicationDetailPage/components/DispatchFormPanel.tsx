// TASK-PC3-B — right column: naming suggestion → claim → dispatch / reject.
//
// State machine the buttons mirror (from personal_kb.application_service):
//   pending      →  claim only        (dispatch + reject only after claim)
//   in_progress  →  dispatch | reject (claim button hides)
//   done/rejected/cancelled → all buttons hide (terminal)
//
// claim 409 → toast + leave detail state untouched; backend will not have
// flipped status. dispatch 409 → form-level errorMessage from body.detail
// (KB-name collision is the dominant case). reject is handled by RejectModal.

import { useState } from "react";
import { Alert, Button, Form, Input, Space, Tag, Typography, message } from "antd";
import {
  ApiError,
  claimApplication,
  dispatchApplication,
} from "@/lib/api";
import type {
  ApplicationDetailOut,
  ApplicationStatus,
  DispatchIn,
} from "@/lib/api";
import { RejectModal } from "./RejectModal";

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

interface DispatchFormPanelProps {
  detail: ApplicationDetailOut;
  onChanged: () => void; // bumps the useAdminApplication tick
  onDispatched: () => void; // navigate back to list
  onRejected: () => void; // navigate back to list
}

export function DispatchFormPanel({
  detail,
  onChanged,
  onDispatched,
  onRejected,
}: DispatchFormPanelProps) {
  const [claiming, setClaiming] = useState(false);
  const [dispatching, setDispatching] = useState(false);
  const [rejectOpen, setRejectOpen] = useState(false);
  const [dispatchError, setDispatchError] = useState<string | null>(null);
  const [messageApi, messageContextHolder] = message.useMessage();
  const [form] = Form.useForm<DispatchIn>();

  const isPending = detail.status === "pending";
  const isInProgress = detail.status === "in_progress";
  const isTerminal = !isPending && !isInProgress;

  const suggestedChatAppName = detail.kb_name_suggested ?? "(未填)";
  const suggestedTag = detail.kb_name_suggested
    ? `kb-${detail.kb_name_suggested}`
    : "(未填)";
  const suggestedDataset = detail.kb_name_suggested ?? "(未填)";

  function copy(text: string) {
    void navigator.clipboard
      .writeText(text)
      .then(() => messageApi.success("已复制"))
      .catch(() => messageApi.warning("剪贴板不可用，请手动复制"));
  }

  async function handleClaim() {
    setClaiming(true);
    try {
      await claimApplication(detail.id);
      onChanged();
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 409) {
          messageApi.error("该申请已被其他管理员认领");
        } else if (err.status === 404) {
          messageApi.error("申请不存在");
        } else if (err.status === 422) {
          messageApi.error(err.message || "申请状态已变化");
          onChanged();
        } else {
          messageApi.error(`认领失败 HTTP ${err.status}`);
        }
      } else {
        messageApi.error("认领失败，请稍后重试");
      }
    } finally {
      setClaiming(false);
    }
  }

  async function handleDispatch(values: DispatchIn) {
    setDispatching(true);
    setDispatchError(null);
    try {
      await dispatchApplication(detail.id, values);
      onDispatched();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setDispatchError(err.message || "状态冲突或名称冲突");
      } else if (err instanceof ApiError) {
        setDispatchError(`分发失败 HTTP ${err.status}: ${err.message}`);
      } else {
        setDispatchError("分发失败，请稍后重试");
      }
    } finally {
      setDispatching(false);
    }
  }

  return (
    <div data-testid="admin-detail-right-panel">
      {messageContextHolder}

      <Alert
        data-testid="admin-naming-suggestion"
        type="info"
        showIcon
        message="命名建议"
        description={
          <Space direction="vertical" size={4} style={{ width: "100%" }}>
            <Space size={8}>
              <Typography.Text>Chat App 名：</Typography.Text>
              <Typography.Text code>{suggestedChatAppName}</Typography.Text>
              <Button
                size="small"
                data-testid="admin-copy-chat-app-name"
                onClick={() => copy(suggestedChatAppName)}
              >
                复制
              </Button>
            </Space>
            <Space size={8}>
              <Typography.Text>Chat App tag：</Typography.Text>
              <Typography.Text code>{suggestedTag}</Typography.Text>
              <Button
                size="small"
                data-testid="admin-copy-chat-app-tag"
                onClick={() => copy(suggestedTag)}
              >
                复制
              </Button>
            </Space>
            <Space size={8}>
              <Typography.Text>Dataset 名：</Typography.Text>
              <Typography.Text code>{suggestedDataset}</Typography.Text>
              <Button
                size="small"
                data-testid="admin-copy-dataset-name"
                onClick={() => copy(suggestedDataset)}
              >
                复制
              </Button>
            </Space>
          </Space>
        }
        style={{ marginBottom: 16 }}
      />

      <div style={{ marginBottom: 16 }}>
        <Typography.Text>当前状态：</Typography.Text>{" "}
        <Tag data-testid="admin-status-badge" color={STATUS_COLOR[detail.status]}>
          {renderStatus(detail)}
        </Tag>
      </div>

      {isPending && (
        <Button
          data-testid="admin-claim-button"
          type="primary"
          loading={claiming}
          onClick={handleClaim}
          style={{ marginBottom: 16 }}
        >
          {claiming ? (
            <span data-testid="admin-claim-loading">认领中…</span>
          ) : (
            "开始处理"
          )}
        </Button>
      )}

      <Form
        form={form}
        layout="vertical"
        disabled={!isInProgress}
        onFinish={handleDispatch}
      >
        <Form.Item
          name="dataset_id"
          label="FastGPT dataset ID（MongoDB ObjectId）"
          rules={[
            { required: true, message: "必填" },
            { min: 1, message: "至少 1 字符" },
          ]}
        >
          <Input data-testid="admin-form-dataset-id" placeholder="例如 66e2a7f8a9b0c1d2e3f4567" />
        </Form.Item>

        <Form.Item
          name="dify_app_id"
          label="Dify App ID"
          rules={[
            { required: true, message: "必填" },
            { min: 1, message: "至少 1 字符" },
            { max: 64, message: "最多 64 字符" },
          ]}
        >
          <Input data-testid="admin-form-dify-app-id" placeholder="例如 abc12def" />
        </Form.Item>

        <Form.Item
          name="kb_name_final"
          label="最终 KB 名"
          rules={[
            { required: true, message: "必填" },
            { max: 200, message: "最多 200 字符" },
          ]}
        >
          <Input data-testid="admin-form-kb-name-final" placeholder="审核后落库的 external_kb_name" />
        </Form.Item>

        {dispatchError && (
          <Alert
            data-testid="admin-dispatch-error"
            type="error"
            showIcon
            message={dispatchError}
            style={{ marginBottom: 16 }}
            closable
            onClose={() => setDispatchError(null)}
          />
        )}

        <Space>
          <Button
            data-testid="admin-dispatch-button"
            type="primary"
            htmlType="submit"
            disabled={!isInProgress}
            loading={dispatching}
          >
            分发
          </Button>
          <Button
            data-testid="admin-reject-button"
            danger
            disabled={isTerminal}
            onClick={() => setRejectOpen(true)}
          >
            拒绝（填原因）
          </Button>
        </Space>
      </Form>

      <RejectModal
        applicationId={detail.id}
        open={rejectOpen}
        onClose={() => setRejectOpen(false)}
        onRejected={onRejected}
      />
    </div>
  );
}

export default DispatchFormPanel;
