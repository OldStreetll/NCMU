// TASK-PE-05 — admin tags list hook.
//
// Mirrors the vanilla useState+useEffect+try/catch+api<T>() pattern of
// ``useAdminUsers.ts`` (Boss 2026-05-09 decision: no TanStack Query in
// this repo). Surface returns a ``QueryResult<T>`` subset
// (``data | isLoading | isError | error``) and a manual ``refetch()`` so
// callers can refresh after mutate without coupling to the mutation API.
//
// PE-05 plan §3 line 910 字面: GET /admin/tags returns ``list[TagOut]``
// without pagination — the v1 admin UX is a single table view of all
// tags. PE-08/-09 may add search / page params once the tag volume grows.

import { useCallback, useEffect, useState } from "react";
import { listAdminTags, type AdminTagOut } from "@/lib/api";

export interface AdminTagsQueryResult {
  data: AdminTagOut[] | undefined;
  isLoading: boolean;
  isError: boolean;
  error: Error | null;
  refetch: () => void;
}

export function useAdminTags(): AdminTagsQueryResult {
  const [data, setData] = useState<AdminTagOut[] | undefined>(undefined);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<Error | null>(null);
  const [reloadCounter, setReloadCounter] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setError(null);
    listAdminTags()
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
  }, [reloadCounter]);

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
