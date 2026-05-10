import { Empty, List, Skeleton, Tag, Typography } from "antd";
import {
  useWorkflowRunList,
  type WorkflowRunSummary,
} from "@/hooks/useWorkflowRuns";

export interface RunHistoryListProps {
  appId: string;
  onRowClick?: (runId: string) => void;
}

// TASK-FIX-I1: full 8-status coverage. The terminal-status set is the
// `RunStatus` Literal at backend `ncmu-backend/src/ncmu_backend/workflow/
// crud.py:29-32` (succeeded / failed / stopped / exception / timeout /
// cancelled / db_error); `running` is the initial state written at
// `crud.py:50`. Pre-fix only 5 were mapped, so timeout / cancelled /
// db_error fell through to "default" → three semantically distinct
// failure modes rendered as indistinguishable grey tags (INDEP-70b
// fresh-eyes, NIT-70b-4 → I-1).
//
// Palette intent:
//   - succeeded:  green   — terminal success.
//   - failed:     red     — hard logical failure.
//   - stopped:    orange  — interrupted by the system mid-flight.
//   - exception:  magenta — uncaught code-level exception.
//   - timeout:    gold    — retry-able / latency miss (yellow warning).
//   - cancelled:  default — explicit user cancel, neutral grey.
//   - db_error:   volcano — persistence-layer failure (high contrast).
//   - running:    blue    — initial / non-terminal.
// Antd preset names; the trailing `?? "default"` fallback below is
// deliberately preserved so any future RunStatus literal added
// backend-side keeps rendering rather than crashing.
const STATUS_COLOR: Record<string, string> = {
  succeeded: "green",
  failed: "red",
  stopped: "orange",
  exception: "magenta",
  timeout: "gold",
  cancelled: "default",
  db_error: "volcano",
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
