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

  // TASK-C 维度 33 (B-NEW-33): TASK-B backend ships error path as a
  // workflow_finished(exception) envelope (no throw); agent-chat surfaces
  // it via the ChatWindow.onNcmuEvent boundary as a toast. Page has no
  // page-level submitting state (ChatWindow owns streaming flag).
  it("(e) AC#2 error envelope — workflow_finished(exception) via onNcmuEvent → notification.error('运行异常')", async () => {
    const { notification } = await import("antd");
    const errorSpy = vi
      .spyOn(notification, "error")
      .mockImplementation(() => undefined);

    renderAt("/apps/test-app-id/agent");
    await waitFor(() => expect(chatWindowSpy).toHaveBeenCalled());

    const props = lastChatWindowProps();
    const onNcmuEvent = props.onNcmuEvent as (evt: NcmuSseEvent) => void;

    const errEvt: NcmuSseEvent = {
      event_type: "workflow_finished",
      run_id: "55555555-5555-4555-8555-555555555555",
      timestamp: "2026-05-18T00:00:01Z",
      data: {
        status: "exception",
        outputs: {},
        error: "httpx.RequestError: upstream lost",
      },
    };

    await act(async () => {
      onNcmuEvent(errEvt);
    });

    expect(errorSpy).toHaveBeenCalledTimes(1);
    expect(errorSpy.mock.calls[0]![0]).toEqual(
      expect.objectContaining({
        message: "运行异常",
        description: "httpx.RequestError: upstream lost",
      }),
    );
    errorSpy.mockRestore();
  });

  // TASK-E (B-NEW-52) — regression lock: status='failed' → '上游错误' must
  // survive the nested-ternary expansion in src/pages/AgentChatPage.tsx:84.
  // Without this lock, a typo in the new 'timeout' branch could silently
  // collapse the failed/exception branches into "运行超时".
  it("test_e1_status_failed_unchanged_behavior — failed envelope still maps to '上游错误'", async () => {
    const { notification } = await import("antd");
    const errorSpy = vi
      .spyOn(notification, "error")
      .mockImplementation(() => undefined);

    renderAt("/apps/test-app-id/agent");
    await waitFor(() => expect(chatWindowSpy).toHaveBeenCalled());

    const props = lastChatWindowProps();
    const onNcmuEvent = props.onNcmuEvent as (evt: NcmuSseEvent) => void;

    const errEvt: NcmuSseEvent = {
      event_type: "workflow_finished",
      run_id: "e1e1e1e1-e1e1-4e1e-8e1e-e1e1e1e1e1e1",
      timestamp: "2026-05-18T01:00:00Z",
      data: {
        status: "failed",
        outputs: {},
        error: "upstream invalid_param",
      },
    };

    await act(async () => {
      onNcmuEvent(errEvt);
    });

    expect(errorSpy).toHaveBeenCalledTimes(1);
    expect(errorSpy.mock.calls[0]![0]).toEqual(
      expect.objectContaining({
        message: "上游错误",
        description: "upstream invalid_param",
      }),
    );
    errorSpy.mockRestore();
  });

  // TASK-E (B-NEW-52) — new branch: status='timeout' must NOT silent
  // fallthrough. INDEP TASK-D 主审盲区 #11 实证 — 本 test 锁定 fix 后 timeout
  // → "运行超时" toast。
  it("test_e2_status_timeout_shows_timeout_message — timeout envelope shows '运行超时'", async () => {
    const { notification } = await import("antd");
    const errorSpy = vi
      .spyOn(notification, "error")
      .mockImplementation(() => undefined);

    renderAt("/apps/test-app-id/agent");
    await waitFor(() => expect(chatWindowSpy).toHaveBeenCalled());

    const props = lastChatWindowProps();
    const onNcmuEvent = props.onNcmuEvent as (evt: NcmuSseEvent) => void;

    const timeoutEvt: NcmuSseEvent = {
      event_type: "workflow_finished",
      run_id: "e2e2e2e2-e2e2-4e2e-8e2e-e2e2e2e2e2e2",
      timestamp: "2026-05-18T01:00:01Z",
      data: {
        status: "timeout",
        outputs: {},
        error: "upstream timeout: TimeoutException('read timeout')",
      },
    };

    await act(async () => {
      onNcmuEvent(timeoutEvt);
    });

    expect(errorSpy).toHaveBeenCalledTimes(1);
    expect(errorSpy.mock.calls[0]![0]).toEqual(
      expect.objectContaining({
        message: "运行超时",
        description: "upstream timeout: TimeoutException('read timeout')",
      }),
    );
    errorSpy.mockRestore();
  });

  // TASK-F (B-NEW-53) — regression lock: status='exception' → '运行异常' must
  // survive the 4-way nested-ternary expansion in src/pages/AgentChatPage.tsx:91.
  // Without this lock, a typo in the new 'stopped' branch could silently
  // collapse the exception branch (now in the middle of a 4-way ternary)
  // into "运行已停止". test_e1 already locks 'failed' / test_e2 locks
  // 'timeout' — 'exception' is the only previously-uncovered middle branch
  // and the most-at-risk position during the 4-way expansion.
  it("test_f1_status_exception_unchanged_behavior — exception envelope still maps to '运行异常'", async () => {
    const { notification } = await import("antd");
    const errorSpy = vi
      .spyOn(notification, "error")
      .mockImplementation(() => undefined);

    renderAt("/apps/test-app-id/agent");
    await waitFor(() => expect(chatWindowSpy).toHaveBeenCalled());

    const props = lastChatWindowProps();
    const onNcmuEvent = props.onNcmuEvent as (evt: NcmuSseEvent) => void;

    const errEvt: NcmuSseEvent = {
      event_type: "workflow_finished",
      run_id: "f1f1f1f1-f1f1-4f1f-8f1f-f1f1f1f1f1f1",
      timestamp: "2026-05-19T01:00:00Z",
      data: {
        status: "exception",
        outputs: {},
        error: "internal orchestrator panic",
      },
    };

    await act(async () => {
      onNcmuEvent(errEvt);
    });

    expect(errorSpy).toHaveBeenCalledTimes(1);
    expect(errorSpy.mock.calls[0]![0]).toEqual(
      expect.objectContaining({
        message: "运行异常",
        description: "internal orchestrator panic",
      }),
    );
    errorSpy.mockRestore();
  });

  // TASK-F (B-NEW-53) — new branch: status='stopped' must NOT silent
  // fallthrough. backend Pydantic Literal pre-existing contains 'stopped'
  // (4-stack since v3.3.1) but 4 page in-flight handler OR clause was
  // missing the branch → workflow stopped 时 chat-mode 无视觉反馈
  // (silent UX fallthrough). INDEP TASK-D M2 candidate B-NEW-53 升级实施。
  it("test_f2_status_stopped_shows_stopped_message — stopped envelope shows '运行已停止'", async () => {
    const { notification } = await import("antd");
    const errorSpy = vi
      .spyOn(notification, "error")
      .mockImplementation(() => undefined);

    renderAt("/apps/test-app-id/agent");
    await waitFor(() => expect(chatWindowSpy).toHaveBeenCalled());

    const props = lastChatWindowProps();
    const onNcmuEvent = props.onNcmuEvent as (evt: NcmuSseEvent) => void;

    const stoppedEvt: NcmuSseEvent = {
      event_type: "workflow_finished",
      run_id: "f2f2f2f2-f2f2-4f2f-8f2f-f2f2f2f2f2f2",
      timestamp: "2026-05-19T01:00:01Z",
      data: {
        status: "stopped",
        outputs: {},
        error: "Run stopped by user",
      },
    };

    await act(async () => {
      onNcmuEvent(stoppedEvt);
    });

    expect(errorSpy).toHaveBeenCalledTimes(1);
    expect(errorSpy.mock.calls[0]![0]).toEqual(
      expect.objectContaining({
        message: "运行已停止",
        description: "Run stopped by user",
      }),
    );
    errorSpy.mockRestore();
  });
});
