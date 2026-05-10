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

  it("AC#FIX-I1 — timeout / cancelled / db_error tags render with their distinct preset color classes (INDEP-70b NIT-70b-4 → I-1)", () => {
    // Backend `crud.py:29-32` defines 7 terminal RunStatus literals; the
    // pre-fix STATUS_COLOR map only covered succeeded / failed / stopped /
    // exception → timeout / cancelled / db_error fell through to the
    // "default" branch and rendered as the same grey tag. Verify the three
    // mappings emitted by TASK-FIX-I1 reach the DOM as distinct antd preset
    // color classes.
    const failureRuns: WorkflowRunSummary[] = [
      {
        run_id: "44444444-4444-4444-8444-444444444444",
        app_id: "app-1",
        mode: "completion",
        status: "timeout",
        started_at: "2026-05-09T13:00:00Z",
        finished_at: "2026-05-09T13:01:30Z",
      },
      {
        run_id: "55555555-5555-4555-8555-555555555555",
        app_id: "app-1",
        mode: "completion",
        status: "cancelled",
        started_at: "2026-05-09T14:00:00Z",
        finished_at: "2026-05-09T14:00:05Z",
      },
      {
        run_id: "66666666-6666-4666-8666-666666666666",
        app_id: "app-1",
        mode: "completion",
        status: "db_error",
        started_at: "2026-05-09T15:00:00Z",
        finished_at: "2026-05-09T15:00:01Z",
      },
    ];
    mockUseWorkflowRunList.mockReturnValue({
      data: failureRuns,
      isLoading: false,
      isError: false,
      error: null,
    });
    render(<RunHistoryList appId="app-1" />);

    const timeoutTag = screen
      .getByTestId(`run-row-${failureRuns[0]!.run_id}`)
      .querySelector(".ant-tag");
    expect(timeoutTag).not.toBeNull();
    expect(timeoutTag!.className).toContain("ant-tag-gold");
    expect(timeoutTag!.textContent).toBe("timeout");

    const cancelledTag = screen
      .getByTestId(`run-row-${failureRuns[1]!.run_id}`)
      .querySelector(".ant-tag");
    expect(cancelledTag).not.toBeNull();
    // cancelled = "default" → antd 5 emits no preset color suffix on the
    // tag root; assert the absence of any preset color class so a future
    // mapping accidentally collapsing back to "default" still passes
    // while a wrong preset value would fail.
    expect(cancelledTag!.className).not.toMatch(
      /ant-tag-(green|red|orange|magenta|volcano|gold|blue|cyan|geekblue|purple|pink|lime)\b/,
    );
    expect(cancelledTag!.textContent).toBe("cancelled");

    const dbErrorTag = screen
      .getByTestId(`run-row-${failureRuns[2]!.run_id}`)
      .querySelector(".ant-tag");
    expect(dbErrorTag).not.toBeNull();
    expect(dbErrorTag!.className).toContain("ant-tag-volcano");
    expect(dbErrorTag!.textContent).toBe("db_error");

    // All three tag classNames are pairwise distinct — the regression
    // this case guards against is precisely "all three look the same".
    const classes = [
      timeoutTag!.className,
      cancelledTag!.className,
      dbErrorTag!.className,
    ];
    expect(new Set(classes).size).toBe(3);
  });
});
