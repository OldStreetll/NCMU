// TASK-78 — AgentChatPage (Phase 2B B3) wiring tests.
//
// AgentChatPage routes /apps/:appId/agent → ChatWindow (main) + Sider Tabs
// with AgentThoughtTimeline + ToolCallCard. ChatWindow is mocked here so the
// assertions focus on THIS page's wiring (the same mock pattern as
// AdvancedChatPage.test.tsx — vi.hoisted spy captures every props snapshot).
//
// 4 cases (plan §AC#2 字面 a/b/c/d)：
//   (a) renders ChatWindow + Sider with 2 Tabs (Agent Thoughts / Tool Calls)
//   (b) onNcmuEvent push agent_thought → AgentThoughtTimeline 收 1 行
//   (c) onNcmuEvent push tool_call → ToolCallCard 渲染 1 个
//   (d) appId 从 URL 解析 + workflow streamEndpoint wired

import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { NcmuSseEvent } from "@/lib/sse-types";

// Hoisted spy — vi.mock factories are hoisted above imports, so the spy must
// also be hoisted to be referenced inside the factory.
const { chatWindowSpy } = vi.hoisted(() => ({ chatWindowSpy: vi.fn() }));

vi.mock("@/components/ChatWindow", () => ({
  ChatWindow: (props: Record<string, unknown>) => {
    chatWindowSpy(props);
    return <div data-testid="mock-chat-window" />;
  },
}));

// MUST come after vi.mock so the resolution sees the mocked module.
import { AgentChatPage } from "@/pages/AgentChatPage";

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
        <Route path="/apps/:appId/agent" element={<AgentChatPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

function lastChatWindowProps(): Record<string, unknown> {
  const calls = chatWindowSpy.mock.calls;
  expect(calls.length).toBeGreaterThan(0);
  return calls.at(-1)?.[0] as Record<string, unknown>;
}

describe("<AgentChatPage> (TASK-78)", () => {
  it("(a) renders ChatWindow + Sider with 2 Tabs (Agent Thoughts / Tool Calls)", async () => {
    renderAt("/apps/test-app-id/agent");

    await waitFor(() => expect(chatWindowSpy).toHaveBeenCalled());
    expect(screen.getByTestId("mock-chat-window")).toBeInTheDocument();

    // antd 5 Tabs renders the tab strip as role=tab elements.
    expect(
      screen.getByRole("tab", { name: "Agent Thoughts" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("tab", { name: "Tool Calls" }),
    ).toBeInTheDocument();

    // Empty-state for AgentThoughtTimeline (active tab by default).
    expect(screen.getByTestId("agent-thought-empty")).toBeInTheDocument();
  });

  it("(b) onNcmuEvent push agent_thought → AgentThoughtTimeline 收 1 行", async () => {
    renderAt("/apps/test-app-id/agent");
    await waitFor(() => expect(chatWindowSpy).toHaveBeenCalled());

    const props = lastChatWindowProps();
    const onNcmuEvent = props.onNcmuEvent as (evt: NcmuSseEvent) => void;
    expect(typeof onNcmuEvent).toBe("function");

    const evt: NcmuSseEvent = {
      event_type: "agent_thought",
      run_id: "22222222-2222-4222-8222-222222222222",
      timestamp: "2026-05-11T00:00:00Z",
      data: {
        thought: "I should call the calculator tool.",
        tool_name: "calculator",
      },
    };

    await act(async () => {
      onNcmuEvent(evt);
    });

    expect(screen.queryByTestId("agent-thought-empty")).not.toBeInTheDocument();
    const rows = screen.getAllByTestId("agent-thought-row");
    expect(rows).toHaveLength(1);
    expect(rows[0].textContent).toContain("I should call the calculator tool.");
  });

  it("(c) onNcmuEvent push tool_call → ToolCallCard 渲染 1 个", async () => {
    renderAt("/apps/test-app-id/agent");
    await waitFor(() => expect(chatWindowSpy).toHaveBeenCalled());

    const props = lastChatWindowProps();
    const onNcmuEvent = props.onNcmuEvent as (evt: NcmuSseEvent) => void;

    const evt: NcmuSseEvent = {
      event_type: "tool_call",
      run_id: "33333333-3333-4333-8333-333333333333",
      timestamp: "2026-05-11T00:00:01Z",
      data: {
        tool_name: "calculator",
        tool_input: { expression: "1 + 1" },
        tool_output: 2,
        status: "completed",
      },
    };

    await act(async () => {
      onNcmuEvent(evt);
    });

    // Activate the "Tool Calls" tab to mount the ToolCallCard list. antd 5
    // Tabs lazy-mounts inactive panels — same pattern as
    // WorkflowRunPage.test.tsx AC#2(e).
    await act(async () => {
      fireEvent.click(screen.getByRole("tab", { name: "Tool Calls" }));
    });

    await waitFor(() => {
      const cards = screen.getAllByTestId("tool-call-card");
      expect(cards).toHaveLength(1);
      expect(cards[0].getAttribute("data-status")).toBe("completed");
    });
  });

  it("(d) appId resolved from URL param + workflow streamEndpoint wired", async () => {
    renderAt("/apps/test-app-id/agent");
    await waitFor(() => expect(chatWindowSpy).toHaveBeenCalled());

    const props = lastChatWindowProps();
    expect(props.appId).toBe("test-app-id");
    // F-NEW-1 (PLAN-FIX-3) + F-FRESH-2 (PLAN-FIX-4): workflow path RELATIVE
    // to API_BASE (= "/api/v1/ncmu"); no double-prefix.
    expect(props.streamEndpointOverride).toBe(
      "/workflow/apps/test-app-id/run",
    );
  });
});
