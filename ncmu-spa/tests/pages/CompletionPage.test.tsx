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
import { CompletionPage } from "@/pages/CompletionPage";
import { useAppParameters, runWorkflow } from "@/lib/api";
import type { ParameterSchema } from "@/components/workflow/DynamicInputForm";
import { useWorkflowRunList } from "@/hooks/useWorkflowRuns";

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
});
