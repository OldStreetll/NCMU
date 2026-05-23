// TASK-PC3-C (Phase 2C) — AppKbContentPanel: KB document list collapse panel.
//
// errata-13 §2.5 UI mockup + plan §4.8 AC#1-3 r3 INDEP-2 路径 A 简化设计.
//
// Single prop ``{appId}`` — the panel does NOT inspect mode/tags (ChatPage
// is already the single entry for [KB] chat apps; non-chat modes mount no
// panel this phase, plan §4.8 r3 INDEP-2 C-INDEP2-1).
//
// Lazy loading: ``useKbFiles(appId, enabled)`` is wired with ``enabled`` =
// expanded flag, so the file-list fetch is paid only after the user opens
// the collapse (plan §4.8 AC#1 字面 "懒加载 / 不进页面就调端点").
//
// testid 字面 (plan §4.8 AC#8):
//   app-kb-content-panel
//   app-kb-content-list
//   app-kb-content-file-item-{file_id}
//   app-kb-content-download-button-{file_id}
import { useState } from "react";
import { Button, Collapse, Empty, List, Spin } from "antd";
import { getKbFileDownloadUrl, type KbFile } from "@/lib/api";
import { useKbFiles } from "@/components/AppKbContentPanel/hooks/useKbFiles";

const PANEL_KEY = "kb";

// plan §4.8 AC#2 字面 — bytes < 1 MiB 用 KB / ≥ 1 MiB 用 MB / 保留 1 位小数.
// 二进制单位（1024）与 backend FastGPTReadOnlyClient size_bytes int 一致。
export function humanSize(bytes: number): string {
  const MB = 1024 * 1024;
  if (bytes < MB) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  return `${(bytes / MB).toFixed(1)} MB`;
}

interface AppKbContentPanelProps {
  appId: string;
}

function FileRow({ appId, file }: { appId: string; file: KbFile }) {
  const handleDownload = () => {
    window.open(getKbFileDownloadUrl(appId, file.file_id), "_self");
  };
  return (
    <List.Item
      data-testid={`app-kb-content-file-item-${file.file_id}`}
      actions={[
        <Button
          key="download"
          type="link"
          size="small"
          data-testid={`app-kb-content-download-button-${file.file_id}`}
          onClick={handleDownload}
        >
          ⬇ 下载
        </Button>,
      ]}
    >
      <span>
        {file.original_filename} ({humanSize(file.size_bytes)})
      </span>
    </List.Item>
  );
}

function PanelContent({ appId, expanded }: { appId: string; expanded: boolean }) {
  const { data, isLoading, isError } = useKbFiles(appId, expanded);

  if (isLoading && data === undefined) {
    return (
      <div style={{ padding: "16px 0", textAlign: "center" }}>
        <Spin />
      </div>
    );
  }
  if (isError && data === undefined) {
    return <Empty description="加载失败" />;
  }
  if (!data || data.length === 0) {
    return <Empty description="暂无文档" />;
  }
  return (
    <List
      data-testid="app-kb-content-list"
      size="small"
      dataSource={data}
      renderItem={(file) => <FileRow appId={appId} file={file} />}
    />
  );
}

export default function AppKbContentPanel({ appId }: AppKbContentPanelProps) {
  const [expanded, setExpanded] = useState<boolean>(false);

  return (
    <div data-testid="app-kb-content-panel">
      <Collapse
        ghost
        activeKey={expanded ? [PANEL_KEY] : []}
        onChange={(keys) => {
          const next = Array.isArray(keys) ? keys : [keys];
          setExpanded(next.includes(PANEL_KEY));
        }}
        items={[
          {
            key: PANEL_KEY,
            label: "知识库内容",
            children: <PanelContent appId={appId} expanded={expanded} />,
          },
        ]}
      />
    </div>
  );
}
