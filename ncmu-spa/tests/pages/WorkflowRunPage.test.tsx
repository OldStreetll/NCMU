// TASK-77 — WorkflowRunPage tests.
//
// Page contract under test (per plan §修法 line 2487-2510 + AC#2 a-e):
//   - Layout: Tabs(执行 / 历史). 执行 tab stacks Card(输入) + Card(节点流) +
//             Card(输出). 历史 tab renders RunHistoryList.
//   - Data:   useAppParameters(appId) → form schema; runWorkflow(appId,
//             values, onChunk) — onChunk receives string | NcmuSseEvent per
//             frame; workflow-mode page ignores string chunks and appends
//             NcmuSseEvent to nodeTrace; workflow_finished frames also
//             unwrap data.outputs into the output state.
//
// We mock `@/lib/api` (useAppParameters + runWorkflow) so tests don't hit
// fetch and we control the SSE callback synchronously. We mock
// `@/hooks/useWorkflowRuns` to keep RunHistoryList off the network — it is
// rendered live (not mocked) so the page-level test also catches
// integration regressions with the shared atomic component (same pattern as
// CompletionPage.test.tsx).
//
// NodeTraceViewer rendering rule (NodeTraceViewer.tsx:104-106): only
// node_started + node_finished envelope frames render <li>; the other
// envelope types (workflow_finished / agent_thought / tool_call / ping /
// error) return null. AC#2(c) "推 3 NcmuSseEvent → 收 3 行" therefore uses
// 3 node_* frames.

import {
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { NcmuSseEvent } from "@/lib/sse-types";

vi.mock("@/lib/api", async (importActual) => {
  const actual = await importActual<typeof import("@/lib/api")>();
  return {
    ...actual,
    useAppParameters: vi.fn(),
    runWorkflow: vi.fn(),
  };
});

vi.mock("@/hooks/useWorkflowRuns", () => ({
  useWorkflowRunList: vi.fn(),
}));

// Imports come AFTER vi.mock so the mocked exports resolve.
import { WorkflowRunPage } from "@/pages/WorkflowRunPage";
import { useAppParameters, runWorkflow } from "@/lib/api";
import type { ParameterSchema } from "@/components/workflow/DynamicInputForm";
import { useWorkflowRunList } from "@/hooks/useWorkflowRuns";

const mockUseAppParameters = vi.mocked(useAppParameters);
const mockRunWorkflow = vi.mocked(runWorkflow);
const mockUseWorkflowRunList = vi.mocked(useWorkflowRunList);

// Antd 5 Layout / Form / Tabs / List pull in Grid useBreakpoint which calls
// window.matchMedia. jsdom does not implement it; polyfill once here
// (mirrors RunHistoryList.test.tsx + ChatPage.test.tsx + CompletionPage.test.tsx).
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

beforeEach(() => {
  mockUseAppParameters.mockReset();
  mockRunWorkflow.mockReset();
  mockUseWorkflowRunList.mockReset();
  // RunHistoryList stays off the network unless a test overrides this.
  mockUseWorkflowRunList.mockReturnValue({
    data: [],
    isLoading: false,
    isError: false,
    error: null,
  });
  // Default: empty parameter schema unless a test sets one.
  mockUseAppParameters.mockReturnValue({
    data: [],
    isLoading: false,
    isError: false,
    error: null,
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

function renderAt(appId = "test-app") {
  return render(
    <MemoryRouter initialEntries={[`/apps/${appId}/workflow`]}>
      <Routes>
        <Route path="/apps/:appId/workflow" element={<WorkflowRunPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

function makeEvent(
  event_type: NcmuSseEvent["event_type"],
  data: Record<string, unknown>,
  idx = 0,
): NcmuSseEvent {
  return {
    event_type,
    run_id: `1111111${idx}-1111-4111-8111-111111111111`,
    timestamp: "2026-05-11T00:00:00Z",
    data,
  };
}

describe("<WorkflowRunPage> (TASK-77)", () => {
  it("AC#2(a) — Tabs renders 2 items: 执行 + 历史", () => {
    renderAt();
    // Both tab labels appear in the tab strip (antd 5 renders the tab nav
    // unconditionally regardless of the active pane).
    const runTab = screen.getByRole("tab", { name: "执行" });
    const historyTab = screen.getByRole("tab", { name: "历史" });
    expect(runTab).toBeInTheDocument();
    expect(historyTab).toBeInTheDocument();
    // First tab is active by default.
    expect(runTab.getAttribute("aria-selected")).toBe("true");
    expect(historyTab.getAttribute("aria-selected")).toBe("false");
  });

  it("AC#2(b) — DynamicInputForm + NodeTraceViewer + 输出 Card render in the 执行 tab on mount", () => {
    mockUseAppParameters.mockReturnValue({
      data: [
        {
          variable: "topic",
          label: "主题",
          type: "text",
          required: true,
        },
      ] as ParameterSchema[],
      isLoading: false,
      isError: false,
      error: null,
    });
    renderAt();
    // 1) DynamicInputForm renders → 提交 button visible (same regex
    //    CompletionPage.test.tsx uses to tolerate antd's inner spacing).
    expect(
      screen.getByRole("button", { name: /提\s?交/ }),
    ).toBeInTheDocument();
    // Form.Item label from useAppParameters.
    expect(screen.getByText("主题")).toBeInTheDocument();
    // 2) NodeTraceViewer renders the empty state (nodeTrace=[]) per
    //    NodeTraceViewer.tsx:26-27 — data-testid="node-trace-empty".
    expect(screen.getByTestId("node-trace-empty")).toBeInTheDocument();
    // 3) 输出 Card renders an empty JSON object dump.
    const out = screen.getByTestId("workflow-output");
    expect(out).toBeInTheDocument();
    expect(out.textContent).toBe("{}");
    // useAppParameters was called with the appId resolved from the URL.
    expect(mockUseAppParameters).toHaveBeenCalledWith("test-app");
  });

  it("AC#2(c) — submit + mock runWorkflow pushes 3 NcmuSseEvent → NodeTraceViewer renders 3 rows", async () => {
    let pushedOnChunk:
      | ((c: string | NcmuSseEvent) => void)
      | null = null;
    mockRunWorkflow.mockImplementation(async (_appId, _values, onChunk) => {
      pushedOnChunk = onChunk;
    });

    renderAt("app-77");
    fireEvent.click(screen.getByRole("button", { name: /提\s?交/ }));

    // runWorkflow received (appId, values, callback). appId from useParams.
    await waitFor(() => {
      expect(mockRunWorkflow).toHaveBeenCalledTimes(1);
    });
    const [appIdArg, valuesArg, cbArg] = mockRunWorkflow.mock.calls[0]!;
    expect(appIdArg).toBe("app-77");
    expect(typeof valuesArg).toBe("object");
    expect(typeof cbArg).toBe("function");
    expect(pushedOnChunk).toBeTruthy();

    // Push 3 node_* frames through the captured callback — NodeTraceViewer
    // renders <li data-testid="node-trace-row"> for node_started / node_finished
    // only, so all 3 frames must be node_* to produce "3 行" (其他 event_type
    // 走 NodeTraceViewer.tsx:104 → return null).
    const frames: NcmuSseEvent[] = [
      makeEvent(
        "node_started",
        { node_id: "n1", node_type: "llm", title: "节点 1", inputs: {} },
        1,
      ),
      makeEvent(
        "node_finished",
        {
          node_id: "n1",
          node_type: "llm",
          status: "succeeded",
          outputs: {},
          elapsed_ms: 12,
        },
        2,
      ),
      makeEvent(
        "node_started",
        { node_id: "n2", node_type: "code", title: "节点 2", inputs: {} },
        3,
      ),
    ];
    await act(async () => {
      for (const f of frames) pushedOnChunk!(f);
    });

    // Empty state replaced; 3 rows now rendered.
    expect(screen.queryByTestId("node-trace-empty")).not.toBeInTheDocument();
    const rows = screen.getAllByTestId("node-trace-row");
    expect(rows).toHaveLength(3);
    expect(rows[0].getAttribute("data-event-type")).toBe("node_started");
    expect(rows[1].getAttribute("data-event-type")).toBe("node_finished");
    expect(rows[2].getAttribute("data-event-type")).toBe("node_started");
  });

  it("AC#2(d) — workflow_finished frame → output state updates to data.outputs", async () => {
    let pushedOnChunk:
      | ((c: string | NcmuSseEvent) => void)
      | null = null;
    mockRunWorkflow.mockImplementation(async (_a, _v, onChunk) => {
      pushedOnChunk = onChunk;
    });

    renderAt();
    fireEvent.click(screen.getByRole("button", { name: /提\s?交/ }));
    await waitFor(() => expect(mockRunWorkflow).toHaveBeenCalledTimes(1));

    const finished = makeEvent("workflow_finished", {
      status: "succeeded",
      outputs: { answer: "ok", elapsed_ms: 42 },
      total_elapsed_ms: 1234,
    });
    await act(async () => {
      pushedOnChunk!(finished);
    });

    const outEl = screen.getByTestId("workflow-output");
    // JSON.stringify(output, null, 2) — pretty-printed; assert field-level
    // contents reach the DOM (字段级实证 per plan AC#2 字面).
    expect(outEl.textContent).toContain('"answer"');
    expect(outEl.textContent).toContain('"ok"');
    expect(outEl.textContent).toContain('"elapsed_ms"');
    expect(outEl.textContent).toContain("42");
    // Empty placeholder {} replaced.
    expect(outEl.textContent).not.toBe("{}");
  });

  it("AC#2(e) — clicking 历史 tab reveals RunHistoryList", async () => {
    // Override useWorkflowRunList for this test so RunHistoryList renders
    // its visible empty-state copy ("暂无运行历史" — RunHistoryList.tsx:71).
    mockUseWorkflowRunList.mockReturnValue({
      data: [],
      isLoading: false,
      isError: false,
      error: null,
    });
    renderAt();

    // History tab is not active initially — antd 5 Tabs lazy-mounts inactive
    // panes (forceRender unset), so RunHistoryList's empty-state copy is not
    // in the DOM yet.
    expect(screen.queryByText("暂无运行历史")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "历史" }));

    // After tab switch the history pane mounts and RunHistoryList renders.
    await waitFor(() => {
      expect(screen.getByText("暂无运行历史")).toBeInTheDocument();
    });
    // Tab aria-selected flipped.
    expect(
      screen.getByRole("tab", { name: "历史" }).getAttribute("aria-selected"),
    ).toBe("true");
    // useWorkflowRunList called with the URL appId.
    expect(mockUseWorkflowRunList).toHaveBeenCalledWith("test-app");
  });

  // TASK-C 维度 33 + 36 (B-NEW-33 + B-NEW-36): AC-INDEP-1 / AC-INDEP-2
  // symmetric coverage for the workflow page — mirror CompletionPage's
  // INDEP-1/-2 contract so both pages that drive runWorkflow directly
  // pin the same error UI behaviour.

  it("AC-INDEP-1 — workflow_finished{status:'failed'} via onChunk → notification.error('上游错误') + submitting settles", async () => {
    mockUseAppParameters.mockReturnValue({
      data: [
        { variable: "topic", label: "主题", type: "text" },
      ] as ParameterSchema[],
      isLoading: false,
      isError: false,
      error: null,
    });

    let pushedOnChunk:
      | ((c: string | NcmuSseEvent) => void)
      | null = null;
    mockRunWorkflow.mockImplementation(async (_a, _v, onChunk) => {
      pushedOnChunk = onChunk;
    });
    const { notification } = await import("antd");
    const errorSpy = vi
      .spyOn(notification, "error")
      .mockImplementation(() => undefined);

    renderAt();
    const submitBtn = screen.getByRole("button", { name: /提\s?交/ });
    fireEvent.click(submitBtn);

    await waitFor(() => expect(mockRunWorkflow).toHaveBeenCalledTimes(1));

    const errEvt = makeEvent("workflow_finished", {
      status: "failed",
      outputs: {},
      error: "upstream X",
    });

    await act(async () => {
      pushedOnChunk!(errEvt);
    });

    await waitFor(() => {
      expect(errorSpy).toHaveBeenCalledTimes(1);
    });
    expect(errorSpy.mock.calls[0]![0]).toEqual(
      expect.objectContaining({
        message: "上游错误",
        description: "upstream X",
      }),
    );

    // submitting settles — the onChunk branch sets it to false before the
    // page's finally also fires.
    await waitFor(() => {
      expect(submitBtn.className).not.toContain("ant-btn-loading");
    });
    errorSpy.mockRestore();
  });

  // TASK-C REWORK-INDEP-I-1 (AC-INDEP-3): symmetric coverage with
  // CompletionPage — see that test's docstring for the full rationale.
  // AbortError is the cancel path, NOT a failure; no toast must fire.
  it("AC-INDEP-3 — AbortError rejection does NOT show toast (regression for I-1)", async () => {
    mockUseAppParameters.mockReturnValue({
      data: [
        { variable: "topic", label: "主题", type: "text" },
      ] as ParameterSchema[],
      isLoading: false,
      isError: false,
      error: null,
    });
    mockRunWorkflow.mockImplementation(async () => {
      const e = new Error("The user aborted a request");
      e.name = "AbortError";
      throw e;
    });
    const { notification } = await import("antd");
    const errorSpy = vi
      .spyOn(notification, "error")
      .mockImplementation(() => undefined);

    renderAt();
    const submitBtn = screen.getByRole("button", { name: /提\s?交/ });
    fireEvent.click(submitBtn);

    // finally branch must settle submitting even on the early-return path.
    await waitFor(() => {
      expect(submitBtn.className).not.toContain("ant-btn-loading");
    });

    expect(errorSpy).not.toHaveBeenCalled();
    errorSpy.mockRestore();
  });

  it("AC-INDEP-2 — runWorkflow rejection → notification.error('执行失败') + submitting settles", async () => {
    mockUseAppParameters.mockReturnValue({
      data: [
        { variable: "topic", label: "主题", type: "text" },
      ] as ParameterSchema[],
      isLoading: false,
      isError: false,
      error: null,
    });
    mockRunWorkflow.mockImplementation(async () => {
      throw new Error("boom");
    });
    const { notification } = await import("antd");
    const errorSpy = vi
      .spyOn(notification, "error")
      .mockImplementation(() => undefined);

    renderAt();
    const submitBtn = screen.getByRole("button", { name: /提\s?交/ });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(errorSpy).toHaveBeenCalledTimes(1);
    });
    // submitting must settle even on the rejection path (finally branch).
    await waitFor(() => {
      expect(submitBtn.className).not.toContain("ant-btn-loading");
    });
    errorSpy.mockRestore();
  });
});
