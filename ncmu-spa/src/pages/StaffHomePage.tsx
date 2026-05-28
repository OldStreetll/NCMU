// TASK-PE-03: 员工首页 /staff — Apps 按 mode 分 5 tab 展示。替换 PE-01
// 在 routes.tsx 内的 StaffHomePlaceholder。Path C (Boss 2026-05-28): plan
// §3 字面用了 useQuery + @tanstack/react-query 但项目未引入，本页改用
// useState + useEffect + fetchApps 项目既有范式（match useMyApplications
// 第 33-101 行 + useWorkflowRuns.ts:8 注释 "mimic TanStack 不安装真依赖"）。
// `icon` 是 AppOut 不存在的幻字段，本页用 mode-based emoji fallback。
//
// 5 tab 划分（plan §3 line 601-607）：
//   - 全部：所有可访问 App
//   - 对话 App：mode in {chat, agent-chat}
//   - 工作流 App：mode in {workflow, advanced-chat}
//   - 一次性生成：mode === "completion"
//   - KB：name 以 "[KB]" 前缀（覆盖 mode 维度 — [KB] mode=chat 也在 KB tab）
//
// 卡片点击按 mode 分发到 5 个 mode 页（plan §3 line 609-615 + routes.tsx
// 89-103 已有 5 route 兜底）。<Tabs destroyOnHidden> 保证非活动 tab 的
// pane 不进 DOM —— 否则 antd 默认渲染所有 pane（hidden via CSS）会让
// 测试 queryByText("报销助手") 错误命中切走的 tab。

import { useEffect, useState } from "react";
import { Card, Empty, Skeleton, Tabs } from "antd";
import { Link } from "react-router-dom";
import { fetchApps, type App } from "@/lib/api";

const TAB_FILTERS: Record<string, (a: App) => boolean> = {
  all: () => true,
  chat: (a) => a.mode === "chat" || a.mode === "agent-chat",
  workflow: (a) => a.mode === "workflow" || a.mode === "advanced-chat",
  completion: (a) => a.mode === "completion",
  kb: (a) => a.name.startsWith("[KB]"),
};

const TAB_LABELS: Array<{ key: string; label: string }> = [
  { key: "all", label: "全部" },
  { key: "chat", label: "对话 App" },
  { key: "workflow", label: "工作流 App" },
  { key: "completion", label: "一次性生成" },
  { key: "kb", label: "KB" },
];

const ROUTE_FOR_MODE: Record<string, (id: string) => string> = {
  chat: (id) => `/chat?app_id=${id}`,
  "agent-chat": (id) => `/apps/${id}/agent`,
  "advanced-chat": (id) => `/apps/${id}/chatflow`,
  workflow: (id) => `/apps/${id}/workflow`,
  completion: (id) => `/apps/${id}/completion`,
};

function routeFor(app: App): string {
  const builder = ROUTE_FOR_MODE[app.mode];
  return builder ? builder(app.id) : `/chat?app_id=${app.id}`;
}

// Mode-based icon fallback — replaces the `icon` field plan §3 hallucinated.
// `[KB]` name prefix takes precedence over mode (KB apps render the 📚 even
// when mode=chat / workflow).
function iconFor(app: App): string {
  if (app.name.startsWith("[KB]")) return "📚";
  switch (app.mode) {
    case "chat":
    case "agent-chat":
      return "💬";
    case "workflow":
    case "advanced-chat":
      return "⚙️";
    case "completion":
      return "📝";
    default:
      return "🔧";
  }
}

export function StaffHomePage() {
  const [apps, setApps] = useState<App[] | undefined>(undefined);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchApps()
      .then((rows) => {
        if (cancelled) return;
        setApps(rows);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err : new Error(String(err)));
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (isLoading) return <Skeleton active />;
  if (error) {
    return (
      <Empty
        description={`加载失败：${error.message}`}
        style={{ marginTop: 48 }}
      />
    );
  }
  const all = apps ?? [];
  return (
    <Tabs
      defaultActiveKey="all"
      destroyOnHidden
      items={TAB_LABELS.map(({ key, label }) => ({
        key,
        label,
        children: <Grid apps={all.filter(TAB_FILTERS[key])} />,
      }))}
    />
  );
}

function Grid({ apps }: { apps: App[] }) {
  if (apps.length === 0) {
    return <Empty description="这个分类下你暂无可访问 App" />;
  }
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))",
        gap: 16,
      }}
    >
      {apps.map((app) => (
        <Link key={app.id} to={routeFor(app)}>
          <Card hoverable style={{ height: 160 }}>
            <Card.Meta
              avatar={<span style={{ fontSize: 32 }}>{iconFor(app)}</span>}
              title={app.name}
              description={app.description?.slice(0, 60) ?? ""}
            />
          </Card>
        </Link>
      ))}
    </div>
  );
}

export default StaffHomePage;
