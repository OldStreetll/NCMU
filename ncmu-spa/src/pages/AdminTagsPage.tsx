// TASK-PE-05 — admin 标签管理 CRUD 页（不含绑定 UI）.
//
// antd Table + "新建标签" 按钮 + 行级 "编辑 modal" / "删除 Popconfirm"。
// 4 列：名称 / 描述 / App 数 / 用户数。绑定 UI 推 PE-08（apps↔tags）+
// PE-09（users↔tags）；本批次仅 tags 表 CRUD。
//
// 删除 Popconfirm description 字面"将解除 X 个 App 与 Y 个用户的绑定"
// 提示 admin 后端 FK ondelete=CASCADE 行为（不是隐藏副作用）。

import { useState } from "react";
import {
  Alert,
  Button,
  Form,
  Input,
  message,
  Modal,
  Popconfirm,
  Space,
  Table,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  ApiError,
  type AdminTagCreateBody,
  type AdminTagOut,
  type AdminTagUpdateBody,
  createAdminTag,
  deleteAdminTag,
  updateAdminTag,
} from "@/lib/api";
import { useAdminTags } from "@/hooks/useAdminTags";
import { BindAppsModal } from "@/pages/AdminTagsPage/BindAppsModal";

export function AdminTagsPage() {
  const { data, isLoading, refetch } = useAdminTags();
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [editingTag, setEditingTag] = useState<AdminTagOut | null>(null);
  // TASK-PE-08: tag→apps binding modal target row.
  const [bindingTag, setBindingTag] = useState<AdminTagOut | null>(null);

  // 守 PE-06 REWORK-PE-06-INDEP #5 — declared above ``columns`` so the
  // array's ``onConfirm`` reference is not relying on function hoisting.
  async function handleDelete(row: AdminTagOut) {
    try {
      await deleteAdminTag(row.id);
      message.success(`已删除：${row.name}`);
      refetch();
    } catch (err) {
      message.error(formatError(err));
    }
  }

  const columns: ColumnsType<AdminTagOut> = [
    {
      title: "名称",
      dataIndex: "name",
      key: "name",
    },
    {
      title: "描述",
      dataIndex: "description",
      key: "description",
      render: (val: string | null) =>
        val ?? <Typography.Text type="secondary">—</Typography.Text>,
    },
    {
      title: "App 数",
      dataIndex: "app_count",
      key: "app_count",
      width: 96,
    },
    {
      title: "用户数",
      dataIndex: "user_count",
      key: "user_count",
      width: 96,
    },
    {
      title: "操作",
      key: "actions",
      render: (_v, row) => (
        <Space>
          <Button size="small" onClick={() => setEditingTag(row)}>
            编辑
          </Button>
          <Button size="small" onClick={() => setBindingTag(row)}>
            绑定 App
          </Button>
          <Popconfirm
            title="确认删除该标签？"
            description={`将解除 ${row.app_count} 个 App 与 ${row.user_count} 个用户的绑定（FK 级联）`}
            okText="确定"
            cancelText="取消"
            onConfirm={() => handleDelete(row)}
          >
            <Button size="small" danger>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <Space style={{ marginBottom: 16 }} wrap>
        <Button type="primary" onClick={() => setCreateModalOpen(true)}>
          新建标签
        </Button>
      </Space>

      <Table<AdminTagOut>
        rowKey="id"
        loading={isLoading}
        columns={columns}
        dataSource={data ?? []}
        pagination={false}
      />

      <CreateTagModal
        open={createModalOpen}
        onClose={() => setCreateModalOpen(false)}
        onCreated={() => {
          setCreateModalOpen(false);
          refetch();
        }}
      />

      {editingTag && (
        <EditTagModal
          tag={editingTag}
          onClose={() => setEditingTag(null)}
          onSaved={() => {
            setEditingTag(null);
            refetch();
          }}
        />
      )}

      {bindingTag && (
        <BindAppsModal
          tagId={bindingTag.id}
          tagName={bindingTag.name}
          open
          onClose={() => setBindingTag(null)}
          onSaved={refetch}
        />
      )}
    </div>
  );
}

// ---- Create modal ---------------------------------------------------------

interface CreateTagModalProps {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}

function CreateTagModal({ open, onClose, onCreated }: CreateTagModalProps) {
  const [form] = Form.useForm<AdminTagCreateBody>();
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit() {
    setSubmitError(null);
    let values: AdminTagCreateBody;
    try {
      values = await form.validateFields();
    } catch {
      return; // Form-level validation rendered its own messages.
    }
    setSubmitting(true);
    try {
      const created = await createAdminTag({
        name: values.name,
        description: values.description || null,
      });
      message.success(`标签已创建：${created.name}`);
      form.resetFields();
      onCreated();
    } catch (err) {
      setSubmitError(formatError(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal
      title="新建标签"
      open={open}
      onCancel={() => {
        form.resetFields();
        setSubmitError(null);
        onClose();
      }}
      onOk={handleSubmit}
      okText="创建"
      cancelText="取消"
      confirmLoading={submitting}
      destroyOnClose
    >
      <Form form={form} layout="vertical" preserve={false}>
        {submitError && (
          <Alert
            type="error"
            message={submitError}
            style={{ marginBottom: 12 }}
          />
        )}
        <Form.Item
          name="name"
          label="名称"
          rules={[
            { required: true, message: "名称必填" },
            { max: 64, message: "名称最多 64 字符" },
          ]}
        >
          <Input maxLength={64} placeholder="例：技术" />
        </Form.Item>
        <Form.Item
          name="description"
          label="描述（可选）"
          rules={[{ max: 255, message: "描述最多 255 字符" }]}
        >
          <Input.TextArea
            maxLength={255}
            rows={3}
            placeholder="留空则不写描述"
          />
        </Form.Item>
      </Form>
    </Modal>
  );
}

// ---- Edit modal -----------------------------------------------------------

interface EditTagModalProps {
  tag: AdminTagOut;
  onClose: () => void;
  onSaved: () => void;
}

function EditTagModal({ tag, onClose, onSaved }: EditTagModalProps) {
  const [form] = Form.useForm<AdminTagUpdateBody>();
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Initial values mirror the row so the test can read the prefilled name.
  const initial: AdminTagUpdateBody = {
    name: tag.name,
    description: tag.description ?? "",
  };

  async function handleSubmit() {
    setSubmitError(null);
    let values: AdminTagUpdateBody;
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    // 守 PE-06 REWORK-PE-06-INDEP #4 + INDEP-2 #1 — only send fields whose
    // normalized form value differs from the row's current value. RHS uses
    // ``||`` (not ``??``) for symmetric null/empty-string coalescing with
    // the LHS — backend Pydantic ``Optional[str]`` returns None for unset
    // (so ``""`` never actually shows up in ``tag.*``), but mirroring the
    // LHS keeps the comparison robust against any future code path that
    // hands us an empty string.
    const patch: AdminTagUpdateBody = {};
    if (values.name !== tag.name) patch.name = values.name;
    const desc = values.description || null;
    if (desc !== (tag.description || null)) patch.description = desc;

    if (Object.keys(patch).length === 0) {
      message.info("无变更");
      onClose();
      return;
    }

    setSubmitting(true);
    try {
      await updateAdminTag(tag.id, patch);
      message.success(`已保存：${values.name ?? tag.name}`);
      onSaved();
    } catch (err) {
      setSubmitError(formatError(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal
      title={`编辑标签：${tag.name}`}
      open
      onCancel={onClose}
      onOk={handleSubmit}
      okText="保存"
      cancelText="取消"
      confirmLoading={submitting}
      destroyOnClose
    >
      <Form
        form={form}
        layout="vertical"
        initialValues={initial}
        preserve={false}
      >
        {submitError && (
          <Alert
            type="error"
            message={submitError}
            style={{ marginBottom: 12 }}
          />
        )}
        <Form.Item
          name="name"
          label="名称"
          rules={[
            { required: true, message: "名称必填" },
            { max: 64, message: "名称最多 64 字符" },
          ]}
        >
          <Input maxLength={64} />
        </Form.Item>
        <Form.Item
          name="description"
          label="描述"
          rules={[{ max: 255, message: "描述最多 255 字符" }]}
        >
          <Input.TextArea maxLength={255} rows={3} />
        </Form.Item>
      </Form>
    </Modal>
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

export default AdminTagsPage;
