// useWorkflowRuns — workflow run list / run detail data hooks.
//
// inline 实现风格（Boss 拍板 B / 2026-05-09）：plan §修法 line 1419-1469 原本
// 假设 `@tanstack/react-query`，但项目实际未装该 lib（package.json 无 `@tanstack/*`）。
// 与 SessionList (line 46-68) 等现有组件保持一致：useState + useEffect + try/catch
// + lib/api.ts 的 `api<T>()` helper（已含 JWT + ApiError）。
//
// 返回值 shape (`QueryResult<T>`) 对齐 TanStack Query useQuery 字段子集
// (`data` / `isLoading` / `isError` / `error`)，让 RunHistoryList (TASK-70b-2) 的
// 消费方代码字面与 plan 一致 — 未来若回头装 TanStack Query 替换实现时无需改 caller。

import { useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";
import type { NcmuSseEvent } from "@/lib/sse-types";

export interface WorkflowRunSummary {
  run_id: string;
  app_id: string;
  mode: string;
  status: string;
  started_at: string;
  finished_at: string | null;
}

export interface WorkflowRunDetail extends WorkflowRunSummary {
  inputs: Record<string, unknown>;
  outputs: Record<string, unknown>;
  node_trace: NcmuSseEvent[];
  error_msg: string | null;
}

export interface QueryResult<T> {
  data: T | undefined;
  isLoading: boolean;
  isError: boolean;
  error: Error | null;
}

// GET /api/v1/ncmu/workflow/apps/{appId}/runs (★F-FRESH-2: relative to API_BASE)
export function useWorkflowRunList(appId: string): QueryResult<WorkflowRunSummary[]> {
  const [data, setData] = useState<WorkflowRunSummary[] | undefined>(undefined);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setError(null);
    api<WorkflowRunSummary[]>(`/workflow/apps/${appId}/runs`)
      .then((res) => {
        if (cancelled) return;
        setData(res);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        // api() throws ApiError on non-2xx; surface as `error` for caller.
        setError(err instanceof Error ? err : new Error(String(err)));
      })
      .finally(() => {
        if (cancelled) return;
        setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [appId]);

  return { data, isLoading, isError: error !== null, error };
}

// GET /api/v1/ncmu/workflow/runs/{runId}
// runId === null → enabled=false 等价（不发请求，isLoading=false，data undefined）
export function useWorkflowRunDetail(runId: string | null): QueryResult<WorkflowRunDetail> {
  const [data, setData] = useState<WorkflowRunDetail | undefined>(undefined);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    if (runId === null) {
      // 显式 enabled=false 路径：清状态、不发请求。
      setData(undefined);
      setError(null);
      setIsLoading(false);
      return;
    }
    let cancelled = false;
    setIsLoading(true);
    setError(null);
    api<WorkflowRunDetail>(`/workflow/runs/${runId}`)
      .then((res) => {
        if (cancelled) return;
        setData(res);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err : new Error(String(err)));
      })
      .finally(() => {
        if (cancelled) return;
        setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [runId]);

  return { data, isLoading, isError: error !== null, error };
}

export { ApiError };
