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
import { useEffect, useRef, useState } from "react";
import { Card, Layout, notification } from "antd";
import { DynamicInputForm } from "@/components/workflow/DynamicInputForm";
import { RunHistoryList } from "@/components/workflow/RunHistoryList";
import { runWorkflow, useAppParameters } from "@/lib/api";
import type { WorkflowFinishedData } from "@/lib/sse-types";

export function CompletionPage() {
  const { appId } = useParams<{ appId: string }>();
  const [output, setOutput] = useState<string>("");
  const [submitting, setSubmitting] = useState(false);
  const { data: parameters } = useAppParameters(appId!);

  // TASK-C 维度 34 (B-NEW-34): on unmount cancel any in-flight SSE so the
  // background fetch doesn't keep pumping into setState on a torn-down tree
  // (React 19 strict mode + dev tools surface this as a warning). The
  // AbortController is replaced per handleSubmit invocation; useEffect's
  // cleanup runs only on unmount because the dep array is empty.
  const abortRef = useRef<AbortController | null>(null);
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  const handleSubmit = async (values: Record<string, unknown>) => {
    setSubmitting(true);
    setOutput("");
    // Cancel a previous in-flight stream (if any) before starting a new one.
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
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
          // TASK-C 维度 33 (B-NEW-33): TASK-B backend now ships error path
          // as workflow_finished(failed/exception) envelope (no throw).
          // Surface it as a toast so the user sees feedback — succeeded
          // path keeps the prior outputs.answer rendering behaviour.
          const wf = chunk.data as WorkflowFinishedData;
          // TASK-E (B-NEW-52): backend +'timeout' Literal. Surface with
          // dedicated "运行超时" copy — the else branch sets output state
          // from outputs.answer, which is empty on timeout → would render
          // an empty Card (silent UX fallthrough). INDEP TASK-D 主审盲区
          // #11 「用户体验完整路径」实证。
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
          } else {
            const ans = (wf.outputs as { answer?: unknown } | undefined)
              ?.answer;
            if (typeof ans === "string") setOutput(ans);
          }
          setSubmitting(false);
        }
      }, controller.signal);
    } catch (err) {
      // ★ TASK-C REWORK-INDEP-I-1: AbortController.abort() (unmount cleanup
      // or handleSubmit's cancel-previous) causes the in-flight fetch to
      // reject with `Error.name === "AbortError"` — that's an expected user
      // action, not a failure. Mirror ChatWindow:427-428's existing guard
      // so we don't surface a misleading "执行失败" toast on cancel paths.
      if (err instanceof Error && err.name === "AbortError") return;
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
        {/* TASK-C 维度 35 (B-NEW-35): page-{kind}- testid landmarks. */}
        <Card
          title="输入"
          style={{ marginBottom: 16 }}
          data-testid="completion-form"
        >
          <div data-testid="completion-submit">
            <DynamicInputForm
              parameters={parameters ?? []}
              onSubmit={handleSubmit}
              submitting={submitting}
            />
          </div>
        </Card>
        <Card title="输出" data-testid="completion-output">
          <pre style={{ whiteSpace: "pre-wrap", margin: 0 }}>
            {output || "（待执行）"}
          </pre>
        </Card>
      </Layout.Content>
      <Layout.Sider
        width={320}
        theme="light"
        style={{ borderLeft: "1px solid #eee" }}
        data-testid="completion-history"
      >
        <h3 style={{ padding: 16, margin: 0 }}>历史</h3>
        <RunHistoryList appId={appId!} />
      </Layout.Sider>
    </Layout>
  );
}

export default CompletionPage;
