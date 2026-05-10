import { Empty, Tag, Typography } from "antd";
import type {
  NcmuSseEvent,
  NodeFinishedData,
  NodeStartedData,
} from "@/lib/sse-types";

const { Text } = Typography;

export interface NodeTraceViewerProps {
  nodeTrace: NcmuSseEvent[];
}

// Antd Tag preset colors per backend NodeFinishedData.status (4 terminal values
// per H2 / spike §3.7). "failed" maps to red — that's the visual contract the
// AC#5 (c) test asserts on.
const STATUS_COLOR: Record<NodeFinishedData["status"], string> = {
  succeeded: "green",
  failed: "red",
  stopped: "default",
  exception: "volcano",
};

export function NodeTraceViewer({ nodeTrace }: NodeTraceViewerProps) {
  if (nodeTrace.length === 0) {
    return (
      <div data-testid="node-trace-empty" style={{ padding: 16 }}>
        <Empty description="暂无节点" />
      </div>
    );
  }

  return (
    <ul
      data-testid="node-trace-list"
      style={{ listStyle: "none", padding: 0, margin: 0 }}
    >
      {nodeTrace.map((evt, idx) => {
        const key = `${evt.run_id}-${idx}`;

        if (evt.event_type === "node_started") {
          const data = evt.data as NodeStartedData;
          return (
            <li
              key={key}
              data-testid="node-trace-row"
              data-event-type="node_started"
              style={{
                borderBottom: "1px solid #f0f0f0",
                padding: "8px 12px",
              }}
            >
              <Tag color="processing">开始</Tag>
              <Text strong>{data.title ?? data.node_id}</Text>
              <Text type="secondary" style={{ marginLeft: 8, fontSize: 12 }}>
                {data.node_type}
              </Text>
            </li>
          );
        }

        if (evt.event_type === "node_finished") {
          const data = evt.data as NodeFinishedData;
          const color = STATUS_COLOR[data.status] ?? "default";
          return (
            <li
              key={key}
              data-testid="node-trace-row"
              data-event-type="node_finished"
              data-status={data.status}
              style={{
                borderBottom: "1px solid #f0f0f0",
                padding: "8px 12px",
              }}
            >
              <Tag color={color} data-testid={`node-status-${data.status}`}>
                {data.status}
              </Tag>
              <Text strong>{data.node_id}</Text>
              <Text type="secondary" style={{ marginLeft: 8, fontSize: 12 }}>
                {data.node_type}
              </Text>
              {typeof data.elapsed_ms === "number" && (
                <Text type="secondary" style={{ marginLeft: 8, fontSize: 12 }}>
                  {data.elapsed_ms}ms
                </Text>
              )}
              {data.error && (
                <Text
                  type="danger"
                  style={{ display: "block", marginTop: 4 }}
                  data-testid="node-error-msg"
                >
                  {data.error}
                </Text>
              )}
            </li>
          );
        }

        // Other event types (workflow_finished / agent_thought / tool_call /
        // ping / error) belong to siblings — not this viewer's concern.
        return null;
      })}
    </ul>
  );
}

export default NodeTraceViewer;
