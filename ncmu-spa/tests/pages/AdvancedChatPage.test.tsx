// TASK-75 — AdvancedChatPage (Phase 2B B3) wiring tests.
//
// AdvancedChatPage's job is small but critical: route /apps/:appId/chatflow
// → resolve appId from URL params → render ChatWindow (left) + NodeTraceViewer
// (right) → forward ChatWindow's onNcmuEvent envelopes into NodeTraceViewer's
// nodeTrace state. ChatWindow itself is mocked here so the assertions focus on
// THIS page's wiring (the same mock pattern is used in ChatPage.test.tsx —
// vi.hoisted spy captures every props snapshot for inspection).

import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { NcmuSseEvent } from "@/lib/sse-types";

// Hoisted spy — vi.mock factories are hoisted above imports, so the spy
// must be hoisted too in order to be referenced from the factory.
const { chatWindowSpy } = vi.hoisted(() => ({ chatWindowSpy: vi.fn() }));

vi.mock("@/components/ChatWindow", () => ({
  ChatWindow: (props: Record<string, unknown>) => {
    chatWindowSpy(props);
    return <div data-testid="mock-chat-window" />;
  },
}));

// MUST come after vi.mock so the resolution sees the mocked module.
import { AdvancedChatPage } from "@/pages/AdvancedChatPage";

// Antd 5 Layout pulls in the Grid useBreakpoint hook which calls
// window.matchMedia. jsdom does not implement it; polyfill once here.
beforeAll(() => {
  if (!window.matchMedia) {
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      writable: true,
      value: (query: string): MediaQueryList =>
        ({
          matches: false,
          media: query,
          onchange: null,
          addListener: () => {},
          removeListener: () => {},
          addEventListener: () => {},
          removeEventListener: () => {},
          dispatchEvent: () => false,
        }) as MediaQueryList,
    });
  }
});

afterEach(() => {
  chatWindowSpy.mockClear();
});

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/apps/:appId/chatflow" element={<AdvancedChatPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

function lastChatWindowProps(): Record<string, unknown> {
  const calls = chatWindowSpy.mock.calls;
  expect(calls.length).toBeGreaterThan(0);
  return calls.at(-1)?.[0] as Record<string, unknown>;
}

describe("<AdvancedChatPage> (TASK-75)", () => {
  it("(a) renders ChatWindow + NodeTraceViewer (empty state) on mount", async () => {
    renderAt("/apps/test-app-id/chatflow");

    // ChatWindow mock renders synchronously on first paint.
    await waitFor(() => expect(chatWindowSpy).toHaveBeenCalled());
    expect(screen.getByTestId("mock-chat-window")).toBeInTheDocument();
    // NodeTraceViewer renders the empty-state slot when nodeTrace=[]
    // (NodeTraceViewer.tsx:26 — data-testid="node-trace-empty").
    expect(screen.getByTestId("node-trace-empty")).toBeInTheDocument();
  });

  it("(b) onNcmuEvent push → NodeTraceViewer renders 1 row", async () => {
    renderAt("/apps/test-app-id/chatflow");
    await waitFor(() => expect(chatWindowSpy).toHaveBeenCalled());

    const props = lastChatWindowProps();
    const onNcmuEvent = props.onNcmuEvent as (evt: NcmuSseEvent) => void;
    expect(typeof onNcmuEvent).toBe("function");

    const evt: NcmuSseEvent = {
      event_type: "node_started",
      run_id: "11111111-1111-4111-8111-111111111111",
      timestamp: "2026-05-11T00:00:00Z",
      data: {
        node_id: "node-1",
        node_type: "llm",
        title: "节点 1",
        inputs: {},
      },
    };

    // setState is async in React 18 — wrap in act so the re-render flushes
    // before assertions.
    await act(async () => {
      onNcmuEvent(evt);
    });

    // node-trace-list now replaces node-trace-empty (NodeTraceViewer.tsx:33-36).
    expect(screen.queryByTestId("node-trace-empty")).not.toBeInTheDocument();
    const rows = screen.getAllByTestId("node-trace-row");
    expect(rows).toHaveLength(1);
    expect(rows[0].getAttribute("data-event-type")).toBe("node_started");
  });

  it("(c) appId resolved from URL param + workflow streamEndpoint wired", async () => {
    renderAt("/apps/test-app-id/chatflow");
    await waitFor(() => expect(chatWindowSpy).toHaveBeenCalled());

    const props = lastChatWindowProps();
    expect(props.appId).toBe("test-app-id");
    // F-NEW-1 (PLAN-FIX-3): advanced-chat MUST hit workflow endpoint or
    // dispatcher.dispatch is bypassed → spec §6.3 AC unreachable.
    // F-FRESH-2 (PLAN-FIX-4): path RELATIVE to API_BASE — no /api/v1/ncmu
    // prefix or fetch yields a double-prefix 404.
    expect(props.streamEndpointOverride).toBe(
      "/workflow/apps/test-app-id/run",
    );
  });
});
