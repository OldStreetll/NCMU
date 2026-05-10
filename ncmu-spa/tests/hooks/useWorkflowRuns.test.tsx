import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// Mock the api module before importing the hook so the hook's bound reference
// resolves to the spy. We re-export ApiError so the hook can throw real-shape
// errors when needed.
vi.mock("@/lib/api", () => {
  const ApiError = class extends Error {
    status: number;
    code: number | undefined;
    constructor(status: number, code: number | undefined, message: string) {
      super(message);
      this.status = status;
      this.code = code;
      this.name = "ApiError";
    }
  };
  return {
    api: vi.fn(),
    ApiError,
    API_BASE: "/api/v1/ncmu",
  };
});

import { api, ApiError } from "@/lib/api";
import {
  useWorkflowRunDetail,
  useWorkflowRunList,
  type WorkflowRunDetail,
  type WorkflowRunSummary,
} from "@/hooks/useWorkflowRuns";

const apiMock = api as unknown as ReturnType<typeof vi.fn>;

const summaryFixture: WorkflowRunSummary[] = [
  {
    run_id: "run-001",
    app_id: "app-X",
    mode: "workflow",
    status: "succeeded",
    started_at: "2026-05-09T10:00:00Z",
    finished_at: "2026-05-09T10:00:05Z",
  },
];

const detailFixture: WorkflowRunDetail = {
  ...summaryFixture[0],
  inputs: { query: "ping" },
  outputs: { answer: "pong" },
  node_trace: [],
  error_msg: null,
};

describe("useWorkflowRunList", () => {
  beforeEach(() => {
    apiMock.mockReset();
  });

  it("(a) mock api 返回 200 + JSON → useWorkflowRunList 返回 data", async () => {
    apiMock.mockResolvedValueOnce(summaryFixture);
    const { result } = renderHook(() => useWorkflowRunList("app-X"));

    // initial: isLoading true
    expect(result.current.isLoading).toBe(true);
    expect(result.current.data).toBeUndefined();

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.data).toEqual(summaryFixture);
    expect(result.current.isError).toBe(false);
    expect(result.current.error).toBeNull();
    // path is RELATIVE to API_BASE (★F-FRESH-2)
    expect(apiMock).toHaveBeenCalledWith("/workflow/apps/app-X/runs");
  });

  it("(b) api 抛 5xx ApiError → returns isError=true / error 字段", async () => {
    apiMock.mockRejectedValueOnce(new ApiError(503, undefined, "service unavailable"));
    const { result } = renderHook(() => useWorkflowRunList("app-X"));

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.isError).toBe(true);
    expect(result.current.error).toBeInstanceOf(Error);
    expect(result.current.error?.message).toContain("service unavailable");
    expect(result.current.data).toBeUndefined();
  });
});

describe("useWorkflowRunDetail", () => {
  beforeEach(() => {
    apiMock.mockReset();
  });

  it("(c) runId=null 时 enabled=false 不发请求", async () => {
    const { result } = renderHook(() => useWorkflowRunDetail(null));

    // not loading, no fetch issued, data undefined
    expect(result.current.isLoading).toBe(false);
    expect(result.current.data).toBeUndefined();
    expect(result.current.error).toBeNull();
    // give any pending microtasks a tick to confirm api() was not invoked
    await Promise.resolve();
    expect(apiMock).not.toHaveBeenCalled();
  });

  it("(d) runId=string → 发请求 + 解析 detail", async () => {
    apiMock.mockResolvedValueOnce(detailFixture);
    const { result } = renderHook(() => useWorkflowRunDetail("run-001"));

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.data).toEqual(detailFixture);
    expect(apiMock).toHaveBeenCalledWith("/workflow/runs/run-001");
  });

  it("(e) runId 从 string → null 切换：清空 data + 不再发请求", async () => {
    apiMock.mockResolvedValueOnce(detailFixture);
    const { result, rerender } = renderHook(
      ({ runId }: { runId: string | null }) => useWorkflowRunDetail(runId),
      { initialProps: { runId: "run-001" as string | null } },
    );

    await waitFor(() => expect(result.current.data).toEqual(detailFixture));
    expect(apiMock).toHaveBeenCalledTimes(1);

    rerender({ runId: null });
    expect(result.current.data).toBeUndefined();
    expect(result.current.isLoading).toBe(false);
    // no additional call when runId becomes null
    expect(apiMock).toHaveBeenCalledTimes(1);
  });
});
