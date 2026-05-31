// TASK-PE-08 — 把标签绑定到某个 App（app→tags 反向）.
//
// antd Transfer：左列 = 全部标签，右列 = 当前已绑. 打开时 GET
// /admin/apps/{id}/tags 拉当前绑定；保存时 PUT replace-all. Transfer key
// 用 tag.id (UUID). 与 AdminTagsPage/BindAppsModal 对称（reverse direction）.

import { useEffect, useState } from "react";
import { Modal, Transfer, message } from "antd";
import { ApiError, getAppTags, replaceAppTags } from "@/lib/api";
import { useAdminTags } from "@/hooks/useAdminTags";

interface BindTagsModalProps {
  appId: string;
  appName: string;
  open: boolean;
  onClose: () => void;
  onSaved?: () => void;
}

interface TransferItem {
  key: string;
  title: string;
}

export function BindTagsModal({
  appId,
  appName,
  open,
  onClose,
  onSaved,
}: BindTagsModalProps) {
  const { data: allTags = [] } = useAdminTags();
  const [targetKeys, setTargetKeys] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    getAppTags(appId)
      .then((res) => {
        if (!cancelled) setTargetKeys(res.tag_ids);
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
  }, [open, appId]);

  async function handleOk() {
    setSaving(true);
    try {
      await replaceAppTags(appId, targetKeys);
      message.success("绑定已更新");
      onSaved?.();
      onClose();
    } catch (err) {
      message.error(formatErr(err));
    } finally {
      setSaving(false);
    }
  }

  const dataSource: TransferItem[] = allTags.map((t) => ({
    key: t.id,
    title: t.name,
  }));

  return (
    <Modal
      title={`绑定标签：${appName}`}
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
        titles={["可选标签", "已绑定"]}
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

export default BindTagsModal;
