// TASK-76 — CompletionPage tests.
//
// Page contract under test (per plan §修法 line 2358-2402):
//   - Layout: DynamicInputForm + 输出 Card 主区 / RunHistoryList 历史侧栏
//   - Data:   useAppParameters(appId) → form schema; runWorkflow(appId,
//             values, onChunk) → SSE text stream → output state append
//   - State:  submitting toggles button loading
//
// We mock the whole `@/lib/api` module to control useAppParameters +
// runWorkflow without hitting fetch (M-FRESH-3 backlog: backend
// /parameters endpoint not implemented; tests must be self-contained).
// We mock `@/hooks/useWorkflowRuns` to keep RunHistoryList off the
// network — it is rendered live (not mocked) so this page-level test
// also catches an accidental import or layout regression in the
// integration with the shared atomic component.

import {
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

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
import { notification } from "antd";
import { CompletionPage } from "@/pages/CompletionPage";
import { useAppParameters, runWorkflow } from "@/lib/api";
import type { ParameterSchema } from "@/components/workflow/DynamicInputForm";
import { useWorkflowRunList } from "@/hooks/useWorkflowRuns";
import type { NcmuSseEvent } from "@/lib/sse-types";

const mockUseAppParameters = vi.mocked(useAppParameters);
const mockRunWorkflow = vi.mocked(runWorkflow);
const mockUseWorkflowRunList = vi.mocked(useWorkflowRunList);

// Antd 5 Layout / Form / List pull in the Grid useBreakpoint hook which
// calls window.matchMedia. jsdom does not implement it; polyfill once
// here (mirrors RunHistoryList.test.tsx + ChatPage.test.tsx pattern).
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
});

afterEach(() => {
  vi.restoreAllMocks();
});

function renderAt(appId = "test-app") {
  return render(
    <MemoryRouter initialEntries={[`/apps/${appId}/completion`]}>
      <Routes>
        <Route path="/apps/:appId/completion" element={<CompletionPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("<CompletionPage>", () => {
  it("AC#2(a) — renders DynamicInputForm + 输出 Card + RunHistoryList (3 zones)", () => {
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
    // 1) DynamicInputForm rendered → 提交 button present (text 提交).
    expect(
      screen.getByRole("button", { name: /提\s?交/ }),
    ).toBeInTheDocument();
    // 2) 输出 Card title rendered.
    expect(screen.getByText("输出")).toBeInTheDocument();
    // 3) 历史 sider header rendered.
    expect(screen.getByText("历史")).toBeInTheDocument();
    // 4) Empty placeholder for the output region while no run has been
    //    executed yet (assert the literal copy from the page).
    expect(screen.getByText("（待执行）")).toBeInTheDocument();
  });

  it("AC#2(b) — useAppParameters returns 3 parameters → form renders 3 input fields", () => {
    const params: ParameterSchema[] = [
      { variable: "topic", label: "主题", type: "text", required: true },
      { variable: "tone", label: "语气", type: "select", options: ["正式", "活泼"] },
      { variable: "length", label: "字数", type: "number" },
    ];
    mockUseAppParameters.mockReturnValue({
      data: params,
      isLoading: false,
      isError: false,
      error: null,
    });
    renderAt();
    // Each parameter is rendered as a Form.Item with the `label` text.
    for (const p of params) {
      expect(screen.getByText(p.label)).toBeInTheDocument();
    }
    // useAppParameters called with the appId resolved from the URL.
    expect(mockUseAppParameters).toHaveBeenCalledWith("test-app");
  });

  it("AC#2(c) — submit calls runWorkflow(appId, values, fn); button toggles loading while pending then settles", async () => {
    mockUseAppParameters.mockReturnValue({
      data: [
        { variable: "topic", label: "主题", type: "text" },
      ] as ParameterSchema[],
      isLoading: false,
      isError: false,
      error: null,
    });
    // runWorkflow returns a manually-resolvable promise so we can observe
    // the submitting=true window before letting it settle.
    let resolveRun: (() => void) | null = null;
    mockRunWorkflow.mockImplementation(
      () =>
        new Promise<void>((r) => {
          resolveRun = () => r();
        }),
    );

    renderAt("app-42");

    const submitBtn = screen.getByRole("button", { name: /提\s?交/ });
    // Pre-click: the button must not be in the loading state.
    expect(submitBtn.className).not.toContain("ant-btn-loading");

    fireEvent.click(submitBtn);

    // submitting=true → antd Button adds the loading class.
    await waitFor(() => {
      expect(submitBtn.className).toContain("ant-btn-loading");
    });
    // runWorkflow received (appId, values, callback) — appId from useParams,
    // values from antd Form (empty {} when no inputs touched is acceptable;
    // the topic field has no `required` rule here so submit fires).
    expect(mockRunWorkflow).toHaveBeenCalledTimes(1);
    const [appIdArg, valuesArg, cbArg] = mockRunWorkflow.mock.calls[0]!;
    expect(appIdArg).toBe("app-42");
    expect(typeof valuesArg).toBe("object");
    expect(typeof cbArg).toBe("function");

    // Settle the in-flight runWorkflow → submitting=false → loading class
    // removed (and onSubmit's finally runs).
    resolveRun!();
    await waitFor(() => {
      expect(submitBtn.className).not.toContain("ant-btn-loading");
    });
  });

  it("AC#2(d) — runWorkflow's onChunk callback appends to the output region", async () => {
    mockUseAppParameters.mockReturnValue({
      data: [
        { variable: "topic", label: "主题", type: "text" },
      ] as ParameterSchema[],
      isLoading: false,
      isError: false,
      error: null,
    });
    // Simulate completion-mode SSE: two text chunks arrive in order.
    mockRunWorkflow.mockImplementation(async (_appId, _values, onChunk) => {
      onChunk("Hello, ");
      onChunk("world!");
    });

    renderAt();
    fireEvent.click(screen.getByRole("button", { name: /提\s?交/ }));

    // Page accumulates chunks via setOutput(prev => prev + chunk) — the
    // final concatenated string must reach the DOM.
    await waitFor(() => {
      expect(screen.getByText("Hello, world!")).toBeInTheDocument();
    });
    // The placeholder must have been replaced.
    expect(screen.queryByText("（待执行）")).not.toBeInTheDocument();
  });

  // REWORK-76-INDEP / C-INDEP-1: backend completion.py orchestrator
  // (`ncmu-backend/.../workflow/completion.py`) accumulates Dify upstream
  // `message` / `text_chunk` frames into `accumulated_text` and emits a
  // SINGLE `workflow_finished` NcmuSseEvent envelope at `message_end` —
  // it never emits a `message` event on the NCMU SSE stream. Pre-fix,
  // CompletionPage filtered to `typeof chunk === "string"` only and
  // silently dropped the envelope, leaving the output Card permanently
  // empty in production. This test asserts the envelope branch wires
  // `data.outputs.answer` straight into the output region.
  it("AC-INDEP-1 — workflow_finished envelope → outputs.answer rendered (real backend shape)", async () => {
    mockUseAppParameters.mockReturnValue({
      data: [
        { variable: "topic", label: "主题", type: "text" },
      ] as ParameterSchema[],
      isLoading: false,
      isError: false,
      error: null,
    });

    const envelope: NcmuSseEvent = {
      event_type: "workflow_finished",
      run_id: "00000000-0000-4000-8000-000000000abc",
      timestamp: "2026-05-11T10:00:00Z",
      data: {
        status: "succeeded",
        outputs: { answer: "Hello world from completion orchestrator." },
        total_elapsed_ms: 42,
      },
    };
    mockRunWorkflow.mockImplementation(async (_appId, _values, onChunk) => {
      onChunk(envelope);
    });

    renderAt();
    fireEvent.click(screen.getByRole("button", { name: /提\s?交/ }));

    await waitFor(() => {
      expect(
        screen.getByText("Hello world from completion orchestrator."),
      ).toBeInTheDocument();
    });
    // Placeholder gone — the envelope set output state.
    expect(screen.queryByText("（待执行）")).not.toBeInTheDocument();
  });

  // REWORK-76-INDEP / I-INDEP-2 (76 部分): handleSubmit had no try/catch,
  // so a runWorkflow rejection would bubble to React as an unhandled
  // promise. Post-fix it surfaces via antd notification.error and the
  // submitting state still settles back to false.
  it("AC-INDEP-2 — runWorkflow rejection → notification.error called + submitting settles", async () => {
    mockUseAppParameters.mockReturnValue({
      data: [
        { variable: "topic", label: "主题", type: "text" },
      ] as ParameterSchema[],
      isLoading: false,
      isError: false,
      error: null,
    });
    mockRunWorkflow.mockImplementation(async () => {
      throw new Error("upstream 502");
    });
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
  });

  // TASK-C 维度 33 (B-NEW-33): TASK-B backend now ships error path as a
  // workflow_finished(exception) envelope (no throw); the prior catch(err)
  // branch never fires for this path. AC-INDEP-1 (above) covers the
  // succeeded envelope; this test covers the exception envelope so both
  // status flavours are pinned. submitting must also settle (the onChunk
  // branch sets it to false before the page's `finally` also fires).
  it("AC#2 error envelope — workflow_finished(exception) → notification.error('运行异常') + submitting settles", async () => {
    mockUseAppParameters.mockReturnValue({
      data: [
        { variable: "topic", label: "主题", type: "text" },
      ] as ParameterSchema[],
      isLoading: false,
      isError: false,
      error: null,
    });

    const envelope: NcmuSseEvent = {
      event_type: "workflow_finished",
      run_id: "66666666-6666-4666-8666-666666666666",
      timestamp: "2026-05-18T00:00:02Z",
      data: {
        status: "exception",
        outputs: {},
        error: "RuntimeError: late error",
      },
    };
    mockRunWorkflow.mockImplementation(async (_appId, _values, onChunk) => {
      onChunk(envelope);
    });
    const errorSpy = vi
      .spyOn(notification, "error")
      .mockImplementation(() => undefined);

    renderAt();
    const submitBtn = screen.getByRole("button", { name: /提\s?交/ });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(errorSpy).toHaveBeenCalledTimes(1);
    });
    expect(errorSpy.mock.calls[0]![0]).toEqual(
      expect.objectContaining({
        message: "运行异常",
        description: "RuntimeError: late error",
      }),
    );

    await waitFor(() => {
      expect(submitBtn.className).not.toContain("ant-btn-loading");
    });
  });

  // TASK-C REWORK-INDEP-I-1 (AC-INDEP-3): when AbortController.abort()
  // fires (unmount cleanup or handleSubmit's cancel-previous), fetch
  // rejects the in-flight runWorkflow promise with `Error.name ===
  // "AbortError"`. That's a normal user action, NOT a failure — surfacing
  // it as a "执行失败" toast misleads users who clicked away or hit cancel.
  // The page's catch block must short-circuit on AbortError exactly like
  // ChatWindow:427-428 already does. submitting must still settle in the
  // finally branch so a re-mount finds a clean state.
  it("AC-INDEP-3 — AbortError rejection does NOT show toast (regression for I-1)", async () => {
    mockUseAppParameters.mockReturnValue({
      data: [
        { variable: "topic", label: "主题", type: "text" },
      ] as ParameterSchema[],
      isLoading: false,
      isError: false,
      error: null,
    });
    // Synthesize the rejection shape Web Fetch produces on abort: an
    // Error subclass whose `.name === "AbortError"`. The page's guard
    // checks `err instanceof Error && err.name === "AbortError"`.
    mockRunWorkflow.mockImplementation(async () => {
      const e = new Error("The user aborted a request");
      e.name = "AbortError";
      throw e;
    });
    const errorSpy = vi
      .spyOn(notification, "error")
      .mockImplementation(() => undefined);

    renderAt();
    const submitBtn = screen.getByRole("button", { name: /提\s?交/ });
    fireEvent.click(submitBtn);

    // Wait for the rejection to flow through the page (the catch branch
    // either returns early or fires notification.error). We can't await
    // a sentinel from the page directly, so wait for the submitting flag
    // to settle — the finally clause runs regardless of the early return.
    await waitFor(() => {
      expect(submitBtn.className).not.toContain("ant-btn-loading");
    });

    // The critical assertion: NO toast was shown for the abort path.
    expect(errorSpy).not.toHaveBeenCalled();
    errorSpy.mockRestore();
  });

  // TASK-C 维度 34 (B-NEW-34): on unmount the page must abort any
  // in-flight SSE so background fetch doesn't keep pumping into setState
  // on a torn-down tree. mockRunWorkflow keeps the promise pending; we
  // capture the signal handed to runWorkflow at click time and assert
  // .aborted flips true after unmount fires the useEffect cleanup.
  it("AC#3 abort — unmount aborts in-flight runWorkflow via AbortSignal", async () => {
    mockUseAppParameters.mockReturnValue({
      data: [
        { variable: "topic", label: "主题", type: "text" },
      ] as ParameterSchema[],
      isLoading: false,
      isError: false,
      error: null,
    });

    let capturedSignal: AbortSignal | undefined;
    mockRunWorkflow.mockImplementation(
      async (_appId, _values, _onChunk, signal) => {
        capturedSignal = signal;
        // Never resolve — pretend the SSE stream is in flight.
        return new Promise<void>(() => {});
      },
    );

    const { unmount } = renderAt();
    fireEvent.click(screen.getByRole("button", { name: /提\s?交/ }));

    await waitFor(() => {
      expect(mockRunWorkflow).toHaveBeenCalledTimes(1);
    });
    expect(capturedSignal).toBeDefined();
    expect(capturedSignal!.aborted).toBe(false);

    unmount();

    // useEffect cleanup runs on unmount → abortRef.current.abort().
    expect(capturedSignal!.aborted).toBe(true);
  });

  // TASK-E (B-NEW-52) — regression lock: status='failed' → '上游错误' must
  // survive the nested-ternary expansion in src/pages/CompletionPage.tsx:102.
  // Without this lock, a typo in the new 'timeout' branch could silently
  // collapse the failed/exception branches.
  it("test_e1_status_failed_unchanged_behavior — failed envelope still maps to '上游错误'", async () => {
    mockUseAppParameters.mockReturnValue({
      data: [
        { variable: "topic", label: "主题", type: "text" },
      ] as ParameterSchema[],
      isLoading: false,
      isError: false,
      error: null,
    });

    const envelope: NcmuSseEvent = {
      event_type: "workflow_finished",
      run_id: "e1e1e1e1-e1e1-4e1e-8e1e-e1e1e1e1e1e1",
      timestamp: "2026-05-18T01:00:00Z",
      data: {
        status: "failed",
        outputs: {},
        error: "upstream invalid_param",
      },
    };
    mockRunWorkflow.mockImplementation(async (_a, _v, onChunk) => {
      onChunk(envelope);
    });
    const errorSpy = vi
      .spyOn(notification, "error")
      .mockImplementation(() => undefined);

    renderAt();
    const submitBtn = screen.getByRole("button", { name: /提\s?交/ });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(errorSpy).toHaveBeenCalledTimes(1);
    });
    expect(errorSpy.mock.calls[0]![0]).toEqual(
      expect.objectContaining({
        message: "上游错误",
        description: "upstream invalid_param",
      }),
    );
    await waitFor(() => {
      expect(submitBtn.className).not.toContain("ant-btn-loading");
    });
    errorSpy.mockRestore();
  });

  // TASK-E (B-NEW-52) — new branch: status='timeout' must NOT silent
  // fallthrough into the success path (which would attempt to read
  // wf.outputs.answer and render empty). INDEP TASK-D 主审盲区 #11 实证。
  it("test_e2_status_timeout_shows_timeout_message — timeout envelope shows '运行超时'", async () => {
    mockUseAppParameters.mockReturnValue({
      data: [
        { variable: "topic", label: "主题", type: "text" },
      ] as ParameterSchema[],
      isLoading: false,
      isError: false,
      error: null,
    });

    const envelope: NcmuSseEvent = {
      event_type: "workflow_finished",
      run_id: "e2e2e2e2-e2e2-4e2e-8e2e-e2e2e2e2e2e2",
      timestamp: "2026-05-18T01:00:01Z",
      data: {
        status: "timeout",
        // TASK-G (B-NEW-54): partial answer mock — Dify v1.13.3 timeout may
        // carry `outputs.answer` non-empty. CompletionPage.tsx:126-128 reads
        // outputs.answer + setOutput only in else (success) branch; the new
        // assertion below locks that the fix-removed counterfactual (where
        // OR-clause didn't match timeout) would have setOutput("部分回答…")
        // and replaced the placeholder. Real silent-fallthrough reverse.
        outputs: { answer: "部分回答（被中断）" },
        error: "upstream timeout: TimeoutException('read timeout')",
      },
    };
    mockRunWorkflow.mockImplementation(async (_a, _v, onChunk) => {
      onChunk(envelope);
    });
    const errorSpy = vi
      .spyOn(notification, "error")
      .mockImplementation(() => undefined);

    renderAt();
    const submitBtn = screen.getByRole("button", { name: /提\s?交/ });
    fireEvent.click(submitBtn);

    // (a) showError called exactly once (not silent fallthrough)
    await waitFor(() => {
      expect(errorSpy).toHaveBeenCalledTimes(1);
    });
    // (b) message literal "运行超时" — NOT "上游错误" or "运行异常"
    expect(errorSpy.mock.calls[0]![0]).toEqual(
      expect.objectContaining({
        message: "运行超时",
        description: "upstream timeout: TimeoutException('read timeout')",
      }),
    );
    // (c) submitting settles — the onChunk branch sets it to false before
    // the page's finally also fires.
    await waitFor(() => {
      expect(submitBtn.className).not.toContain("ant-btn-loading");
    });
    // (d) output region NOT populated with success content (silent
    // fallthrough check — placeholder remains since the success branch
    // does not fire on timeout).
    expect(screen.queryByText("（待执行）")).toBeInTheDocument();
    // (e) TASK-G (B-NEW-54) strengthen — partial answer literal NOT in DOM.
    // Mock now carries `outputs.answer = "部分回答（被中断）"` (real Dify
    // v1.13.3 timeout path may carry partial generation); the fix-removed
    // counterfactual would have hit CompletionPage.tsx:126-128 else branch
    // → setOutput("部分回答（被中断）") → placeholder replaced. Locks both
    // halves: placeholder still present + partial answer not rendered.
    expect(screen.queryByText("部分回答（被中断）")).not.toBeInTheDocument();
    errorSpy.mockRestore();
  });

  // TASK-F (B-NEW-53) — regression lock: status='exception' → '运行异常' must
  // survive the 4-way nested-ternary expansion in src/pages/CompletionPage.tsx:112.
  // Without this lock, a typo in the new 'stopped' branch could silently
  // collapse the exception branch (now in the middle of a 4-way ternary)
  // into "运行已停止". test_e1 already locks 'failed' / test_e2 locks
  // 'timeout' — 'exception' is the only previously-uncovered middle branch
  // and the most-at-risk position during the 4-way expansion.
  it("test_f1_status_exception_unchanged_behavior — exception envelope still maps to '运行异常'", async () => {
    mockUseAppParameters.mockReturnValue({
      data: [
        { variable: "topic", label: "主题", type: "text" },
      ] as ParameterSchema[],
      isLoading: false,
      isError: false,
      error: null,
    });

    const envelope: NcmuSseEvent = {
      event_type: "workflow_finished",
      run_id: "f1f1f1f1-f1f1-4f1f-8f1f-f1f1f1f1f1f1",
      timestamp: "2026-05-19T01:00:00Z",
      data: {
        status: "exception",
        outputs: {},
        error: "internal orchestrator panic",
      },
    };
    mockRunWorkflow.mockImplementation(async (_a, _v, onChunk) => {
      onChunk(envelope);
    });
    const errorSpy = vi
      .spyOn(notification, "error")
      .mockImplementation(() => undefined);

    renderAt();
    const submitBtn = screen.getByRole("button", { name: /提\s?交/ });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(errorSpy).toHaveBeenCalledTimes(1);
    });
    expect(errorSpy.mock.calls[0]![0]).toEqual(
      expect.objectContaining({
        message: "运行异常",
        description: "internal orchestrator panic",
      }),
    );
    await waitFor(() => {
      expect(submitBtn.className).not.toContain("ant-btn-loading");
    });
    errorSpy.mockRestore();
  });

  // TASK-F (B-NEW-53) — new branch: status='stopped' must NOT silent
  // fallthrough into the success path (which would attempt to read
  // wf.outputs.answer = empty → render an empty Card, clobbering prior
  // output). INDEP TASK-D M2 candidate B-NEW-53 升级实施。本 test 锁定
  // fix 后 stopped → "运行已停止" toast + 输出区不被 silent setOutput。
  it("test_f2_status_stopped_shows_stopped_message — stopped envelope shows '运行已停止'", async () => {
    mockUseAppParameters.mockReturnValue({
      data: [
        { variable: "topic", label: "主题", type: "text" },
      ] as ParameterSchema[],
      isLoading: false,
      isError: false,
      error: null,
    });

    const envelope: NcmuSseEvent = {
      event_type: "workflow_finished",
      run_id: "f2f2f2f2-f2f2-4f2f-8f2f-f2f2f2f2f2f2",
      timestamp: "2026-05-19T01:00:01Z",
      data: {
        status: "stopped",
        // TASK-G (B-NEW-54): partial answer mock — Dify v1.13.3 stopped may
        // carry `outputs.answer` non-empty (user-cancelled partial run). Real
        // silent-fallthrough reverse on form-mode (see test_e2 for rationale).
        outputs: { answer: "部分回答（被中断）" },
        error: "Run stopped by user",
      },
    };
    mockRunWorkflow.mockImplementation(async (_a, _v, onChunk) => {
      onChunk(envelope);
    });
    const errorSpy = vi
      .spyOn(notification, "error")
      .mockImplementation(() => undefined);

    renderAt();
    const submitBtn = screen.getByRole("button", { name: /提\s?交/ });
    fireEvent.click(submitBtn);

    // (a) showError called exactly once (not silent fallthrough)
    await waitFor(() => {
      expect(errorSpy).toHaveBeenCalledTimes(1);
    });
    // (b) message literal "运行已停止" — NOT "上游错误" / "运行异常" / "运行超时"
    expect(errorSpy.mock.calls[0]![0]).toEqual(
      expect.objectContaining({
        message: "运行已停止",
        description: "Run stopped by user",
      }),
    );
    // (c) submitting settles — the onChunk branch sets it to false before
    // the page's finally also fires.
    await waitFor(() => {
      expect(submitBtn.className).not.toContain("ant-btn-loading");
    });
    // (d) output region NOT populated with success content (silent
    // fallthrough check — placeholder remains since the success branch
    // does not fire on stopped).
    expect(screen.queryByText("（待执行）")).toBeInTheDocument();
    // (e) TASK-G (B-NEW-54) strengthen — partial answer literal NOT in DOM
    // (see test_e2 for rationale; same counterfactual reverse for stopped).
    expect(screen.queryByText("部分回答（被中断）")).not.toBeInTheDocument();
    errorSpy.mockRestore();
  });
});
