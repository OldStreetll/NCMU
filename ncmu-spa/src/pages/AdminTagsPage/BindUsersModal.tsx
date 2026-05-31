// TASK-PE-09 — 把用户绑定到某个标签（tag→users 方向）.
//
// antd Transfer：左列 = 全部用户（含已停用 / admin 视角），右列 = 当前已绑.
// 打开时 GET /admin/tags/{id}/users 拉当前绑定填右列；保存时 PUT replace-all.
//
// Transfer key 用 ``u.id``（users.id UUID）. 镜像 AdminTagsPage/BindAppsModal
// （reverse direction：users 而非 apps；同 user_tags join 表）.

import { useEffect, useState } from "react";
import { Modal, Transfer, message } from "antd";
import { ApiError, getTagUsers, replaceTagUsers } from "@/lib/api";
import { useAdminUsers } from "@/hooks/useAdminUsers";

interface BindUsersModalProps {
  tagId: string;
  tagName: string;
  open: boolean;
  onClose: () => void;
  onSaved?: () => void;
}

interface TransferItem {
  key: string;
  title: string;
  description?: string;
}

export function BindUsersModal({
  tagId,
  tagName,
  open,
  onClose,
  onSaved,
}: BindUsersModalProps) {
  // Admin binds to ANY user incl. deactivated → include_inactive. Single page
  // up to 200 (mirror PE-08 BindAppsModal's single-call assumption; pagination
  // is a future concern once an org exceeds 200 users).
  const { data } = useAdminUsers({ page: 1, size: 200, include_inactive: true });
  const allUsers = data?.items ?? [];
  const [targetKeys, setTargetKeys] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  // Fetch current bindings each time the modal opens (fresh, not stale).
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    getTagUsers(tagId)
      .then((res) => {
        if (!cancelled) setTargetKeys(res.user_ids);
      })
      .catch((err: unknown) => {
        if (!cancelled) message.error(formatErr(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, tagId]);

  async function handleOk() {
    setSaving(true);
    try {
      await replaceTagUsers(tagId, targetKeys);
      message.success("绑定已更新");
      onSaved?.();
      onClose();
    } catch (err) {
      message.error(formatErr(err));
    } finally {
      setSaving(false);
    }
  }

  const dataSource: TransferItem[] = allUsers.map((u) => ({
    key: u.id,
    title: u.name,
    description: u.dingtalk_userid ?? undefined,
  }));

  return (
    <Modal
      title={`绑定用户：${tagName}`}
      open={open}
      onCancel={onClose}
      onOk={handleOk}
      confirmLoading={saving}
      okText="保存"
      cancelText="取消"
      width={640}
      destroyOnClose
    >
      <Transfer<TransferItem>
        dataSource={dataSource}
        targetKeys={targetKeys}
        onChange={(keys) => setTargetKeys(keys.map(String))}
        render={(item) => item.title}
        showSearch
        listStyle={{ width: 280, height: 360 }}
        titles={["可选用户", "已绑定"]}
        disabled={loading}
      />
    </Modal>
  );
}

function formatErr(err: unknown): string {
  if (err instanceof ApiError) {
    return (
      err.message ||
      `操作失败（HTTP ${err.status}${err.code ? ` / code ${err.code}` : ""}）`
    );
  }
  if (err instanceof Error) return err.message;
  return String(err);
}

export default BindUsersModal;
