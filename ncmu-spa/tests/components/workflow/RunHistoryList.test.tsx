import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

// vi.mock is hoisted above all imports — must use a factory that returns
// only mockable refs (no out-of-scope vars). The hook is then resolved as
// a vi.fn() in subsequent imports.
vi.mock("@/hooks/useWorkflowRuns", () => ({
  useWorkflowRunList: vi.fn(),
}));

import { RunHistoryList } from "@/components/workflow/RunHistoryList";
import {
  useWorkflowRunList,
  type WorkflowRunSummary,
} from "@/hooks/useWorkflowRuns";

const mockUseWorkflowRunList = vi.mocked(useWorkflowRunList);

// Antd 5 List + Empty pull in the Grid useBreakpoint hook which calls
// window.matchMedia. jsdom doesn't ship it; polyfill once like the
// SessionList / RetrieverChip / DynamicInputForm tests do.
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
  mockUseWorkflowRunList.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

const sampleRuns: WorkflowRunSummary[] = [
  {
    run_id: "11111111-1111-4111-8111-111111111111",
    app_id: "app-1",
    mode: "completion",
    status: "succeeded",
    started_at: "2026-05-09T10:00:00Z",
    finished_at: "2026-05-09T10:00:30Z",
  },
  {
    run_id: "22222222-2222-4222-8222-222222222222",
    app_id: "app-1",
    mode: "completion",
    status: "failed",
    started_at: "2026-05-09T11:00:00Z",
    finished_at: null,
  },
  {
    run_id: "33333333-3333-4333-8333-333333333333",
    app_id: "app-1",
    mode: "completion",
    status: "running",
    started_at: "2026-05-09T12:00:00Z",
    finished_at: null,
  },
];

describe("<RunHistoryList>", () => {
  it("AC#6(a) — mock useWorkflowRunList returns 3 items → renders 3 rows", () => {
    mockUseWorkflowRunList.mockReturnValue({
      data: sampleRuns,
      isLoading: false,
      isError: false,
      error: null,
    });
    render(<RunHistoryList appId="app-1" />);
    // Each row carries data-testid={`run-row-${run_id}`} on the antd List.Item li.
    for (const run of sampleRuns) {
      expect(screen.getByTestId(`run-row-${run.run_id}`)).toBeInTheDocument();
    }
    // Hook called with the appId prop.
    expect(mockUseWorkflowRunList).toHaveBeenCalledWith("app-1");
  });

  it("AC#6(b) — clicking a row fires onRowClick(run_id)", () => {
    mockUseWorkflowRunList.mockReturnValue({
      data: sampleRuns,
      isLoading: false,
      isError: false,
      error: null,
    });
    const onRowClick = vi.fn();
    render(<RunHistoryList appId="app-1" onRowClick={onRowClick} />);
    const targetRow = screen.getByTestId(`run-row-${sampleRuns[1]!.run_id}`);
    fireEvent.click(targetRow);
    expect(onRowClick).toHaveBeenCalledTimes(1);
    expect(onRowClick).toHaveBeenCalledWith(sampleRuns[1]!.run_id);
  });

  it("AC#6(c) — loading state renders skeleton", () => {
    mockUseWorkflowRunList.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
    });
    const { container } = render(<RunHistoryList appId="app-1" />);
    expect(screen.getByTestId("runs-skeleton")).toBeInTheDocument();
    // While loading we must not render the row List — assert the antd List
    // root class (.ant-list) is absent. Antd Skeleton itself renders a
    // <ul class="ant-skeleton-paragraph">, so role="list" is not a clean
    // discriminator here.
    expect(container.querySelector(".ant-list")).toBeNull();
  });
});
