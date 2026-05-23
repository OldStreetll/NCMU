// TASK-PC3-B — RejectModal: capture a reason then POST reject + navigate back.
//
// Backend Field rejection_reason has only min_length=1 (no max). We cap at 500
// in the textarea as a UX courtesy — anything that long is suspicious anyway.

import { Form, Input, Modal, message } from "antd";
import { ApiError, rejectApplication } from "@/lib/api";
import type { RejectIn } from "@/lib/api";

interface RejectModalProps {
  applicationId: string;
  open: boolean;
  onClose: () => void;
  onRejected: () => void;
}

export function RejectModal({
  applicationId,
  open,
  onClose,
  onRejected,
}: RejectModalProps) {
  const [form] = Form.useForm<RejectIn>();
  const [messageApi, messageContextHolder] = message.useMessage();

  async function handleSubmit() {
    const values = await form.validateFields();
    try {
      await rejectApplication(applicationId, values);
      form.resetFields();
      onClose();
      onRejected();
    } catch (err) {
      if (err instanceof ApiError) {
        messageApi.error(`拒绝失败 HTTP ${err.status}: ${err.message}`);
      } else {
        messageApi.error("拒绝失败，请稍后重试");
      }
    }
  }

  return (
    <Modal
      data-testid="admin-reject-modal"
      title="拒绝申请"
      open={open}
      onCancel={onClose}
      onOk={handleSubmit}
      okText="提交拒绝"
      cancelText="取消"
      destroyOnClose
    >
      {messageContextHolder}
      <Form form={form} layout="vertical" preserve={false}>
        <Form.Item
          name="rejection_reason"
          label="拒绝原因"
          rules={[
            { required: true, message: "必填" },
            { max: 500, message: "最多 500 字" },
          ]}
        >
          <Input.TextArea
            data-testid="admin-reject-reason"
            rows={4}
            maxLength={500}
            showCount
            placeholder="请说明拒绝原因（员工可见）"
          />
        </Form.Item>
      </Form>
    </Modal>
  );
}

export default RejectModal;
