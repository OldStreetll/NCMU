// TASK-76 (Phase 2B B3) — completion-mode page.
//
// Layout: input form + 输出 Card 主区 / 历史 sider 复用 RunHistoryList.
// Data flow: useAppParameters → DynamicInputForm; runWorkflow drives the
// completion-mode SSE stream and invokes the callback per frame.
//
// ★ REWORK-76-INDEP / C-INDEP-1: backend `completion.py` orchestrator
// accumulates Dify upstream `message` / `text_chunk` frames into a single
// `accumulated_text` and emits ONE `workflow_finished` NcmuSseEvent at
// upstream `message_end` (data.outputs.answer = full text). The NCMU SSE
// stream therefore never carries a `message` event for this mode, so a
// pure `typeof chunk === "string"` branch silently drops the only frame.
// We keep the string branch (forward-compat for a future per-token mode)
// but add the `workflow_finished` envelope branch as the actual sink.

import { useParams } from "react-router-dom";
import { useState } from "react";
import { Card, Layout, notification } from "antd";
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
      // TASK-81 (B-NEW-26b): Completion mode requires DOUBLE-fill of the
      // `query` field per TASK-79 fixtures.json字面 (commit c320cd9, 5/5 PASS):
      //   body = { inputs: { inputs: <form_vars>, query: <text> } }
      //                              ^^^^^^^^^^^^^^^^^^^^^^^^^^
      //                              both filled with the user's text — form
      //                              vars retains the `query` key (Dify
      //                              prompt template references {{query}}
      //                              via inputs.*) AND backend body.query is
      //                              explicitly set per completion.py:45.
      // Pull `query` off form values (if the App schema includes a
      // `variable: "query"` field) and lift it to the outer slot;
      // body.inputs.inputs keeps the full values dict so the inner copy
      // remains for template resolution — exact字面 mirror of fixtures.json
      // completion shape `{inputs: {inputs: {query: "..."}, query: "..."}}`.
      //
      // ★ App schema assumption (REWORK-81-NIT C1): the bound Dify Completion
      // App's parameter schema MUST include a `variable: "query"` field of
      // type text/paragraph — without it `maybeQuery` is undefined,
      // `outerQuery` falls back to "" and backend `completion.py:45`
      // forwards `body.query = ""` to Dify Completion API, which may 400 the
      // request (Dify expects a non-empty user message). NCMU [KB]-style
      // naming convention places this responsibility on the App author; the
      // SPA does not synthesize a query from arbitrary form fields.
      //
      // ★ 双填根因 (REWORK-81-NIT C3): Dify Completion App simultaneously
      // consumes `query` at two distinct call sites — (1) prompt template
      // interpolation `{{query}}` resolves from `inputs.<var>` so the value
      // must appear inside `body.inputs.*`; (2) Dify Completion REST API
      // body.query is the user message ushered into chat-history /
      // run-context. The two sinks read from independent slots — leaving
      // either blank silently degrades a different facet of the run
      // (template renders an empty placeholder vs context shows no user
      // turn). Hence the SPA double-fills: form values land in
      // `body.inputs.inputs` verbatim (template) AND the same string is
      // promoted to `body.inputs.query` (API body).
      const maybeQuery = (values as Record<string, unknown>).query;
      const outerQuery = typeof maybeQuery === "string" ? maybeQuery : "";
      await runWorkflow(appId!, { inputs: values, query: outerQuery }, (chunk) => {
        if (typeof chunk === "string") {
          // Reserved for a future per-token streaming mode; current
          // backend completion orchestrator does not emit string chunks.
          setOutput((prev) => prev + chunk);
          return;
        }
        if (chunk.event_type === "workflow_finished") {
          const ans = (chunk.data as { outputs?: { answer?: unknown } })
            .outputs?.answer;
          if (typeof ans === "string") setOutput(ans);
        }
      });
    } catch (err) {
      // ★ REWORK-76-INDEP / I-INDEP-2: surface upstream / network failures
      // as a toast instead of leaking an unhandled promise to React (which
      // would land on the error boundary and blank the page).
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
