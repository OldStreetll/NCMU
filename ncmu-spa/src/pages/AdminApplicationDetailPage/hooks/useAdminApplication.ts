// TASK-PC3-B — useAdminApplication: vanilla SWR-shaped hook (PC3-A pattern).
//
// Matches `useAppParameters` in lib/api.ts: useState + useEffect + cancelled
// flag, no third-party data lib. `invalidate()` bumps a tick that re-runs the
// effect so the right-side panel can refresh after a claim/dispatch/reject
// mutation. No automatic polling on the detail view — list page polls every
// 30s; detail refreshes on user mutation or page nav.

import { useCallback, useEffect, useState } from "react";
import { ApiError, getApplicationDetail } from "@/lib/api";
import type { ApplicationDetailOut } from "@/lib/api";

interface UseAdminApplicationResult {
  data: ApplicationDetailOut | undefined;
  isLoading: boolean;
  error: ApiError | null;
  invalidate: () => void;
}

export function useAdminApplication(
  applicationId: string | undefined,
): UseAdminApplicationResult {
  const [data, setData] = useState<ApplicationDetailOut | undefined>(undefined);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<ApiError | null>(null);
  const [tick, setTick] = useState<number>(0);

  useEffect(() => {
    if (!applicationId) {
      setIsLoading(false);
      return;
    }
    let cancelled = false;
    setIsLoading(true);
    setError(null);
    getApplicationDetail(applicationId)
      .then((res) => {
        if (cancelled) return;
        setData(res);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(
          err instanceof ApiError
            ? err
            : new ApiError(0, undefined, err instanceof Error ? err.message : String(err)),
        );
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [applicationId, tick]);

  const invalidate = useCallback(() => {
    setTick((t) => t + 1);
  }, []);

  return { data, isLoading, error, invalidate };
}
