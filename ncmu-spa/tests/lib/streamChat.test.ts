import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setAuth } from "@/lib/auth";
import { parseSseFrame, streamChat } from "@/lib/streamChat";

// Build a Response whose body is a ReadableStream that yields the given
// chunks (string segments). Each chunk is encoded into UTF-8; the consumer
// pipes through TextDecoderStream so frame splitting works on string text.
function makeStreamingResponse(chunks: string[], status = 200): Response {
  const enc = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const c of chunks) controller.enqueue(enc.encode(c));
      controller.close();
    },
  });
  return new Response(stream, {
    status,
    headers: { "Content-Type": "text/event-stream" },
  });
}

beforeEach(() => {
  setAuth("dev-jwt-fixture", { id: "u1", name: "Alice", is_active: true });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("parseSseFrame", () => {
  it("parses event: + data: pair into structured event", () => {
    const frame = 'event: message\ndata: {"answer":"hi"}';
    expect(parseSseFrame(frame)).toEqual({
      event: "message",
      data: { answer: "hi" },
    });
  });

  it("defaults to event=message when only data: present", () => {
    expect(parseSseFrame('data: {"x":1}')).toEqual({ event: "message", data: { x: 1 } });
  });

  it("ignores SSE comment / heartbeat lines starting with :", () => {
    expect(parseSseFrame(": ping")).toBeNull();
  });
});

describe("streamChat", () => {
  it("AC#9.1 happy path — yields session_created + message + message_end in order", async () => {
    const frames =
      'event: ncmu.session_created\ndata: {"session_id":"abc","conversation_id":"abc"}\n\n' +
      'event: message\ndata: {"answer":"hello"}\n\n' +
      'event: message_end\ndata: {"message_id":"m1","retriever_resources":[]}\n\n';
    vi.stubGlobal("fetch", vi.fn(async () => makeStreamingResponse([frames])));

    const events: Array<{ event: string; data: unknown }> = [];
    for await (const evt of streamChat("app-1", { query: "hi", conversation_id: null })) {
      events.push({ event: evt.event, data: evt.data });
    }

    expect(events).toEqual([
      { event: "ncmu.session_created", data: { session_id: "abc", conversation_id: "abc" } },
      { event: "message", data: { answer: "hello" } },
      { event: "message_end", data: { message_id: "m1", retriever_resources: [] } },
    ]);
  });

  it("AC#9.2 cancel via pre-aborted signal — fetch rejects with AbortError", async () => {
    // jsdom's fetch isn't real — stub it to honor signal.
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init?: RequestInit) => {
        if (init?.signal?.aborted) {
          // Match standard fetch semantics: aborted signal → AbortError
          const err = new Error("aborted");
          err.name = "AbortError";
          throw err;
        }
        return makeStreamingResponse([]);
      }),
    );

    const ctl = new AbortController();
    ctl.abort();
    const gen = streamChat("app-1", { query: "hi", conversation_id: null }, ctl.signal);
    await expect(gen.next()).rejects.toMatchObject({ name: "AbortError" });
  });

  it("AC#9.3 caller break triggers reader.cancel() via finally", async () => {
    const frames =
      'event: message\ndata: {"answer":"a"}\n\n' +
      'event: message\ndata: {"answer":"b"}\n\n' +
      'event: message\ndata: {"answer":"c"}\n\n';

    // Track whether the stream's underlying source was cancelled by the reader.
    let cancelCalled = false;
    const enc = new TextEncoder();
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(enc.encode(frames));
        // do not close — keep the reader hanging so cancel() observably fires
      },
      cancel() {
        cancelCalled = true;
      },
    });
    const resp = new Response(body, {
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
    });
    vi.stubGlobal("fetch", vi.fn(async () => resp));

    let received = 0;
    for await (const _evt of streamChat("app-1", { query: "hi", conversation_id: null })) {
      received += 1;
      if (received >= 1) break; // exit early — generator finally must cancel
    }

    // microtask flush so the finally's `await reader.cancel()` actually runs
    await Promise.resolve();
    await Promise.resolve();
    expect(cancelCalled).toBe(true);
  });

  it("AC#9.4 error frame — yielded as event=error preserving NCMU code", async () => {
    const frames = 'event: error\ndata: {"code":9001,"message":"AI 服务暂不可用"}\n\n';
    vi.stubGlobal("fetch", vi.fn(async () => makeStreamingResponse([frames])));

    const events = [];
    for await (const evt of streamChat("app-1", { query: "hi", conversation_id: null })) {
      events.push(evt);
    }
    expect(events).toHaveLength(1);
    expect(events[0]).toEqual({
      event: "error",
      data: { code: 9001, message: "AI 服务暂不可用" },
    });
  });

  it("AC#9.5 HTTP non-2xx — throws with status + body text", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("upstream down", { status: 503 })),
    );

    const gen = streamChat("app-1", { query: "hi", conversation_id: null });
    await expect(gen.next()).rejects.toThrow(/HTTP 503/);
  });

  it("rejects when no JWT present (AC#9 prerequisite)", async () => {
    sessionStorage.clear();
    const gen = streamChat("app-1", { query: "hi", conversation_id: null });
    await expect(gen.next()).rejects.toThrow(/no jwt/);
  });

  // -- TASK-70a (PLAN-FIX-3 F-NEW-1 + PLAN-FIX-4 F-FRESH-2) endpointPath param
  // Default = /chat/${appId} (Phase 1 chat baseline). Override is RELATIVE to
  // API_BASE — passing the full path "/api/v1/ncmu/..." would yield a
  // double-prefix 404 (F-FRESH-2 trap).

  it("TASK-70a default endpointPath fetches ${API_BASE}/chat/${appId}", async () => {
    let capturedUrl = "";
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string | URL) => {
        capturedUrl = String(url);
        return makeStreamingResponse([]);
      }),
    );

    const gen = streamChat("app-X", { query: "hi", conversation_id: null });
    // Drive the generator far enough to make the underlying fetch call.
    await gen.next();

    // API_BASE = "/api/v1/ncmu" (src/lib/api.ts:4) → full URL is
    // "/api/v1/ncmu/chat/app-X". Single API_BASE prefix.
    expect(capturedUrl).toContain("/chat/app-X");
    expect(
      (capturedUrl.match(/\/api\/v1\/ncmu/g) ?? []).length,
    ).toBe(1);
  });

  it("TASK-70a explicit endpointPath fetches ${API_BASE}/<path> (no /chat fallback)", async () => {
    let capturedUrl = "";
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string | URL) => {
        capturedUrl = String(url);
        return makeStreamingResponse([]);
      }),
    );

    const gen = streamChat(
      "app-X",
      { inputs: { query: "hi", conversation_id: "" } },
      undefined,
      "/workflow/apps/app-X/run",
    );
    await gen.next();

    // Hits the explicit override path …
    expect(capturedUrl).toContain("/workflow/apps/app-X/run");
    // … and does NOT fall back to /chat/<appId>.
    expect(capturedUrl).not.toContain("/chat/app-X");
    // F-FRESH-2: API_BASE appears exactly once.
    expect(
      (capturedUrl.match(/\/api\/v1\/ncmu/g) ?? []).length,
    ).toBe(1);
  });
});
