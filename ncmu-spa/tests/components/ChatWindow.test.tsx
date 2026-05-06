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
});
