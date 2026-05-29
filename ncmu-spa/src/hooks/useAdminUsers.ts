// TASK-PE-06 — admin users list/mutate hooks.
//
// Mirrors the vanilla useState+useEffect+try/catch+api<T>() pattern of
// ``useWorkflowRuns.ts`` (Boss 2026-05-09 decision: no TanStack Query in
// this repo). Surface returns a ``QueryResult<T>`` subset
// (``data | isLoading | isError | error``) and a manual ``refetch()`` so
// callers can refresh after mutate without coupling to the mutation API.

import { useCallback, useEffect, useState } from "react";
import {
  listAdminUsers,
  type AdminUserList,
  type AdminUserListParams,
} from "@/lib/api";

export interface AdminUsersQueryResult {
  data: AdminUserList | undefined;
  isLoading: boolean;
  isError: boolean;
  error: Error | null;
  refetch: () => void;
}

export function useAdminUsers(params: AdminUserListParams): AdminUsersQueryResult {
  const [data, setData] = useState<AdminUserList | undefined>(undefined);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<Error | null>(null);
  const [reloadCounter, setReloadCounter] = useState(0);

  // Serialize params so the effect dep array stays primitive and we don't
  // re-trigger on every parent re-render. The shape is small (4 fields), so
  // string equality is the cheapest stable key.
  const paramsKey = JSON.stringify(params);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setError(null);
    listAdminUsers(params)
      .then((res) => {
        if (!cancelled) {
          setData(res);
          setIsLoading(false);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err : new Error(String(err)));
          setIsLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paramsKey, reloadCounter]);

  const refetch = useCallback(() => {
    setReloadCounter((n) => n + 1);
  }, []);

  return {
    data,
    isLoading,
    isError: error !== null,
    error,
    refetch,
  };
}
