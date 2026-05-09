import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ChatWindow } from "@/components/ChatWindow";
import { setAuth } from "@/lib/auth";

async function typeAndSubmit(text: string): Promise<void> {
  const ta = screen.getByPlaceholderText(/输入问题/) as HTMLTextAreaElement;
  await act(async () => {
    fireEvent.change(ta, { target: { value: text } });
  });
  await act(async () => {
    // antd inserts a space between two CJK chars in button text → "发 送"
    const btn = screen.getByRole("button", { name: /发\s*送/ });
    fireEvent.click(btn);
  });
}

function streamingResponse(frames: string[], onCancel?: () => void): Response {
  const enc = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const f of frames) controller.enqueue(enc.encode(f));
      // do not close — leaves stream open so cancel() observably triggers
    },
    cancel() {
      onCancel?.();
    },
  });
  return new Response(stream, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

beforeEach(() => {
  setAuth("dev-jwt-fixture", { id: "u1", name: "Alice", is_active: true });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ChatWindow", () => {
  it("AC#10.1 first mount with sessionId fetches history and renders bubbles", async () => {
    // REWORK-31 MF-1: real backend MessagesOut is a Dify passthrough envelope —
    // {data: [{id, query, answer, retriever_resources?, created_at?, ...}], has_more, limit}.
    // Each record = one Q/A turn → fan out to 2 ChatMessage rows (user, assistant).
    const fetchSpy = vi.fn(async (url: string | URL) => {
      const u = String(url);
      if (u.endsWith("/sessions/sess-1/messages")) {
        return new Response(
          JSON.stringify({
            data: [
              {
                id: "msg-001",
                conversation_id: "conv-fake",
                query: "user 提问",
                answer: "assistant 回答",
                retriever_resources: [],
                created_at: 1234567890,
              },
            ],
            has_more: false,
            limit: 20,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      throw new Error("unexpected fetch: " + u);
    });
    vi.stubGlobal("fetch", fetchSpy);

    render(<ChatWindow appId="app-1" sessionId="sess-1" />);

    // Both bubbles must render — fan-out produced one user + one assistant.
    await waitFor(() =>
      expect(screen.getByText("assistant 回答")).toBeInTheDocument(),
    );
    expect(screen.getByText("user 提问")).toBeInTheDocument();
    // Sanity: only the two we fanned out, not 1, not 3.
    const list = document.querySelector('[data-slot="message-list"]');
    expect(list?.querySelectorAll('[data-role]').length).toBe(2);
  });

  it("AC#10.2 submitting query streams session_created + message into the assistant bubble", async () => {
    const onSessionCreated = vi.fn();
    const frames =
      'event: ncmu.session_created\ndata: {"session_id":"new-sess"}\n\n' +
      'event: message\ndata: {"answer":"Hello "}\n\n' +
      'event: message\ndata: {"answer":"world"}\n\n' +
      'event: message_end\ndata: {"message_id":"m-end"}\n\n';

    const fetchSpy = vi.fn(async (url: string | URL) => {
      const u = String(url);
      if (u.endsWith("/chat/app-1")) {
        // closed stream — full body delivered then EOF
        const enc = new TextEncoder();
        return new Response(
          new ReadableStream<Uint8Array>({
            start(c) {
              c.enqueue(enc.encode(frames));
              c.close();
            },
          }),
          { status: 200, headers: { "Content-Type": "text/event-stream" } },
        );
      }
      throw new Error("unexpected fetch: " + u);
    });
    vi.stubGlobal("fetch", fetchSpy);

    render(
      <ChatWindow
        appId="app-1"
        sessionId={null}
        onSessionCreated={onSessionCreated}
      />,
    );

    await typeAndSubmit("hi");

    await waitFor(() =>
      expect(screen.getByText("Hello world")).toBeInTheDocument(),
    );
    expect(screen.getByText("hi")).toBeInTheDocument();
    expect(onSessionCreated).toHaveBeenCalledWith("new-sess");
  });

  // TASK-44 — message_end retriever_resources dual-read (B-NEW-20).
  // INDEP-PLAN-1 实测: Dify v1.13.3 SSE message_end frame nests
  // retriever_resources at `data.metadata.retriever_resources`, not
  // top-level. ChatWindow:188 must read metadata-nested first, then fall
  // back to top-level (defensive against schema variants), else `[]`.
  // INDEP-PLAN-2 A2 spike (Pane 1, 2026-05-06): GET /v1/messages REST
  // endpoint returns retriever_resources at row top-level — different
  // shape from SSE — so history-load path (line 65) does NOT need this
  // dual-read. These cases cover the streaming path only.
  function chatStreamFetchSpy(frames: string) {
    return vi.fn(async (url: string | URL) => {
      const u = String(url);
      if (u.endsWith("/chat/app-1")) {
        const enc = new TextEncoder();
        return new Response(
          new ReadableStream<Uint8Array>({
            start(c) {
              c.enqueue(enc.encode(frames));
              c.close();
            },
          }),
          { status: 200, headers: { "Content-Type": "text/event-stream" } },
        );
      }
      throw new Error("unexpected fetch: " + u);
    });
  }

  it("TASK-44.1 message_end with metadata.retriever_resources renders chips (Dify真实路径)", async () => {
    const frames =
      'event: message\ndata: {"answer":"hi"}\n\n' +
      'event: message_end\ndata: {"message_id":"m-end","metadata":{"retriever_resources":[' +
      '{"segment_id":"s1","document_id":"d1","document_name":"手册第一节","content":"alpha","score":0.91},' +
      '{"segment_id":"s2","document_id":"d2","document_name":"手册第二节","content":"beta","score":0.82}' +
      ']}}\n\n';
    vi.stubGlobal("fetch", chatStreamFetchSpy(frames));

    render(<ChatWindow appId="app-1" sessionId={null} />);
    await typeAndSubmit("q");

    await waitFor(() => expect(screen.getByText("hi")).toBeInTheDocument());
    await waitFor(() =>
      expect(screen.getAllByTestId("retriever-chip").length).toBe(2),
    );
    expect(screen.getByText("📄 手册第一节")).toBeInTheDocument();
    expect(screen.getByText("📄 手册第二节")).toBeInTheDocument();
  });

  it("TASK-44.2 message_end with top-level retriever_resources falls back (legacy/variant)", async () => {
    const frames =
      'event: message\ndata: {"answer":"ok"}\n\n' +
      'event: message_end\ndata: {"message_id":"m-end","retriever_resources":[' +
      '{"segment_id":"s9","document_id":"d9","document_name":"老路径来源","content":"x","score":0.5}' +
      ']}\n\n';
    vi.stubGlobal("fetch", chatStreamFetchSpy(frames));

    render(<ChatWindow appId="app-1" sessionId={null} />);
    await typeAndSubmit("q");

    await waitFor(() => expect(screen.getByText("ok")).toBeInTheDocument());
    await waitFor(() =>
      expect(screen.getAllByTestId("retriever-chip").length).toBe(1),
    );
    expect(screen.getByText("📄 老路径来源")).toBeInTheDocument();
  });

  it("TASK-44.3 message_end without retriever_resources renders no chip strip", async () => {
    const frames =
      'event: message\ndata: {"answer":"plain"}\n\n' +
      'event: message_end\ndata: {"message_id":"m-end"}\n\n';
    vi.stubGlobal("fetch", chatStreamFetchSpy(frames));

    render(<ChatWindow appId="app-1" sessionId={null} />);
    await typeAndSubmit("q");

    await waitFor(() => expect(screen.getByText("plain")).toBeInTheDocument());
    expect(screen.queryAllByTestId("retriever-chip").length).toBe(0);
    expect(document.querySelector('[data-slot="retriever-chips"]')).toBeNull();
  });

  it("AC#10.3 unmount aborts in-flight stream (no leak)", async () => {
    let cancelled = false;
    // Build a never-closing stream so the reader is parked when we unmount.
    const fetchSpy = vi.fn(async (url: string | URL, init?: RequestInit) => {
      const u = String(url);
      if (u.endsWith("/chat/app-1")) {
        // Honor abort: in real fetch, abort() rejects in-flight reads.
        if (init?.signal) {
          init.signal.addEventListener("abort", () => {
            cancelled = true;
          });
        }
        return streamingResponse(
          ['event: message\ndata: {"answer":"hi"}\n\n'],
          () => {
            cancelled = true;
          },
        );
      }
      throw new Error("unexpected fetch: " + u);
    });
    vi.stubGlobal("fetch", fetchSpy);

    const { unmount } = render(<ChatWindow appId="app-1" sessionId={null} />);
    await typeAndSubmit("hi");
    // give the stream a microtask or two to start before yanking it
    await waitFor(() => expect(fetchSpy).toHaveBeenCalled());

    unmount();

    await waitFor(() => expect(cancelled).toBe(true));
  });

  // -- TASK-70a (PLAN-FIX-3 F-NEW-1 + H-NEW-1 + PLAN-FIX-4 F-FRESH-2/H-FRESH-3)
  // 6 cases (a-f) covering ChatWindow's new NCMU envelope passthrough +
  // endpoint switching + body adapter + workflow_finished injection.
  //
  // baseline-vs-plan note: AC#4(f) shorthand `evt.data.outputs.answer` skips
  // the envelope layer; real backend ships the entire NcmuSseEvent envelope
  // in the SSE `data:` line (routes.py:96 `evt.model_dump(mode="json")`).
  // Mocks below emit envelope-shaped frames (matching AC#4(b)). MessageBubble
  // baseline lacks `data-testid="message-bubble-assistant"` (no testid at
  // all — uses `data-role={role}`); we query via `[data-role="assistant"]`
  // and pick the last item to match plan AC#4(f) "末项" semantics.

  function ncmuStreamFetchSpy(frames: string, urlSuffix = "/chat/app-1") {
    return vi.fn(async (url: string | URL) => {
      const u = String(url);
      if (u.endsWith(urlSuffix)) {
        const enc = new TextEncoder();
        return new Response(
          new ReadableStream<Uint8Array>({
            start(c) {
              c.enqueue(enc.encode(frames));
              c.close();
            },
          }),
          { status: 200, headers: { "Content-Type": "text/event-stream" } },
        );
      }
      throw new Error("unexpected fetch: " + u);
    });
  }

  it("TASK-70a (a) NCMU envelope events without callbacks/override are silently absorbed", async () => {
    // node_started + node_finished envelopes interleaved with chat
    // message; without onNcmuEvent or streamEndpointOverride the
    // component must not crash and must continue rendering chat bubbles.
    const frames =
      'event: node_started\ndata: {"event_type":"node_started","run_id":"r1","timestamp":"2026-01-01T00:00:00Z","data":{"node_id":"n1","node_type":"start"}}\n\n' +
      'event: node_finished\ndata: {"event_type":"node_finished","run_id":"r1","timestamp":"2026-01-01T00:00:01Z","data":{"node_id":"n1","node_type":"start","status":"succeeded","outputs":{}}}\n\n' +
      'event: message\ndata: {"answer":"plain"}\n\n' +
      'event: message_end\ndata: {"message_id":"m"}\n\n';
    vi.stubGlobal("fetch", ncmuStreamFetchSpy(frames));

    render(<ChatWindow appId="app-1" sessionId={null} />);
    await typeAndSubmit("q");

    // Chat path renders normally — the message piece accumulated.
    await waitFor(() => expect(screen.getByText("plain")).toBeInTheDocument());
    // Component is still healthy — the input is still mounted.
    expect(screen.getByPlaceholderText(/输入问题/)).toBeInTheDocument();
  });

  it("TASK-70a (b) onNcmuEvent receives 3 NCMU envelope events with NcmuSseEvent shape", async () => {
    const frames =
      'event: node_started\ndata: {"event_type":"node_started","run_id":"r1","timestamp":"t1","data":{"node_id":"n1","node_type":"start"}}\n\n' +
      'event: node_finished\ndata: {"event_type":"node_finished","run_id":"r1","timestamp":"t2","data":{"node_id":"n1","node_type":"start","status":"succeeded","outputs":{}}}\n\n' +
      'event: workflow_finished\ndata: {"event_type":"workflow_finished","run_id":"r1","timestamp":"t3","data":{"status":"succeeded","outputs":{"answer":"hi"}}}\n\n';
    vi.stubGlobal("fetch", ncmuStreamFetchSpy(frames));

    const onNcmuEvent = vi.fn();
    render(
      <ChatWindow
        appId="app-1"
        sessionId={null}
        onNcmuEvent={onNcmuEvent}
      />,
    );
    await typeAndSubmit("q");

    await waitFor(() => expect(onNcmuEvent).toHaveBeenCalledTimes(3));
    // Each call's first arg is a NcmuSseEvent envelope:
    // {event_type, run_id, timestamp, data}.
    const calls = onNcmuEvent.mock.calls;
    expect(calls[0][0]).toMatchObject({
      event_type: "node_started",
      run_id: "r1",
      timestamp: "t1",
      data: { node_id: "n1", node_type: "start" },
    });
    expect(calls[1][0]).toMatchObject({
      event_type: "node_finished",
      data: { status: "succeeded" },
    });
    expect(calls[2][0]).toMatchObject({
      event_type: "workflow_finished",
      data: { outputs: { answer: "hi" } },
    });
  });

  it("TASK-70a (c) chat events (session_created / message / message_end) do not trigger onNcmuEvent", async () => {
    const frames =
      'event: ncmu.session_created\ndata: {"session_id":"s1"}\n\n' +
      'event: message\ndata: {"answer":"hi"}\n\n' +
      'event: message_end\ndata: {"message_id":"m"}\n\n';
    vi.stubGlobal("fetch", ncmuStreamFetchSpy(frames));

    const onNcmuEvent = vi.fn();
    render(
      <ChatWindow
        appId="app-1"
        sessionId={null}
        onNcmuEvent={onNcmuEvent}
      />,
    );
    await typeAndSubmit("q");

    await waitFor(() => expect(screen.getByText("hi")).toBeInTheDocument());
    expect(onNcmuEvent).not.toHaveBeenCalled();
  });

  it("TASK-70a (d) ★F-FRESH-2 streamEndpointOverride routes fetch to ${API_BASE}/<path> with single prefix", async () => {
    let capturedUrl = "";
    const fetchSpy = vi.fn(async (url: string | URL) => {
      capturedUrl = String(url);
      return new Response(
        new ReadableStream<Uint8Array>({
          start(c) {
            c.close();
          },
        }),
        { status: 200, headers: { "Content-Type": "text/event-stream" } },
      );
    });
    vi.stubGlobal("fetch", fetchSpy);

    render(
      <ChatWindow
        appId="app-1"
        sessionId={null}
        streamEndpointOverride="/workflow/apps/app-1/run"
      />,
    );
    await typeAndSubmit("q");
    await waitFor(() => expect(fetchSpy).toHaveBeenCalled());

    // Hits workflow endpoint (relative to API_BASE = "/api/v1/ncmu").
    expect(capturedUrl).toContain("/workflow/apps/app-1/run");
    // Did NOT fall back to chat endpoint.
    expect(capturedUrl).not.toContain("/chat/app-1");
    // F-FRESH-2: API_BASE prefix appears exactly once (no double-prefix
    // /api/v1/ncmu/api/v1/ncmu/... that would 404 in production).
    const matches = capturedUrl.match(/\/api\/v1\/ncmu/g) ?? [];
    expect(matches.length).toBe(1);
  });

  it("TASK-70a (e) body shape: workflow override = {inputs:{...}}, default = flat {query, conversation_id}", async () => {
    let lastBody: Record<string, unknown> | null = null;
    const fetchSpy = vi.fn(async (_url: string | URL, init?: RequestInit) => {
      lastBody = JSON.parse(String(init?.body ?? "null")) as Record<
        string,
        unknown
      >;
      return new Response(
        new ReadableStream<Uint8Array>({
          start(c) {
            c.close();
          },
        }),
        { status: 200, headers: { "Content-Type": "text/event-stream" } },
      );
    });
    vi.stubGlobal("fetch", fetchSpy);

    // Workflow shape: streamEndpointOverride present.
    const { unmount } = render(
      <ChatWindow
        appId="app-1"
        sessionId={null}
        streamEndpointOverride="/workflow/apps/app-1/run"
      />,
    );
    await typeAndSubmit("q1");
    await waitFor(() => expect(lastBody).not.toBeNull());
    expect(lastBody).toEqual({
      inputs: { query: "q1", conversation_id: "" },
    });
    unmount();
    lastBody = null;

    // Chat (default) shape: no override → flat body shape unchanged
    // from Phase 1 contract.
    render(<ChatWindow appId="app-1" sessionId={null} />);
    await typeAndSubmit("q2");
    await waitFor(() => expect(lastBody).not.toBeNull());
    expect(lastBody).toEqual({ query: "q2", conversation_id: null });
  });

  it("TASK-70a (f) ★H-FRESH-3 workflow_finished envelope injects answer into assistant bubble (override only)", async () => {
    // Frames intentionally include a `message` piece that the backend
    // would NOT actually emit on the workflow endpoint — this lets us
    // distinguish chat-path vs override-path: in override mode the
    // workflow_finished injection overwrites whatever message piece
    // landed; in chat mode the guard skips injection and the bubble
    // keeps the message-stream piece.
    const frames =
      'event: message\ndata: {"answer":"streaming-piece"}\n\n' +
      'event: message_end\ndata: {"message_id":"m"}\n\n' +
      'event: workflow_finished\ndata: {"event_type":"workflow_finished","run_id":"r1","timestamp":"t","data":{"status":"succeeded","outputs":{"answer":"hello world"}}}\n\n';

    // Path 1: streamEndpointOverride set → injection happens.
    // Single fetchSpy that matches any URL (override path is
    // /workflow/apps/app-1/run on first render, /chat/app-1 on
    // second).
    const fetchSpy = vi.fn(async () => {
      const enc = new TextEncoder();
      return new Response(
        new ReadableStream<Uint8Array>({
          start(c) {
            c.enqueue(enc.encode(frames));
            c.close();
          },
        }),
        { status: 200, headers: { "Content-Type": "text/event-stream" } },
      );
    });
    vi.stubGlobal("fetch", fetchSpy);

    const { unmount } = render(
      <ChatWindow
        appId="app-1"
        sessionId={null}
        streamEndpointOverride="/workflow/apps/app-1/run"
      />,
    );
    await typeAndSubmit("q");

    // Override path: workflow_finished envelope's
    // envelope.data.outputs.answer overwrites the bubble.
    await waitFor(() => {
      const list = document.querySelector('[data-slot="message-list"]');
      const assistants =
        list?.querySelectorAll('[data-role="assistant"]') ?? [];
      expect(assistants.length).toBeGreaterThan(0);
      const last = assistants[assistants.length - 1];
      expect(last.textContent).toContain("hello world");
    });
    unmount();

    // Path 2: no override → injection guard skips; bubble keeps the
    // message-stream piece accumulated by the chat path.
    render(<ChatWindow appId="app-1" sessionId={null} />);
    await typeAndSubmit("q");

    await waitFor(() => {
      const list = document.querySelector('[data-slot="message-list"]');
      const assistants =
        list?.querySelectorAll('[data-role="assistant"]') ?? [];
      const last = assistants[assistants.length - 1];
      expect(last?.textContent).toContain("streaming-piece");
      expect(last?.textContent).not.toContain("hello world");
    });
  });
});
