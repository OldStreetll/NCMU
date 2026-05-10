import { Empty, Tag, Typography } from "antd";
import type { AgentThoughtData } from "@/lib/sse-types";

const { Text, Paragraph } = Typography;

export interface AgentThoughtTimelineProps {
  thoughts: AgentThoughtData[];
}

export function AgentThoughtTimeline({ thoughts }: AgentThoughtTimelineProps) {
  if (thoughts.length === 0) {
    return (
      <div data-testid="agent-thought-empty" style={{ padding: 16 }}>
        <Empty description="暂无思考" />
      </div>
    );
  }

  return (
    <ol
      data-testid="agent-thought-list"
      style={{ listStyle: "none", padding: 0, margin: 0 }}
    >
      {thoughts.map((t, idx) => (
        <li
          key={idx}
          data-testid="agent-thought-row"
          style={{
            borderLeft: "2px solid #1677ff",
            padding: "8px 12px",
            marginBottom: 8,
          }}
        >
          <Paragraph style={{ marginBottom: 4 }}>
            <Text strong>思考：</Text>
            {t.thought}
          </Paragraph>
          {t.tool_name && (
            <Tag
              data-testid="agent-thought-tool"
              color="purple"
              style={{ marginRight: 4 }}
            >
              工具：{t.tool_name}
            </Tag>
          )}
          {t.observation && (
            <Paragraph
              data-testid="agent-thought-observation"
              type="secondary"
              style={{ marginTop: 4, marginBottom: 0 }}
            >
              观察：{t.observation}
            </Paragraph>
          )}
        </li>
      ))}
    </ol>
  );
}

export default AgentThoughtTimeline;
