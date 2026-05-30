// TASK-PE-10 — admin DSL 导出页（单 App / 多 App ZIP 打包下载）.
//
// antd Table 多选 App（默认全选所有候选）+ include_secret checkbox（默认
// 不勾 + 红字警告）+ [打包下载 ZIP] 按钮 → POST /admin/apps/dsl-export →
// 浏览器下载 ZIP。导出期间 Spin mask（N 个 App 同步透传 Dify Console 可能
// 耗时）。
//
// 默认全选：PE-07 的 ``is_active`` 列尚未并入本分支，故不做 active 过滤，
// 候选 = dify_apps 缓存全量（admin 已 /sync_apps 同步）。
//
// antd v5 仅 2 CJK 字符按钮 autoInsertSpaceInButton 默认开启 — 测试用
// /\s*/ 容错（守 feedback_antd_v5_cjk_button_space_pitfall）。

import { useEffect, useState } from "react";
import {
  Alert,
  Button,
  Checkbox,
  message,
  Space,
  Spin,
  Table,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import type { Key } from "react";
import {
  ApiError,
  type DslCandidate,
  downloadDslZip,
  listDslCandidates,
} from "@/lib/api";

export function AdminDslExportPage() {
  const [apps, setApps] = useState<DslCandidate[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Key[]>([]);
  const [includeSecret, setIncludeSecret] = useState(false);
  const [downloading, setDownloading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    listDslCandidates()
      .then((rows) => {
        if (cancelled) return;
        setApps(rows);
        // 默认全选所有候选（不依赖 is_active）。
        setSelected(rows.map((r) => r.dify_app_id));
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setLoadError(formatError(err));
      })
      .finally(() => {
        if (cancelled) return;
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleExport() {
    if (selected.length === 0) {
      message.warning("请至少选择一个 App");
      return;
    }
    setDownloading(true);
    try {
      await downloadDslZip(
        selected.map((k) => String(k)),
        includeSecret,
      );
      message.success(`已导出 ${selected.length} 个 App 的 DSL`);
    } catch (err) {
      message.error(formatError(err));
    } finally {
      setDownloading(false);
    }
  }

  const columns: ColumnsType<DslCandidate> = [
    {
      title: "App 名称",
      dataIndex: "name",
      key: "name",
    },
    {
      title: "模式",
      dataIndex: "mode",
      key: "mode",
      width: 160,
    },
    {
      title: "Dify App ID",
      dataIndex: "dify_app_id",
      key: "dify_app_id",
      render: (val: string) => (
        <Typography.Text code copyable={{ text: val }}>
          {val}
        </Typography.Text>
      ),
    },
  ];

  return (
    <div>
      <Typography.Title level={4}>DSL 导出</Typography.Title>
      <Typography.Paragraph type="secondary">
        勾选要导出的 App，点击「打包下载」生成包含各 App DSL（YAML）的 ZIP，
        附带 MANIFEST.json 清单。
      </Typography.Paragraph>

      {loadError && (
        <Alert
          type="error"
          showIcon
          message={loadError}
          style={{ marginBottom: 16 }}
        />
      )}

      <Spin spinning={downloading} tip="正在打包导出…">
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          <Space wrap>
            <Button
              type="primary"
              onClick={handleExport}
              disabled={loading || selected.length === 0}
              loading={downloading}
            >
              打包下载 ZIP（{selected.length}）
            </Button>
            <Checkbox
              checked={includeSecret}
              onChange={(e) => setIncludeSecret(e.target.checked)}
            >
              包含密钥（include_secret）
            </Checkbox>
          </Space>

          {includeSecret && (
            <Typography.Text type="danger" data-testid="include-secret-warning">
              ⚠ 警告：导出内容将包含 API 密钥等敏感信息，请妥善保管，切勿外泄。
            </Typography.Text>
          )}

          <Table<DslCandidate>
            rowKey="dify_app_id"
            loading={loading}
            columns={columns}
            dataSource={apps}
            pagination={false}
            rowSelection={{
              selectedRowKeys: selected,
              onChange: (keys) => setSelected(keys),
            }}
          />
        </Space>
      </Spin>
    </div>
  );
}

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

export default AdminDslExportPage;
