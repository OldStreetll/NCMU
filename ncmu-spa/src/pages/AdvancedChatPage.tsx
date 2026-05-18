import { useState } from "react";
import { useParams } from "react-router-dom";
import { Layout, notification } from "antd";
import { ChatWindow } from "@/components/ChatWindow";
import { NodeTraceViewer } from "@/components/workflow/NodeTraceViewer";
import type { NcmuSseEvent, WorkflowFinishedData } from "@/lib/sse-types";

export function AdvancedChatPage() {
  const { appId } = useParams<{ appId: string }>();
  const [nodeTrace, setNodeTrace] = useState<NcmuSseEvent[]>([]);

  // F-NEW-1 (PLAN-FIX-3): advanced-chat MUST hit the workflow endpoint or
  // dispatcher.dispatch is bypassed (workflow_runs row never written → spec
  // §6.3 AC unreachable). F-FRESH-2 (PLAN-FIX-4): the path MUST be RELATIVE
  // to API_BASE (= "/api/v1/ncmu") — passing the full path yields a
  // double-prefix 404 in fetch.
  const streamEndpoint = `/workflow/apps/${appId}/run`;

  return (
    <Layout style={{ height: "100vh" }}>
      {/* TASK-C 维度 35 (B-NEW-35): page-{kind}- testid landmarks — chat-mode
          pages only carry output (chat bubble area) + history (node trace
          sider) because they have no DynamicInputForm / submit button at
          the page level (input lives inside ChatWindow / ChatInput). */}
      <Layout.Content
        style={{ flex: 2, padding: 16, minHeight: 0 }}
        data-testid="advanced-chat-output"
      >
        <ChatWindow
          appId={appId!}
          sessionId={null}
          streamEndpointOverride={streamEndpoint}
          // TASK-C 维度 32 (B-NEW-32): the schema-source backlog mentioned
          // in the prior comment was closed by re-routing streamChat to
          // re-export from sse-types — the `as unknown as NcmuSseEvent`
          // cast is kept for forward-compat (ChatWindow's prop is still
          // declared with the re-exported alias) but the underlying types
          // now share one definition.
          //
          // TASK-C 维度 33 (B-NEW-33): TASK-B backend now ships error path
          // as a `workflow_finished(failed/exception)` envelope (no throw),
          // forwarded here via ChatWindow.onNcmuEvent. Surface it as a
          // toast — the chat-mode page has no page-level submitting state
          // (ChatWindow owns its own `streaming` flag), so the toast is
          // the only user-facing feedback we add at the page boundary.
          onNcmuEvent={(evt) => {
            const canonical = evt as unknown as NcmuSseEvent;
            if (canonical.event_type === "workflow_finished") {
              const wf = canonical.data as WorkflowFinishedData;
              // TASK-E (B-NEW-52): backend WorkflowFinishedData.status now
              // also carries 'timeout'; surface it with a dedicated copy
              // ("运行超时") so it does NOT silent-fallthrough into the
              // implicit success branch (which here is a no-op but on
              // form-mode pages would render an empty output).
              if (
                wf.status === "failed" ||
                wf.status === "exception" ||
                wf.status === "timeout"
              ) {
                notification.error({
                  message:
                    wf.status === "failed"
                      ? "上游错误"
                      : wf.status === "exception"
                        ? "运行异常"
                        : "运行超时",
                  description: wf.error || "未知错误",
                });
              }
            }
            setNodeTrace((prev) => [...prev, canonical]);
          }}
        />
      </Layout.Content>
      <Layout.Sider
        width={320}
        theme="light"
        style={{ borderLeft: "1px solid #eee", overflow: "auto" }}
        data-testid="advanced-chat-history"
      >
        <h3 style={{ padding: 16, margin: 0 }}>节点流</h3>
        <NodeTraceViewer nodeTrace={nodeTrace} />
      </Layout.Sider>
    </Layout>
  );
}

export default AdvancedChatPage;
