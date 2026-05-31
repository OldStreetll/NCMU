// TASK-PE-11 — admin DSL 导入页（单 YAML / ZIP 拖拽上传 → 逐 App 导入）.
//
// antd Upload.Dragger（.yaml/.yml/.zip）→ 上传中 Spin → 完成 Modal：
//   成功列表（点击跳 /admin/apps/{id}）+ 失败列表（字段级 error_code /
//   plugin_missing / yaml_error）+ KB 重挂提示（每次都显示）+ 跳
//   /admin/kb-configs 链接。
//
// 后端逐 App 部分成功也返 200，所以"导入成功"与"有失败行"可并存——Modal
// 同时渲染两段，不用 success/error 二选一。

import { useState } from "react";
import {
  Alert,
  List,
  message,
  Modal,
  Spin,
  Tag,
  Typography,
  Upload,
} from "antd";
import type { UploadProps } from "antd";
import { InboxOutlined } from "@ant-design/icons";
import { Link } from "react-router-dom";
import {
  ApiError,
  type DslImportFailed,
  type DslImportResult,
  importDsl,
} from "@/lib/api";

const ERROR_LABEL: Record<string, string> = {
  YAML_PARSE: "YAML 解析失败",
  DIFY_IMPORT: "Dify 导入失败",
  PLUGIN_MISSING: "缺少插件依赖",
};

export function AdminDslImportPage() {
  const [importing, setImporting] = useState(false);
  const [result, setResult] = useState<DslImportResult | null>(null);

  async function handleFile(file: File): Promise<void> {
    setImporting(true);
    try {
      const res = await importDsl(file);
      setResult(res);
    } catch (err) {
      message.error(formatError(err));
    } finally {
      setImporting(false);
    }
  }

  // beforeUpload returning false keeps antd from auto-POSTing — we drive the
  // upload ourselves via importDsl (the request needs the JWT + our own
  // result handling). LIST_IGNORE keeps the fileList empty so re-imports work.
  const uploadProps: UploadProps = {
    accept: ".yaml,.yml,.zip",
    multiple: false,
    showUploadList: false,
    beforeUpload: (file) => {
      void handleFile(file as unknown as File);
      return Upload.LIST_IGNORE;
    },
  };

  return (
    <div>
      <Typography.Title level={4}>DSL 导入</Typography.Title>
      <Typography.Paragraph type="secondary">
        拖拽或选择 App DSL 文件（单个 .yaml/.yml，或包含多个 YAML 的 .zip）。
        单 YAML ≤ 5MB、ZIP ≤ 20MB。导入后会自动同步 App 列表。
      </Typography.Paragraph>

      <Spin spinning={importing} tip="正在导入…">
        <Upload.Dragger {...uploadProps} data-testid="dsl-import-dragger">
          <p className="ant-upload-drag-icon">
            <InboxOutlined />
          </p>
          <p className="ant-upload-text">点击或拖拽 YAML / ZIP 到此处导入</p>
          <p className="ant-upload-hint">
            支持单个 App 的 .yaml/.yml，或多个 App 打包的 .zip
          </p>
        </Upload.Dragger>
      </Spin>

      <ImportResultModal result={result} onClose={() => setResult(null)} />
    </div>
  );
}

// ---- result modal ---------------------------------------------------------

interface ImportResultModalProps {
  result: DslImportResult | null;
  onClose: () => void;
}

function ImportResultModal({ result, onClose }: ImportResultModalProps) {
  if (!result) return null;
  const { total, succeeded, failed } = result;

  return (
    <Modal
      title={`导入结果（共 ${total} 个 App：成功 ${succeeded.length} / 失败 ${failed.length}）`}
      open
      onCancel={onClose}
      onOk={onClose}
      okText="完成"
      cancelButtonProps={{ style: { display: "none" } }}
      width={680}
      destroyOnClose
    >
      {/* KB 重挂提示 —— 每次导入完成都显示（DSL 不含知识库绑定） */}
      <Alert
        type="warning"
        showIcon
        style={{ marginBottom: 16 }}
        data-testid="kb-remount-notice"
        message="导入的 App 需重新挂载知识库"
        description={
          <>
            DSL 不包含知识库绑定关系，导入后请到{" "}
            <Link to="/admin/kb-configs">知识库配置</Link>{" "}
            为新 App 重新挂载对应知识库。
          </>
        }
      />

      {succeeded.length > 0 && (
        <List
          size="small"
          header={<Typography.Text strong>成功</Typography.Text>}
          dataSource={succeeded}
          data-testid="import-succeeded-list"
          renderItem={(s) => (
            <List.Item
              actions={[
                <Link key="open" to={`/admin/apps/${s.app_id}`}>
                  查看
                </Link>,
              ]}
            >
              <Typography.Text>{s.name}</Typography.Text>{" "}
              <Typography.Text type="secondary">（{s.filename}）</Typography.Text>
            </List.Item>
          )}
        />
      )}

      {failed.length > 0 && (
        <List
          size="small"
          style={{ marginTop: 12 }}
          header={<Typography.Text strong type="danger">失败</Typography.Text>}
          dataSource={failed}
          data-testid="import-failed-list"
          renderItem={(f) => <FailedRow row={f} />}
        />
      )}
    </Modal>
  );
}

function FailedRow({ row }: { row: DslImportFailed }) {
  return (
    <List.Item>
      <div style={{ width: "100%" }}>
        <Tag color="error">{ERROR_LABEL[row.error_code] ?? row.error_code}</Tag>
        <Typography.Text>{row.filename}</Typography.Text>
        {row.error_message && (
          <div>
            <Typography.Text type="secondary">{row.error_message}</Typography.Text>
          </div>
        )}
        {row.error_code === "PLUGIN_MISSING" &&
          Array.isArray(row.plugin_missing) && (
            <div data-testid="plugin-missing-detail">
              <Typography.Text type="warning">
                缺少插件：{row.plugin_missing.length} 个（App 已创建，安装插件后可用）
              </Typography.Text>
            </div>
          )}
      </div>
    </List.Item>
  );
}

function formatError(err: unknown): string {
  if (err instanceof ApiError) {
    return (
      err.message ||
      `导入失败（HTTP ${err.status}${err.code ? ` / code ${err.code}` : ""}）`
    );
  }
  if (err instanceof Error) return err.message;
  return String(err);
}

export default AdminDslImportPage;
