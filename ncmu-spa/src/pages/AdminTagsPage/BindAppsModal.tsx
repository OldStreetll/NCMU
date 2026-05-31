// TASK-PE-08 — 把 App 绑定到某个标签（tag→apps 方向）.
//
// antd Transfer：左列 = 全部 App（含已停用 / admin 视角），右列 = 当前已绑.
// 打开时 GET /admin/tags/{id}/apps 拉当前绑定填右列；保存时 PUT replace-all.
//
// Transfer key 用 ``a.dify_app_id``（NOT plan 字面的 ``a.id`` —— AdminAppOut
// 无 id 字段 / PK 是 dify_app_id String(64)，Boss 2026-05-29 拍板）.

import { useEffect, useState } from "react";
import { Modal, Transfer, message } from "antd";
import { ApiError, getTagApps, replaceTagApps } from "@/lib/api";
import { useAdminApps } from "@/hooks/useAdminApps";

interface BindAppsModalProps {
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

export function BindAppsModal({
  tagId,
  tagName,
  open,
  onClose,
  onSaved,
}: BindAppsModalProps) {
  // Admin binds to ANY app incl. deactivated → include_inactive.
  const { data: allApps = [] } = useAdminApps({ includeInactive: true });
  const [targetKeys, setTargetKeys] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  // Fetch current bindings each time the modal opens (fresh, not stale).
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    getTagApps(tagId)
      .then((res) => {
        if (!cancelled) setTargetKeys(res.app_ids);
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
      await replaceTagApps(tagId, targetKeys);
      message.success("绑定已更新");
      onSaved?.();
      onClose();
    } catch (err) {
      message.error(formatErr(err));
    } finally {
      setSaving(false);
    }
  }

  const dataSource: TransferItem[] = allApps.map((a) => ({
    key: a.dify_app_id,
    title: a.name,
    description: a.mode,
  }));

  return (
    <Modal
      title={`绑定 App：${tagName}`}
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
        titles={["可选 App", "已绑定"]}
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

export default BindAppsModal;
