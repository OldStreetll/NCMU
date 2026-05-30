// TASK-PE-07 — admin apps list hook.
//
// Mirrors the vanilla useState+useEffect+try/catch+api<T>() pattern of
// ``useAdminTags.ts`` / ``useAdminUsers.ts`` (Boss 2026-05-09 decision:
// no TanStack Query in this repo). Adds ``include_inactive`` + ``search``
// params (passed through to GET /admin/apps) and re-fetches whenever they
// change or ``refetch()`` is called — so the toolbar's "显示已停用" switch
// and the search box drive the list without a mutation-API coupling.

import { useCallback, useEffect, useState } from "react";
import { listAdminApps, type AdminAppOut } from "@/lib/api";

export interface UseAdminAppsParams {
  includeInactive?: boolean;
  search?: string;
}

export interface AdminAppsQueryResult {
  data: AdminAppOut[] | undefined;
  isLoading: boolean;
  isError: boolean;
  error: Error | null;
  refetch: () => void;
}

export function useAdminApps(
  params: UseAdminAppsParams = {},
): AdminAppsQueryResult {
  const { includeInactive = false, search } = params;
  const [data, setData] = useState<AdminAppOut[] | undefined>(undefined);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<Error | null>(null);
  const [reloadCounter, setReloadCounter] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setError(null);
    listAdminApps({ include_inactive: includeInactive, search })
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
  }, [includeInactive, search, reloadCounter]);

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
