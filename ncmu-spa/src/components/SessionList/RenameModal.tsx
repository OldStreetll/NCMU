import { useEffect, useState } from "react";
import { Form, Input, Modal, message } from "antd";
import { ApiError, api } from "@/lib/api";
import type { Session } from "./index";

export type RenameModalProps = {
  target: Session | null;
  onCancel: () => void;
  onRenamed: (updated: Session) => void;
};

const TITLE_MAX = 255;

export function RenameModal({ target, onCancel, onRenamed }: RenameModalProps) {
  const [title, setTitle] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [messageApi, messageContextHolder] = message.useMessage();

  // Reset input each time a new target is opened.
  useEffect(() => {
    if (target) setTitle(target.title ?? "");
  }, [target]);

  const trimmed = title.trim();
  const valid = trimmed.length > 0 && trimmed.length <= TITLE_MAX;

  const handleOk = async () => {
    if (!target || !valid) return;
    setSubmitting(true);
    try {
      const updated = await api<Session>(`/sessions/${target.id}`, {
        method: "PATCH",
        body: JSON.stringify({ title: trimmed }),
      });
      onRenamed(updated);
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : "重命名失败";
      messageApi.error(msg);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <>
      {messageContextHolder}
      <Modal
        title="重命名会话"
        open={target !== null}
        onCancel={onCancel}
        onOk={handleOk}
        okText="保存"
        cancelText="取消"
        confirmLoading={submitting}
        okButtonProps={{ disabled: !valid }}
        destroyOnHidden
      >
        <Form layout="vertical">
          <Form.Item
            label="会话标题"
            validateStatus={trimmed.length > TITLE_MAX ? "error" : undefined}
            help={
              trimmed.length > TITLE_MAX
                ? `标题不超过 ${TITLE_MAX} 字`
                : undefined
            }
          >
            <Input
              data-testid="rename-input"
              autoFocus
              value={title}
              maxLength={TITLE_MAX}
              onChange={(e) => setTitle(e.target.value)}
              onPressEnter={() => {
                if (valid && !submitting) void handleOk();
              }}
            />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

export default RenameModal;
