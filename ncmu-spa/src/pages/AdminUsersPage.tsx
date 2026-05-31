// TASK-PE-06 — admin 用户管理 CRUD 页.
//
// antd Table + server-side pagination + 搜索 + "显示已停用" 开关 + 行级
// "编辑 modal" / "停用 Popconfirm" + 顶栏 "新建用户" 按钮.
//
// dev-login 路径不动 — 创建后弹 toast 提示如何 dev-login（user_id={uuid}）
// + 如需提为 admin 需手动编辑 .env NCMU_ADMIN_USER_IDS（plan §3 PE-06 Step 3
// 字面）.
//
// Tags 编辑 (PE-09 落地) — 编辑 modal 内联 Transfer，保存时调 replaceUserTags
// 真发 tag_ids 到后端 PUT /admin/users/{id}/tags (replace-all).

import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Form,
  Input,
  message,
  Modal,
  Popconfirm,
  Space,
  Switch,
  Table,
  Tag,
  Tooltip,
  Transfer,
  Typography,
} from "antd";
import { CopyOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import {
  ApiError,
  type AdminUserCreateBody,
  type AdminUserOut,
  type AdminUserUpdateBody,
  createAdminUser,
  deactivateAdminUser,
  getUserTags,
  replaceUserTags,
  updateAdminUser,
} from "@/lib/api";
import { useAdminUsers } from "@/hooks/useAdminUsers";
import { useAdminTags } from "@/hooks/useAdminTags";

const DEFAULT_PAGE_SIZE = 50;

export function AdminUsersPage() {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [searchInput, setSearchInput] = useState("");
  const [searchKey, setSearchKey] = useState<string>("");
  const [includeInactive, setIncludeInactive] = useState(false);
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [editingUser, setEditingUser] = useState<AdminUserOut | null>(null);

  const params = useMemo(
    () => ({
      page,
      size: pageSize,
      include_inactive: includeInactive,
      search: searchKey || undefined,
    }),
    [page, pageSize, includeInactive, searchKey],
  );

  const { data, isLoading, refetch } = useAdminUsers(params);

  // REWORK-PE-06-INDEP #5 — declared above ``columns`` so the array's
  // ``onConfirm`` reference is not relying on function hoisting (which
  // would silently break if this was later refactored to ``const``).
  async function handleDeactivate(row: AdminUserOut) {
    try {
      await deactivateAdminUser(row.id);
      message.success(`已停用：${row.name}`);
      refetch();
    } catch (err) {
      message.error(formatError(err));
    }
  }

  const columns: ColumnsType<AdminUserOut> = [
    {
      title: "姓名",
      dataIndex: "name",
      key: "name",
      render: (name: string, row) => (
        <Space>
          <span>{name}</span>
          {row.is_admin && <Tag color="gold">admin</Tag>}
          {!row.is_active && <Tag color="default">已停用</Tag>}
        </Space>
      ),
    },
    {
      title: "钉钉 userid",
      dataIndex: "dingtalk_userid",
      key: "dingtalk_userid",
      render: (val: string | null) => val ?? <Typography.Text type="secondary">—</Typography.Text>,
    },
    {
      title: "部门路径",
      dataIndex: "dept_path",
      key: "dept_path",
      render: (val: string | null) => val ?? <Typography.Text type="secondary">—</Typography.Text>,
    },
    {
      title: "user_id",
      dataIndex: "id",
      key: "id",
      render: (id: string) => (
        <Tooltip title={id}>
          <Typography.Text type="secondary" style={{ fontFamily: "monospace" }}>
            {id.slice(0, 8)}…
          </Typography.Text>
        </Tooltip>
      ),
    },
    {
      title: "操作",
      key: "actions",
      render: (_v, row) => (
        <Space>
          <Button size="small" onClick={() => setEditingUser(row)}>
            编辑
          </Button>
          {row.is_active && (
            <Popconfirm
              title="停用该用户？"
              description="软删除：保留数据库行（chat_sessions/审计），仅设 is_active=false。可在「显示已停用」中找回并恢复。"
              okText="确定"
              cancelText="取消"
              onConfirm={() => handleDeactivate(row)}
            >
              <Button size="small" danger>
                停用
              </Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ];

  return (
    <div>
      <Space style={{ marginBottom: 16 }} wrap>
        <Input.Search
          placeholder="搜索 姓名 / 钉钉 userid"
          allowClear
          enterButton="搜索"
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
          onSearch={(val) => {
            setSearchKey(val);
            setPage(1);
          }}
          style={{ width: 320 }}
        />
        <Space>
          <Switch
            checked={includeInactive}
            onChange={(checked) => {
              setIncludeInactive(checked);
              setPage(1);
            }}
            aria-label="显示已停用"
          />
          <span>显示已停用</span>
        </Space>
        <Button type="primary" onClick={() => setCreateModalOpen(true)}>
          新建用户
        </Button>
      </Space>

      <Table<AdminUserOut>
        rowKey="id"
        loading={isLoading}
        columns={columns}
        dataSource={data?.items ?? []}
        pagination={{
          current: page,
          pageSize,
          total: data?.total ?? 0,
          showSizeChanger: true,
          onChange: (p, ps) => {
            setPage(p);
            setPageSize(ps);
          },
        }}
      />

      <CreateUserModal
        open={createModalOpen}
        onClose={() => setCreateModalOpen(false)}
        onCreated={() => {
          setCreateModalOpen(false);
          refetch();
        }}
      />

      {editingUser && (
        <EditUserModal
          user={editingUser}
          onClose={() => setEditingUser(null)}
          onSaved={() => {
            setEditingUser(null);
            refetch();
          }}
        />
      )}
    </div>
  );
}

// ---- Create modal ---------------------------------------------------------

interface CreateUserModalProps {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}

function CreateUserModal({ open, onClose, onCreated }: CreateUserModalProps) {
  const [form] = Form.useForm<AdminUserCreateBody>();
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit() {
    setSubmitError(null);
    let values: AdminUserCreateBody;
    try {
      values = await form.validateFields();
    } catch {
      return; // Form-level validation rendered its own messages.
    }
    setSubmitting(true);
    try {
      const created = await createAdminUser({
        name: values.name,
        dingtalk_userid: values.dingtalk_userid || null,
        dept_path: values.dept_path || null,
      });
      // Plan §3 PE-06 Step 3 toast 字面（含 user_id + dev-login + admin 提权
      // 指引）— keep all 3 substrings so the test assertion stays specific.
      message.success(
        `用户已创建。用 user_id=${created.id} dev-login（如需提为 admin 编辑 .env NCMU_ADMIN_USER_IDS）`,
        8,
      );
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
      title="新建用户"
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
          <Alert type="error" message={submitError} style={{ marginBottom: 12 }} />
        )}
        <Form.Item
          name="name"
          label="姓名"
          rules={[{ required: true, message: "姓名必填" }]}
        >
          <Input maxLength={64} placeholder="例：李四" />
        </Form.Item>
        <Form.Item name="dingtalk_userid" label="钉钉 userid（可选）">
          <Input maxLength={64} placeholder="留空则不绑定钉钉账户" />
        </Form.Item>
        <Form.Item name="dept_path" label="部门路径（可选）">
          <Input maxLength={255} placeholder="例：/技术/后端" />
        </Form.Item>
      </Form>
    </Modal>
  );
}

// ---- Edit modal -----------------------------------------------------------

interface EditUserModalProps {
  user: AdminUserOut;
  onClose: () => void;
  onSaved: () => void;
}

function EditUserModal({ user, onClose, onSaved }: EditUserModalProps) {
  const [form] = Form.useForm<AdminUserUpdateBody>();
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  // TASK-PE-09 — user↔tag binding (many-to-many) edited inline via a Transfer
  // in this modal (plan §1127 "PE-09 时 enable"). The tag-side mirror is the
  // separate AdminTagsPage/BindUsersModal.
  const { data: allTags = [] } = useAdminTags();
  const [tagKeys, setTagKeys] = useState<string[]>([]);
  const [origTagKeys, setOrigTagKeys] = useState<string[]>([]);
  const [tagsLoading, setTagsLoading] = useState(false);

  // Initial values mirror the row so the test can read the prefilled name.
  const initial: AdminUserUpdateBody = {
    name: user.name,
    dingtalk_userid: user.dingtalk_userid ?? "",
    dept_path: user.dept_path ?? "",
    is_active: user.is_active,
  };

  // Load the user's current tag bindings when the modal opens (it mounts
  // fresh per edit, so this fires once). On failure we keep ``tagsLoading``
  // true so the disabled Transfer can't be saved as an empty set (which the
  // replace-all PUT would interpret as "clear all tags").
  useEffect(() => {
    let cancelled = false;
    setTagsLoading(true);
    getUserTags(user.id)
      .then((res) => {
        if (cancelled) return;
        setTagKeys(res.tag_ids);
        setOrigTagKeys(res.tag_ids);
        setTagsLoading(false);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        message.error(formatError(err));
      });
    return () => {
      cancelled = true;
    };
  }, [user.id]);

  async function handleSubmit() {
    setSubmitError(null);
    let values: AdminUserUpdateBody;
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    // REWORK-PE-06-INDEP #4 — only send fields whose normalized form
    // value differs from the row's current value. Backend already does
    // ``exclude_unset=True``; omitting unchanged keys keeps the wire
    // request narrowly scoped to the admin's actual intent.
    const patch: AdminUserUpdateBody = {};
    if (values.name !== user.name) patch.name = values.name;
    // REWORK-PE-06-INDEP-2 #1 — RHS uses `||` (not `??`) for symmetric
    // null/empty-string coalescing with the LHS. Backend Pydantic
    // Optional[str] returns None for unset (so `""` never actually shows
    // up in `user.*`), but mirroring the LHS keeps the comparison robust
    // against any future code path that hands us an empty string.
    const dt = values.dingtalk_userid || null;
    if (dt !== (user.dingtalk_userid || null)) patch.dingtalk_userid = dt;
    const dp = values.dept_path || null;
    if (dp !== (user.dept_path || null)) patch.dept_path = dp;
    if (values.is_active !== user.is_active) patch.is_active = values.is_active;

    // TASK-PE-09 — tags are a sibling many-to-many edit; compare the
    // Transfer's current right-column set against what we loaded on open
    // (order-independent) so an unchanged tag set doesn't fire a PUT.
    const tagsChanged =
      tagKeys.length !== origTagKeys.length ||
      [...tagKeys].sort().join() !== [...origTagKeys].sort().join();

    if (Object.keys(patch).length === 0 && !tagsChanged) {
      message.info("无变更");
      onClose();
      return;
    }

    setSubmitting(true);
    try {
      if (Object.keys(patch).length > 0) {
        await updateAdminUser(user.id, patch);
      }
      if (tagsChanged) {
        await replaceUserTags(user.id, tagKeys);
      }
      message.success(`已保存：${values.name ?? user.name}`);
      onSaved();
    } catch (err) {
      setSubmitError(formatError(err));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleCopyId() {
    try {
      await navigator.clipboard.writeText(user.id);
      message.success("已复制 user_id");
    } catch {
      message.error("复制失败 — 请手动选择并复制");
    }
  }

  return (
    <Modal
      title={`编辑用户：${user.name}`}
      open
      onCancel={onClose}
      onOk={handleSubmit}
      okText="保存"
      cancelText="取消"
      confirmLoading={submitting}
      destroyOnClose
    >
      <Form form={form} layout="vertical" initialValues={initial} preserve={false}>
        {submitError && (
          <Alert type="error" message={submitError} style={{ marginBottom: 12 }} />
        )}
        <Form.Item label="user_id">
          <Space>
            <Typography.Text style={{ fontFamily: "monospace" }}>
              {user.id}
            </Typography.Text>
            <Button
              size="small"
              icon={<CopyOutlined />}
              onClick={handleCopyId}
            >
              复制 UUID
            </Button>
          </Space>
        </Form.Item>
        <Form.Item
          name="name"
          label="姓名"
          rules={[{ required: true, message: "姓名必填" }]}
        >
          <Input maxLength={64} />
        </Form.Item>
        <Form.Item name="dingtalk_userid" label="钉钉 userid">
          <Input maxLength={64} />
        </Form.Item>
        <Form.Item name="dept_path" label="部门路径">
          <Input maxLength={255} />
        </Form.Item>
        <Form.Item label="状态" name="is_active" valuePropName="checked">
          <Switch checkedChildren="启用" unCheckedChildren="停用" />
        </Form.Item>
        <Form.Item label="标签">
          <Transfer<{ key: string; title: string }>
            dataSource={allTags.map((t) => ({ key: t.id, title: t.name }))}
            targetKeys={tagKeys}
            onChange={(keys) => setTagKeys(keys.map(String))}
            render={(item) => item.title}
            showSearch
            listStyle={{ width: 220, height: 280 }}
            titles={["可选标签", "已绑定"]}
            disabled={tagsLoading}
          />
        </Form.Item>
      </Form>
    </Modal>
  );
}

// ---- helpers --------------------------------------------------------------

function formatError(err: unknown): string {
  if (err instanceof ApiError) {
    return err.message || `操作失败（HTTP ${err.status}${err.code ? ` / code ${err.code}` : ""}）`;
  }
  if (err instanceof Error) return err.message;
  return String(err);
}

export default AdminUsersPage;
