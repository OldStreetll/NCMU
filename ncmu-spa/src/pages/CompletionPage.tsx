// TASK-76 (Phase 2B B3) — completion-mode page.
//
// Layout: input form + 输出 Card 主区 / 历史 sider 复用 RunHistoryList.
// Data flow: useAppParameters → DynamicInputForm; runWorkflow streams text
// chunks via the callback into `output` state. runWorkflow's callback may
// also be invoked with an NcmuSseEvent (workflow envelope) — completion
// mode ignores those and only consumes string chunks.

import { useParams } from "react-router-dom";
import { useState } from "react";
import { Card, Layout } from "antd";
import { DynamicInputForm } from "@/components/workflow/DynamicInputForm";
import { RunHistoryList } from "@/components/workflow/RunHistoryList";
import { runWorkflow, useAppParameters } from "@/lib/api";

export function CompletionPage() {
  const { appId } = useParams<{ appId: string }>();
  const [output, setOutput] = useState<string>("");
  const [submitting, setSubmitting] = useState(false);
  const { data: parameters } = useAppParameters(appId!);

  const handleSubmit = async (values: Record<string, unknown>) => {
    setSubmitting(true);
    setOutput("");
    try {
      await runWorkflow(appId!, values, (chunk) => {
        if (typeof chunk === "string") {
          setOutput((prev) => prev + chunk);
        }
      });
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Layout style={{ height: "100vh" }}>
      <Layout.Content style={{ padding: 16 }}>
        <Card title="输入" style={{ marginBottom: 16 }}>
          <DynamicInputForm
            parameters={parameters ?? []}
            onSubmit={handleSubmit}
            submitting={submitting}
          />
        </Card>
        <Card title="输出">
          <pre style={{ whiteSpace: "pre-wrap", margin: 0 }}>
            {output || "（待执行）"}
          </pre>
        </Card>
      </Layout.Content>
      <Layout.Sider
        width={320}
        theme="light"
        style={{ borderLeft: "1px solid #eee" }}
      >
        <h3 style={{ padding: 16, margin: 0 }}>历史</h3>
        <RunHistoryList appId={appId!} />
      </Layout.Sider>
    </Layout>
  );
}

export default CompletionPage;
