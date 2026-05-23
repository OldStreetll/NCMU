// TASK-PC3-A — KbApplicationModal: 员工 [+ 新建] 弹窗.
//
// File-validation constants mirror backend `schemas/personal_kb.py`
// (MAX_FILES_PER_APPLICATION / MAX_FILE_SIZE_BYTES / ALLOWED_EXTENSIONS).
// Plan §4.6 AC#2 字面 — frontend 拦截 + backend 兜底 (Q2-B 字段级).
//
// Error分流 (AC#2 字面):
//   - 422  → form item 行内 (kb_name_suggested 字段)
//   - 其他 → notification.error 全局 Toast
// AbortError-style errors are not expected on form-submit fetch (no AbortController
// wired in for this modal), so no short-circuit guard — the catch block only
// distinguishes ApiError.status === 422 vs everything else.

import { useState } from "react";
import {
  Button,
  Form,
  Input,
  Modal,
  Upload,
  notification,
  type UploadFile,
} from "antd";
import { ApiError, submitApplication } from "@/lib/api";

// Mirror of backend constants — kept in sync manually (守
// `feedback_status_enum_cross_stack_sync` SOP 4 阶段). Numeric literals
// match `schemas/personal_kb.py:23-28`.
export const MAX_FILES_PER_APPLICATION = 10;
export const MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024;
export const ALLOWED_EXTENSIONS = [
  "txt",
  "docx",
  "csv",
  "xlsx",
  "pdf",
  "md",
  "html",
  "pptx",
] as const;

const ACCEPT_STR = ALLOWED_EXTENSIONS.map((e) => `.${e}`).join(",");

interface KbApplicationFormValues {
  kb_name_suggested: string;
  description?: string;
}

export interface KbApplicationModalProps {
  open: boolean;
  onClose: () => void;
  // Refresh-trigger fired after a successful submit so the parent list
  // re-fetches (useMyApplications.refetch).
  onSubmitted: () => void;
}

// Extension extracted from filename; lower-cased, no leading dot. Empty
// string when there's no dot at all (matches Python's
// `Path(name).suffix.lstrip(".").lower()` behavior). Exported for tests.
export function extractExtension(name: string): string {
  const dot = name.lastIndexOf(".");
  if (dot < 0) return "";
  return name.slice(dot + 1).toLowerCase();
}

// Returns the first violation message, or null if all files pass.
// Exported for testability so the page-level test exercises the
// validation table without scrubbing through antd Upload internals.
export function validateFileList(files: UploadFile[]): string | null {
  if (files.length === 0) {
    return "请选择至少 1 个文件";
  }
  if (files.length > MAX_FILES_PER_APPLICATION) {
    return `最多上传 ${MAX_FILES_PER_APPLICATION} 个文件`;
  }
  for (const f of files) {
    const ext = extractExtension(f.name);
    if (!ALLOWED_EXTENSIONS.includes(ext as (typeof ALLOWED_EXTENSIONS)[number])) {
      return `不支持的文件格式 .${ext}`;
    }
    const size = f.size ?? f.originFileObj?.size ?? 0;
    if (size > MAX_FILE_SIZE_BYTES) {
      return `单文件不能超过 ${MAX_FILE_SIZE_BYTES / (1024 * 1024)} MB`;
    }
  }
  return null;
}

export function KbApplicationModal({
  open,
  onClose,
  onSubmitted,
}: KbApplicationModalProps) {
  const [form] = Form.useForm<KbApplicationFormValues>();
  const [fileList, setFileList] = useState<UploadFile[]>([]);
  const [fileError, setFileError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState<boolean>(false);

  const reset = () => {
    form.resetFields();
    setFileList([]);
    setFileError(null);
    setSubmitting(false);
  };

  const handleClose = () => {
    if (submitting) return;
    reset();
    onClose();
  };

  const handleSubmit = async (values: KbApplicationFormValues) => {
    const fileViolation = validateFileList(fileList);
    if (fileViolation) {
      setFileError(fileViolation);
      return;
    }
    setFileError(null);
    const fileObjs: File[] = fileList
      .map((f) => f.originFileObj as File | undefined)
      .filter((f): f is File => f instanceof File);
    setSubmitting(true);
    try {
      await submitApplication({
        kb_name_suggested: values.kb_name_suggested,
        description: values.description,
        files: fileObjs,
      });
      notification.success({ message: "申请已提交" });
      reset();
      onSubmitted();
      onClose();
    } catch (err: unknown) {
      if (err instanceof ApiError && err.status === 422) {
        // backend 422 → form item 行内 (plan §4.6 AC#2 字面). Body.detail
        // may be a per-field message (kb_name length) or file-level
        // (size/extension). The api() helper has already collapsed it
        // into `err.message`. Surface on the kb_name_suggested field for
        // discoverability — files have their own setFileError channel.
        form.setFields([
          {
            name: "kb_name_suggested",
            errors: [err.message || "提交内容不符合校验"],
          },
        ]);
      } else {
        notification.error({
          message: "提交失败",
          description: "请稍后再试",
        });
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      open={open}
      title="申请新建知识库"
      onCancel={handleClose}
      onOk={() => form.submit()}
      okText="提交"
      cancelText="取消"
      confirmLoading={submitting}
      destroyOnClose
      maskClosable={!submitting}
      keyboard={!submitting}
      data-testid="my-kb-application-modal"
    >
      <Form<KbApplicationFormValues>
        form={form}
        layout="vertical"
        onFinish={handleSubmit}
        requiredMark
      >
        <Form.Item
          label="知识库建议名称"
          name="kb_name_suggested"
          rules={[
            { required: true, message: "请填写知识库建议名称" },
            { max: 200, message: "最多 200 个字符" },
          ]}
        >
          <Input
            maxLength={200}
            placeholder="例如：研发部 OKR 知识库"
            data-testid="my-kb-modal-name"
          />
        </Form.Item>
        <Form.Item label="申请说明" name="description">
          <Input.TextArea
            rows={4}
            placeholder="可选：admin 中央化建库时参考"
            data-testid="my-kb-modal-description"
          />
        </Form.Item>
        <Form.Item
          label="上传文件"
          required
          validateStatus={fileError ? "error" : undefined}
          help={fileError ?? `支持 ${ACCEPT_STR}，最多 10 个文件，每文件 ≤ 50 MB`}
        >
          <Upload
            multiple
            accept={ACCEPT_STR}
            beforeUpload={() => false}
            fileList={fileList}
            onChange={({ fileList: next }) => {
              setFileList(next);
              setFileError(null);
            }}
            data-testid="my-kb-modal-upload"
          >
            <Button>选择文件</Button>
          </Upload>
        </Form.Item>
      </Form>
    </Modal>
  );
}

export default KbApplicationModal;
