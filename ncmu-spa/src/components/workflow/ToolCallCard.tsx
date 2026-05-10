// 单条 `tool_call` 事件渲染卡片 — agent-chat (TASK-78) 在 Page 内排成 list 显示。
// props 由 `ToolCallData`（sse-types.ts，与 backend 字面对齐）驱动，纯展示，不
// 维护内部状态。

import { Card, Spin, Tag } from "antd";
import type { ToolCallData } from "@/lib/sse-types";

export interface ToolCallCardProps {
  toolCall: ToolCallData;
}

const STATUS_TAG: Record<ToolCallData["status"], { color: string; text: string }> = {
  calling: { color: "processing", text: "调用中" },
  completed: { color: "success", text: "已完成" },
  failed: { color: "error", text: "失败" },
};

export function ToolCallCard({ toolCall }: ToolCallCardProps) {
  const { tool_name, tool_input, tool_output, status } = toolCall;
  const tag = STATUS_TAG[status];

  return (
    <Card
      data-testid="tool-call-card"
      data-status={status}
      size="small"
      style={{ marginBottom: 8 }}
      title={
        <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span>{tool_name}</span>
          {status === "calling" ? (
            <Spin size="small" data-testid="tool-call-spinner" />
          ) : (
            <Tag color={tag.color} data-testid="tool-call-status-tag">
              {tag.text}
            </Tag>
          )}
        </span>
      }
    >
      <div data-testid="tool-call-input">
        <strong>输入：</strong>
        <pre style={{ margin: 0, fontSize: 12, whiteSpace: "pre-wrap" }}>
          {JSON.stringify(tool_input, null, 2)}
        </pre>
      </div>
      {tool_output !== undefined && (
        <div data-testid="tool-call-output" style={{ marginTop: 8 }}>
          <strong>输出：</strong>
          <pre style={{ margin: 0, fontSize: 12, whiteSpace: "pre-wrap" }}>
            {typeof tool_output === "string"
              ? tool_output
              : JSON.stringify(tool_output, null, 2)}
          </pre>
        </div>
      )}
    </Card>
  );
}

export default ToolCallCard;
