import { useState } from "react";
import { useParams } from "react-router-dom";
import { Layout } from "antd";
import { ChatWindow } from "@/components/ChatWindow";
import { NodeTraceViewer } from "@/components/workflow/NodeTraceViewer";
import type { NcmuSseEvent } from "@/lib/sse-types";

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
      <Layout.Content style={{ flex: 2, padding: 16, minHeight: 0 }}>
        <ChatWindow
          appId={appId!}
          sessionId={null}
          streamEndpointOverride={streamEndpoint}
          // ChatWindow's `onNcmuEvent` parameter is the streamChat.ts copy of
          // NcmuSseEvent (TASK-70a path B inline duplicate); NodeTraceViewer
          // and this page consume the canonical sse-types.ts copy (TASK-70b-1
          // baseline). The two are structurally compatible aside from a
          // nullability nuance on `NodeStartedData.title` (streamChat allows
          // null) — the duplicate-source backlog is documented in
          // sse-types.ts top comment. Cast at this boundary until the
          // backlog is cleared.
          onNcmuEvent={(evt) =>
            setNodeTrace((prev) => [...prev, evt as unknown as NcmuSseEvent])
          }
        />
      </Layout.Content>
      <Layout.Sider
        width={320}
        theme="light"
        style={{ borderLeft: "1px solid #eee", overflow: "auto" }}
      >
        <h3 style={{ padding: 16, margin: 0 }}>节点流</h3>
        <NodeTraceViewer nodeTrace={nodeTrace} />
      </Layout.Sider>
    </Layout>
  );
}

export default AdvancedChatPage;
