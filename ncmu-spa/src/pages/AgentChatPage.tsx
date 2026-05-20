// TASK-78 (Phase 2B B3) — agent-chat mode page.
//
// Layout: ChatWindow main area (left, flex 2) + Sider (right, 360) with Tabs
// — (1) Agent Thoughts → AgentThoughtTimeline, (2) Tool Calls → ToolCallCard
// list. Agent runs go through the workflow SSE pipeline so the page consumes
// ChatWindow's `onNcmuEvent` callback and routes `agent_thought` / `tool_call`
// envelopes into local sider state. ChatWindow itself (PLAN-FIX-4 H-FRESH-3)
// owns the main-area chat bubble rendering — including the
// `workflow_finished.outputs.answer` injection into the assistant bubble — so
// this page does NOT also have to render chat history.
//
// Endpoint correctness:
//   - F-NEW-1 (PLAN-FIX-3): agent-chat MUST hit the workflow endpoint
//     `/workflow/apps/${appId}/run`; the chat endpoint bypasses
//     dispatcher.dispatch → workflow_runs is never persisted → spec §6.3 AC
//     unreachable.
//   - F-FRESH-2 (PLAN-FIX-4): the path passed via `streamEndpointOverride`
//     is RELATIVE to API_BASE (= "/api/v1/ncmu"); writing the full prefix
//     here yields a double-prefix 404 inside fetch.
//
// Type-duplicate note:
//   ChatWindow's `onNcmuEvent` parameter is the streamChat.ts inline copy of
//   NcmuSseEvent (TASK-70a path B); this page consumes the canonical
//   sse-types.ts copy (TASK-70b-1 baseline). The two are structurally
//   compatible. Same cast pattern as AdvancedChatPage (TASK-75) — the
//   duplicate-source backlog is tracked in sse-types.ts top comment.

import { useParams } from "react-router-dom";
import { useState } from "react";
import { Layout, Tabs, notification } from "antd";
import { ChatWindow } from "@/components/ChatWindow";
import { AgentThoughtTimeline } from "@/components/workflow/AgentThoughtTimeline";
import { ToolCallCard } from "@/components/workflow/ToolCallCard";
import type {
  AgentThoughtData,
  NcmuSseEvent,
  ToolCallData,
  WorkflowFinishedData,
} from "@/lib/sse-types";

export function AgentChatPage() {
  const { appId } = useParams<{ appId: string }>();
  const [thoughts, setThoughts] = useState<AgentThoughtData[]>([]);
  const [toolCalls, setToolCalls] = useState<ToolCallData[]>([]);

  const streamEndpoint = `/workflow/apps/${appId}/run`;

  return (
    <Layout style={{ height: "100vh" }}>
      {/* TASK-C 维度 35 (B-NEW-35): page-{kind}- testid landmarks — chat-mode
          pages only carry output (chat bubble area) + history (thoughts /
          tool calls sider) because they have no DynamicInputForm / submit
          button at the page level (input lives inside ChatWindow / ChatInput).
          既有 `agent-chat-sider-tabs` / `tool-call-list` 保留。
          TASK-CLEAN-5 (B-NEW-35 决策 Path A "补测试"): page-level landmark
          contract 锁定 — agent-chat-output / agent-chat-history /
          agent-chat-sider-tabs / agent-thought-timeline / tool-call-list 共
          5 个 page-level testid 由 AgentChatPage.test.tsx 字面断言
          (test_cleanup_5_*)。Path A 选自 b_new_backlog_inventory.md §line
          121 "删 or 补测试" source-of-truth；4 个既有 testid 沿用
          TASK-C/commit b5e5b0f 实施，仅新增 agent-thought-timeline 包装
          AgentThoughtTimeline (sider 默认 tab) 以补齐 plan §AC#2 字面例举。 */}
      <Layout.Content
        style={{ flex: 2, padding: 16, minHeight: 0 }}
        data-testid="agent-chat-output"
      >
        <ChatWindow
          appId={appId!}
          sessionId={null}
          streamEndpointOverride={streamEndpoint}
          onNcmuEvent={(evt) => {
            const canonical = evt as unknown as NcmuSseEvent;
            if (canonical.event_type === "agent_thought") {
              setThoughts((prev) => [
                ...prev,
                canonical.data as AgentThoughtData,
              ]);
            } else if (canonical.event_type === "tool_call") {
              setToolCalls((prev) => [
                ...prev,
                canonical.data as ToolCallData,
              ]);
            } else if (canonical.event_type === "workflow_finished") {
              // TASK-C 维度 33 (B-NEW-33): TASK-B backend ships error path
              // as workflow_finished(failed/exception) envelope. Chat-mode
              // page has no page-level submitting state (ChatWindow owns
              // streaming flag), so the toast is the only feedback added
              // at this boundary.
              const wf = canonical.data as WorkflowFinishedData;
              // TASK-E (B-NEW-52): see AdvancedChatPage.tsx for the
              // 'timeout' rationale.
              // TASK-F (B-NEW-53): see AdvancedChatPage.tsx for the
              // 'stopped' rationale — pre-existing backend Literal 4-stack
              // 同型 silent fallthrough gap，闭环 UX 显式分流。
              if (
                wf.status === "failed" ||
                wf.status === "exception" ||
                wf.status === "timeout" ||
                wf.status === "stopped"
              ) {
                notification.error({
                  message:
                    wf.status === "failed"
                      ? "上游错误"
                      : wf.status === "exception"
                        ? "运行异常"
                        : wf.status === "timeout"
                          ? "运行超时"
                          : "运行已停止",
                  description: wf.error || "未知错误",
                });
              }
            }
          }}
        />
      </Layout.Content>
      <Layout.Sider
        width={360}
        theme="light"
        style={{ borderLeft: "1px solid #eee", overflow: "auto" }}
        data-testid="agent-chat-history"
      >
        <Tabs
          data-testid="agent-chat-sider-tabs"
          items={[
            {
              key: "thoughts",
              label: "Agent Thoughts",
              children: (
                <div data-testid="agent-thought-timeline">
                  <AgentThoughtTimeline thoughts={thoughts} />
                </div>
              ),
            },
            {
              key: "tools",
              label: "Tool Calls",
              children: (
                <div data-testid="tool-call-list">
                  {toolCalls.map((tc, i) => (
                    <ToolCallCard key={i} toolCall={tc} />
                  ))}
                </div>
              ),
            },
          ]}
        />
      </Layout.Sider>
    </Layout>
  );
}

export default AgentChatPage;
