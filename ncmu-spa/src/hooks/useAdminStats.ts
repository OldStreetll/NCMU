// useAdminStats — TASK-PE-04 admin landing-page overview counts hook.
//
// Vanilla useState + useEffect + api() pattern (mirrors useWorkflowRunList
// in @/hooks/useWorkflowRuns). The project does not pull @tanstack/react-query
// so the QueryResult<T> shape is hand-rolled here for caller-side parity.
//
// Endpoint: GET /api/v1/ncmu/admin/stats (require_admin gate, returns 5 int
// counts). api() auto-prefixes BASE = /api/v1/ncmu so the path argument
// is BASE-relative: "/admin/stats".

import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";

export interface AdminStats {
  tag_count: number;
  user_count: number;
  app_count: number;
  pending_personal_kb_count: number;
  external_kb_config_count: number;
}

export interface QueryResult<T> {
  data: T | undefined;
  isLoading: boolean;
  isError: boolean;
  error: Error | null;
}

export function useAdminStats(): QueryResult<AdminStats> {
  const [data, setData] = useState<AdminStats | undefined>(undefined);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setError(null);
    api<AdminStats>("/admin/stats")
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
  }, []);

  return { data, isLoading, isError: error !== null, error };
}

export { ApiError };
