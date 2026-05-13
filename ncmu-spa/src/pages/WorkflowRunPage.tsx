// TASK-77 (Phase 2B B3) — workflow-mode page.
//
// Layout (per plan §修法 line 2487-2510): Tabs（执行 / 历史）on the page; the
// 执行 tab stacks 三 Card 主区 — 输入 (DynamicInputForm) / 节点流
// (NodeTraceViewer) / 输出 (JSON dump of workflow_finished.outputs)；历史 tab
// is RunHistoryList. runWorkflow drives a workflow SSE stream; the callback
// is invoked with one of two shapes per frame (see lib/api.ts:67-77):
//   - string         — completion-mode text chunks (workflow mode ignores).
//   - NcmuSseEvent   — workflow envelope frames: node_started / node_finished
//                      / workflow_finished / agent_thought / tool_call. All
//                      are appended to nodeTrace; only workflow_finished
//                      mutates output state (data.outputs payload).
//
// Endpoint correctness: runWorkflow internally targets
// `/workflow/apps/${appId}/run` relative to API_BASE — F-FRESH-2 (PLAN-FIX-4)
// double-prefix trap closed by TASK-76. This page passes 0 endpoint override;
// the F-NEW-1 / F-FRESH-2 / H-FRESH-3 PLAN-FIX修订 apply to the ChatWindow
// path (TASK-75 / TASK-78), not to direct-runWorkflow callers.

import { useState } from "react";
import { useParams } from "react-router-dom";
import { Card, Layout, Tabs, notification } from "antd";
import { DynamicInputForm } from "@/components/workflow/DynamicInputForm";
import { NodeTraceViewer } from "@/components/workflow/NodeTraceViewer";
import { RunHistoryList } from "@/components/workflow/RunHistoryList";
import { runWorkflow, useAppParameters } from "@/lib/api";
import type { NcmuSseEvent } from "@/lib/sse-types";

export function WorkflowRunPage() {
  const { appId } = useParams<{ appId: string }>();
  const [nodeTrace, setNodeTrace] = useState<NcmuSseEvent[]>([]);
  const [output, setOutput] = useState<Record<string, unknown>>({});
  // REWORK-77-INDEP I-INDEP-3: `submitting` is forwarded to DynamicInputForm so
  // the 提交 button toggles antd's loading state — without this, a long-running
  // workflow lets the user click 提交 again, which races setNodeTrace([]) and
  // wipes the in-flight trace. Parity with CompletionPage submitting wiring.
  const [submitting, setSubmitting] = useState(false);
  const { data: parameters } = useAppParameters(appId!);

  // REWORK-77-INDEP I-INDEP-2: runWorkflow may throw (network error / SSE
  // parse failure / backend 5xx). Without try/catch the rejection becomes an
  // unhandled promise + the user-visible state stays half-reset (nodeTrace and
  // output were cleared at the top but never repopulated). Wrap so the user
  // sees a notification and the submitting flag releases regardless.
  const handleSubmit = async (values: Record<string, unknown>) => {
    setSubmitting(true);
    setNodeTrace([]);
    setOutput({});
    try {
      // TASK-81 (B-NEW-26b): pass form values via the `inputs` slot of
      // RunWorkflowInputs — runWorkflow then serializes the 3-layer wire
      // body `{ inputs: { inputs: values } }`. workflow.py:51-55 reads
      // only `inputs.get("inputs", {})` and ignores query/conversation_id,
      // so neither field is set here.
      await runWorkflow(appId!, { inputs: values }, (evt) => {
        if (typeof evt === "string") return; // completion-mode chunks ignored
        setNodeTrace((prev) => [...prev, evt]);
        if (evt.event_type === "workflow_finished") {
          const outs = (evt.data as { outputs?: Record<string, unknown> })
            .outputs;
          setOutput(outs ?? {});
        }
      });
    } catch (err) {
      notification.error({
        message: "执行失败",
        description: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Layout style={{ height: "100vh" }}>
      <Layout.Content style={{ padding: 16 }}>
        <Tabs
          items={[
            {
              key: "run",
              label: "执行",
              children: (
                <>
                  <Card title="输入" style={{ marginBottom: 16 }}>
                    <DynamicInputForm
                      parameters={parameters ?? []}
                      onSubmit={handleSubmit}
                      submitting={submitting}
                    />
                  </Card>
                  <Card title="节点流" style={{ marginBottom: 16 }}>
                    <NodeTraceViewer nodeTrace={nodeTrace} />
                  </Card>
                  <Card title="输出">
                    <pre
                      data-testid="workflow-output"
                      style={{ whiteSpace: "pre-wrap", margin: 0 }}
                    >
                      {JSON.stringify(output, null, 2)}
                    </pre>
                  </Card>
                </>
              ),
            },
            {
              key: "history",
              label: "历史",
              children: <RunHistoryList appId={appId!} />,
            },
          ]}
        />
      </Layout.Content>
    </Layout>
  );
}

export default WorkflowRunPage;
