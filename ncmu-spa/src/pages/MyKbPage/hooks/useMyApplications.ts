// TASK-PC3-A — list-my-applications hook with 60 s polling.
//
// Mirrors `useWorkflowRunList` (src/hooks/useWorkflowRuns.ts) in shape:
// useState + useEffect + the `api()` helper, returning the same
// `QueryResult<T>` field set (data / isLoading / isError / error). Plan
// §4.6 字面 "SWR refreshInterval=60000" is interpreted as the project
// hook-pattern convention — `swr` is not in package.json (Boss decision
// 2026-05-09, mirrored in `useWorkflowRunList` head comment; 守
// `feedback_plan_tech_stack_grounding`). PC2-FIX 2026-05-23 confirmed
// Path B vanilla — useState + setInterval + visibility gate.
//
// Polling semantics (plan §4.6 AC#5):
//   - refresh on a 60 s setInterval
//   - pause when `document.hidden` (tab not visible)
//   - resume + immediate catch-up fetch on visibilitychange → visible
//   - clean shutdown: interval cleared + listener removed on unmount
//   - `refetch()` bumps an internal refreshKey so callers (Modal submit /
//     cancel flow) can force-refresh without waiting for the next tick

import { useCallback, useEffect, useState } from "react";
import { listMyApplications, type PersonalKbApplication } from "@/lib/api";

export const MY_APPLICATIONS_POLL_INTERVAL_MS = 60_000;

export interface UseMyApplicationsResult {
  data: PersonalKbApplication[] | undefined;
  isLoading: boolean;
  isError: boolean;
  error: Error | null;
  refetch: () => void;
}

export function useMyApplications(): UseMyApplicationsResult {
  const [data, setData] = useState<PersonalKbApplication[] | undefined>(
    undefined,
  );
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<Error | null>(null);
  const [refreshKey, setRefreshKey] = useState<number>(0);

  const refetch = useCallback(() => {
    setRefreshKey((k) => k + 1);
  }, []);

  useEffect(() => {
    let cancelled = false;
    let intervalId: ReturnType<typeof setInterval> | null = null;

    const fetchOnce = async () => {
      try {
        const rows = await listMyApplications();
        if (cancelled) return;
        setData(rows);
        setError(null);
      } catch (err: unknown) {
        if (cancelled) return;
        setError(err instanceof Error ? err : new Error(String(err)));
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    };

    const pollTick = () => {
      // Skip the tick when the tab is hidden — the visibilitychange
      // handler below will fire a catch-up fetch on resume.
      if (!document.hidden) fetchOnce();
    };

    const startInterval = () => {
      if (intervalId === null) {
        intervalId = setInterval(pollTick, MY_APPLICATIONS_POLL_INTERVAL_MS);
      }
    };
    const stopInterval = () => {
      if (intervalId !== null) {
        clearInterval(intervalId);
        intervalId = null;
      }
    };

    const onVisibilityChange = () => {
      if (document.hidden) {
        stopInterval();
      } else {
        fetchOnce();
        startInterval();
      }
    };

    // Initial fetch always runs (so isLoading drops to false even when
    // the tab is hidden at mount-time).
    fetchOnce();
    document.addEventListener("visibilitychange", onVisibilityChange);
    if (!document.hidden) startInterval();

    return () => {
      cancelled = true;
      stopInterval();
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [refreshKey]);

  return { data, isLoading, isError: error !== null, error, refetch };
}
