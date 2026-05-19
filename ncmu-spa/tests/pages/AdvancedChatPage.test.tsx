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

  // TASK-C 维度 33 (B-NEW-33): TASK-B backend ships error path as a
  // workflow_finished(failed/exception) envelope (no throw); chat-mode
  // pages surface it via the ChatWindow.onNcmuEvent boundary as a toast.
  // The page has no page-level submitting state (ChatWindow owns
  // streaming flag) so this is the only assertion at the page edge.
  it("(d) AC#2 error envelope — workflow_finished(failed) via onNcmuEvent → notification.error('上游错误')", async () => {
    const { notification } = await import("antd");
    const errorSpy = vi
      .spyOn(notification, "error")
      .mockImplementation(() => undefined);

    renderAt("/apps/test-app-id/chatflow");
    await waitFor(() => expect(chatWindowSpy).toHaveBeenCalled());

    const props = lastChatWindowProps();
    const onNcmuEvent = props.onNcmuEvent as (evt: NcmuSseEvent) => void;

    const errEvt: NcmuSseEvent = {
      event_type: "workflow_finished",
      run_id: "44444444-4444-4444-8444-444444444444",
      timestamp: "2026-05-18T00:00:00Z",
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

  // TASK-E (B-NEW-52) — regression lock: status='failed' → '上游错误' must
  // survive the nested-ternary expansion in src/pages/AdvancedChatPage.tsx:52.
  // Without this lock, a typo in the new 'timeout' branch could silently
  // collapse the failed/exception branches into "运行超时".
  it("test_e1_status_failed_unchanged_behavior — failed envelope still maps to '上游错误'", async () => {
    const { notification } = await import("antd");
    const errorSpy = vi
      .spyOn(notification, "error")
      .mockImplementation(() => undefined);

    renderAt("/apps/test-app-id/chatflow");
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
  // fallthrough into the success path. INDEP TASK-D 主审盲区 #11 「用户体
  // 验完整路径」实证 — backend Pydantic Literal +'timeout' 已落但 SPA TS
  // Literal + handler enum 未同步 → user 看到 timeout 时 page 默认走"成功"
  // 分支 silent fallthrough。本 test 锁定 fix 后 timeout → "运行超时" toast。
  it("test_e2_status_timeout_shows_timeout_message — timeout envelope shows '运行超时'", async () => {
    const { notification } = await import("antd");
    const errorSpy = vi
      .spyOn(notification, "error")
      .mockImplementation(() => undefined);

    renderAt("/apps/test-app-id/chatflow");
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

    // (a) showError called exactly once (not silent fallthrough into success path)
    expect(errorSpy).toHaveBeenCalledTimes(1);
    // (b) message literal "运行超时" — NOT "上游错误" or "运行异常"
    expect(errorSpy.mock.calls[0]![0]).toEqual(
      expect.objectContaining({
        message: "运行超时",
        description: "upstream timeout: TimeoutException('read timeout')",
      }),
    );
    // (c/d) the error description carries backend's literal error string
    // (字段级 — 守 memory `feedback_pre_existing_error_strict_validation`).
    errorSpy.mockRestore();
  });

  // TASK-F (B-NEW-53) — regression lock: status='exception' → '运行异常' must
  // survive the 4-way nested-ternary expansion in src/pages/AdvancedChatPage.tsx:64.
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

    renderAt("/apps/test-app-id/chatflow");
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
  // fallthrough into the success path. backend Pydantic Literal pre-existing
  // contains 'stopped' (schemas/sse_events.py:120 / 4-stack since v3.3.1)
  // but 4 page in-flight handler OR clause was missing the branch → when a
  // workflow is stopped (user cancel / Dify /stop API), chat-mode page sees
  // no visible feedback / form-mode renders empty output (silent UX
  // fallthrough). INDEP TASK-D M2 candidate B-NEW-53 升级实施。
  it("test_f2_status_stopped_shows_stopped_message — stopped envelope shows '运行已停止'", async () => {
    const { notification } = await import("antd");
    const errorSpy = vi
      .spyOn(notification, "error")
      .mockImplementation(() => undefined);

    renderAt("/apps/test-app-id/chatflow");
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

    // (a) showError called exactly once (not silent fallthrough into success path)
    expect(errorSpy).toHaveBeenCalledTimes(1);
    // (b) message literal "运行已停止" — NOT "上游错误" / "运行异常" / "运行超时"
    expect(errorSpy.mock.calls[0]![0]).toEqual(
      expect.objectContaining({
        message: "运行已停止",
        description: "Run stopped by user",
      }),
    );
    // (c/d) the error description carries backend's literal error string
    // (字段级 — 守 memory `feedback_pre_existing_error_strict_validation`).
    errorSpy.mockRestore();
  });
});
