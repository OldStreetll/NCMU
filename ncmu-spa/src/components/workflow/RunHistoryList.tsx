import { Empty, List, Skeleton, Tag, Typography } from "antd";
import {
  useWorkflowRunList,
  type WorkflowRunSummary,
} from "@/hooks/useWorkflowRuns";

export interface RunHistoryListProps {
  appId: string;
  onRowClick?: (runId: string) => void;
}

const STATUS_COLOR: Record<string, string> = {
  succeeded: "green",
  failed: "red",
  stopped: "orange",
  exception: "magenta",
  running: "blue",
};

export function RunHistoryList({ appId, onRowClick }: RunHistoryListProps) {
  const { data, isLoading, isError, error } = useWorkflowRunList(appId);

  if (isLoading) {
    return (
      <div data-testid="runs-skeleton" style={{ padding: 16 }}>
        <Skeleton active />
      </div>
    );
  }

  if (isError) {
    return (
      <div style={{ padding: 16, color: "#cf1322" }}>
        加载运行历史失败：{error?.message ?? "未知错误"}
      </div>
    );
  }

  const rows = data ?? [];
  if (rows.length === 0) {
    return <Empty description="暂无运行历史" style={{ padding: 16 }} />;
  }

  return (
    <List<WorkflowRunSummary>
      dataSource={rows}
      renderItem={(run) => (
        <List.Item
          key={run.run_id}
          data-testid={`run-row-${run.run_id}`}
          onClick={() => onRowClick?.(run.run_id)}
          style={{ cursor: onRowClick ? "pointer" : "default", padding: "8px 16px" }}
        >
          <List.Item.Meta
            title={
              <Typography.Text code copyable={false}>
                {run.run_id.slice(0, 8)}
              </Typography.Text>
            }
            description={
              <span>
                <Tag color={STATUS_COLOR[run.status] ?? "default"}>{run.status}</Tag>
                <span style={{ color: "#888", fontSize: 12 }}>{run.started_at}</span>
              </span>
            }
          />
        </List.Item>
      )}
    />
  );
}
