// TASK-81 (B-NEW-26b) — wire-shape contract tests for runWorkflow + the
// ChatWindow workflow buildBody path. These spy on `fetch` directly and
// assert FIELD-LEVEL on the JSON-parsed POST body to lock the 3-layer
// shape consumed by ncmu-backend orchestrators (commit c320cd9 字面级):
//
//   POST body = { inputs: { inputs: <form_vars>, query?: <text>, conversation_id?: <conv> } }
//
//   - completion.py:43-48     reads inputs.inputs + inputs.query
//   - workflow.py:51-55       reads inputs.inputs (query/conv ignored)
//   - advanced_chat.py:58-64  reads all three
//   - agent_chat.py:50-56     reads all three
//
// These asserts are intentionally NOT structural (no toMatchObject) — every
// inner / outer key path is named explicitly so future drift surfaces here
// before it reaches Dify upstream (the feedback_tdd_mock_vs_real_api 第 10 次
// 同型 + feedback_batch_failure_wire_shape_first defensive layer).

import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { runWorkflow } from "@/lib/api";
import { ChatWindow } from "@/components/ChatWindow";
import { setAuth } from "@/lib/auth";

function emptyStreamResponse(): Response {
  // Empty SSE body — runWorkflow / streamChat exit cleanly after the reader
  // hits done=true. Sufficient because every assertion here looks at what
  // fetch was CALLED WITH, not what it returns.
  const stream = new ReadableStream<Uint8Array>({
    start(c) {
      c.close();
    },
  });
  return new Response(stream, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

interface CapturedCall {
  url: string;
  body: Record<string, unknown> | null;
}

function captureFetch(): { calls: CapturedCall[]; spy: ReturnType<typeof vi.fn> } {
  const calls: CapturedCall[] = [];
  const spy = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
    const u = typeof url === "string" ? url : String(url);
    const raw = init?.body;
    let body: Record<string, unknown> | null = null;
    if (typeof raw === "string") {
      try {
        body = JSON.parse(raw) as Record<string, unknown>;
      } catch {
        body = null;
      }
    }
    calls.push({ url: u, body });
    return emptyStreamResponse();
  });
  return { calls, spy };
}

beforeEach(() => {
  setAuth("dev-jwt-fixture", { id: "u1", name: "Alice", is_active: true, is_admin: false });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("TASK-81 wire-shape contract — runWorkflow", () => {
  // test A — completion path: outer query field MUST be set, inner inputs
  // MUST carry the form vars verbatim. Mirrors fixtures.json completion
  // shape `{inputs: {inputs: {query: "..."}, query: "..."}}`.
  it("test A: completion path body has body.inputs.inputs.<form_key> + body.inputs.query", async () => {
    const { calls, spy } = captureFetch();
    vi.stubGlobal("fetch", spy);

    const formValues = { query: "Reply with one word", tone: "正式" };
    await runWorkflow(
      "app-completion",
      { inputs: formValues, query: "Reply with one word" },
      () => {
        /* no chunks expected — empty stream */
      },
    );

    expect(spy).toHaveBeenCalledTimes(1);
    expect(calls[0].url).toContain("/workflow/apps/app-completion/run");
    const b = calls[0].body!;
    expect(b).not.toBeNull();
    // outer layer
    expect(b).toHaveProperty("inputs");
    const outer = b.inputs as Record<string, unknown>;
    // inner pydantic layer — fields backend completion.py:43-48 reads
    expect(outer).toHaveProperty("inputs");
    expect(outer).toHaveProperty("query");
    // 3rd layer values
    const inner = outer.inputs as Record<string, unknown>;
    expect(inner.query).toBe("Reply with one word");
    expect(inner.tone).toBe("正式");
    // outer body.inputs.query (the Dify body.query Pane 0 push-back要求
    // promote to outer per fixtures.json completion字面)
    expect(outer.query).toBe("Reply with one word");
  });

  // test B — workflow path: outer inputs contains form vars only; query
  // and conversation_id keys MUST NOT appear (workflow.py:51-55 doesn't
  // read them; fixtures.json workflow shape is `{inputs: {inputs: {x: 1}}}`
  // with NO outer query).
  it("test B: workflow path body has body.inputs.inputs.<form_key> and NO body.inputs.query", async () => {
    const { calls, spy } = captureFetch();
    vi.stubGlobal("fetch", spy);

    const formValues = { x: 1, label: "节点" };
    await runWorkflow("app-workflow", { inputs: formValues }, () => undefined);

    expect(spy).toHaveBeenCalledTimes(1);
    const b = calls[0].body!;
    expect(b).toHaveProperty("inputs");
    const outer = b.inputs as Record<string, unknown>;
    const inner = outer.inputs as Record<string, unknown>;
    expect(inner.x).toBe(1);
    expect(inner.label).toBe("节点");
    // workflow mode: query / conversation_id NOT serialized when caller
    // doesn't pass them (runWorkflow uses `inputs.query !== undefined`
    // guard to skip the key entirely).
    expect(Object.prototype.hasOwnProperty.call(outer, "query")).toBe(false);
    expect(Object.prototype.hasOwnProperty.call(outer, "conversation_id")).toBe(
      false,
    );
  });

  // test C — ChatWindow chat path (default, no streamEndpointOverride):
  // body keeps Phase 1 flat shape `{query, conversation_id}` with NO outer
  // `inputs` wrapper. Regression-locks the chat path against the
  // TASK-81 workflow-shape rewrite.
  it("test C: ChatWindow chat path body is flat {query, conversation_id} with no outer inputs wrapper", async () => {
    const { calls, spy } = captureFetch();
    vi.stubGlobal("fetch", spy);

    render(<ChatWindow appId="app-chat" sessionId={null} />);
    const ta = screen.getByPlaceholderText(/输入问题/) as HTMLTextAreaElement;
    await act(async () => {
      fireEvent.change(ta, { target: { value: "hi chat" } });
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /发\s*送/ }));
    });
    await waitFor(() => expect(spy).toHaveBeenCalled());

    // The history fetch (GET /sessions/.../messages) does not fire when
    // sessionId is null, so the captured call here is the chat POST.
    const chatCall = calls.find((c) =>
      c.url.includes("/chat/app-chat"),
    );
    expect(chatCall).toBeDefined();
    const b = chatCall!.body!;
    expect(b).toHaveProperty("query", "hi chat");
    expect(b).toHaveProperty("conversation_id");
    // chat path字面禁含 outer `inputs` wrapper — proves the buildBody
    // useWorkflowShape=false branch is intact.
    expect(Object.prototype.hasOwnProperty.call(b, "inputs")).toBe(false);
  });

  // test E — runWorkflow without `inputs` field defaults inner.inputs to {}.
  // REWORK-81-NIT C2: locks the api.ts:166 `inputs.inputs ?? {}` nullish
  // fallback at the wire layer so a future refactor that drops the
  // fallback (eg. changing to `inputs.inputs` raw) surfaces here before
  // reaching production. Backend `workflow.py:51-55` AND `completion.py:43-48`
  // both depend on `inputs.get("inputs", {})` resolving to a dict — if the
  // SPA shipped a body where `body.inputs.inputs` was missing or null,
  // pydantic would coerce `WorkflowRunRequest.inputs.inputs = None` and
  // `None.get("...")` would 500 the orchestrator.
  it("test E: runWorkflow without inputs field defaults inner.inputs={} (3-layer body preserved)", async () => {
    const { calls, spy } = captureFetch();
    vi.stubGlobal("fetch", spy);

    // Caller passes only `query` — `inputs` key intentionally omitted to
    // exercise the `inputs.inputs ?? {}` nullish branch in runWorkflow.
    await runWorkflow("app-defaults", { query: "x" }, () => undefined);

    expect(spy).toHaveBeenCalledTimes(1);
    const b = calls[0].body!;
    expect(b).toHaveProperty("inputs");
    const outer = b.inputs as Record<string, unknown>;
    // 3-layer structure preserved even with no caller-provided form vars.
    expect(outer).toHaveProperty("inputs");
    // Strict toEqual({}) — not toMatchObject — so a leaked sentinel value
    // (eg. `null` / `undefined` / arbitrary "form vars" garbage) fails the
    // assertion字面.
    expect(outer.inputs).toEqual({});
    // outer.query was explicitly set, so it must appear.
    expect(outer.query).toBe("x");
    // conversation_id was NOT set by the caller, so the key must be absent
    // (mirrors test B's workflow-mode shape — runWorkflow guards
    // `inputs.conversation_id !== undefined` before writing the key).
    expect(
      Object.prototype.hasOwnProperty.call(outer, "conversation_id"),
    ).toBe(false);
  });

  // test D — ChatWindow workflow path (streamEndpointOverride set):
  // body MUST be the 3-layer shape with inner inputs={} (form vars
  // absent because ChatWindow has no DynamicInputForm concept), inner
  // query=<text>, inner conversation_id=<conv ?? "">. Mirrors fixtures.json
  // advanced-chat/agent-chat字面.
  it("test D: ChatWindow workflow path body is 3-layer with inner inputs={}, query=<text>, conversation_id=<conv>", async () => {
    const { calls, spy } = captureFetch();
    vi.stubGlobal("fetch", spy);

    render(
      <ChatWindow
        appId="app-adv"
        sessionId={null}
        streamEndpointOverride="/workflow/apps/app-adv/run"
      />,
    );
    const ta = screen.getByPlaceholderText(/输入问题/) as HTMLTextAreaElement;
    await act(async () => {
      fireEvent.change(ta, { target: { value: "reply ok" } });
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /发\s*送/ }));
    });
    await waitFor(() => expect(spy).toHaveBeenCalled());

    const wfCall = calls.find((c) =>
      c.url.includes("/workflow/apps/app-adv/run"),
    );
    expect(wfCall).toBeDefined();
    const b = wfCall!.body!;
    expect(b).toHaveProperty("inputs");
    const outer = b.inputs as Record<string, unknown>;
    expect(outer).toHaveProperty("inputs");
    expect(outer).toHaveProperty("query", "reply ok");
    expect(outer).toHaveProperty("conversation_id", "");
    const inner = outer.inputs as Record<string, unknown>;
    // Inner form vars empty by design for advanced-chat / agent-chat
    // (decision 3 of TASK-81 CHECKPOINT — no form-vars prop on ChatWindow).
    expect(Object.keys(inner)).toHaveLength(0);
  });
});
