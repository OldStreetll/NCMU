// TASK-PC3-C (Phase 2C) — useKbFiles: vanilla SWR-like KB file listing.
//
// plan §4.8 AC#5 字面 "SWR" 是语义描述（cache + dedup + mutate 行为），
// 实施按项目 vanilla 模式（match useAppParameters 范式 / 0 第三方 hook lib /
// Boss 决策 vanilla / [INTENT-CHECK] 路径 A2）。
//
// Hook contract:
//   useKbFiles(appId, enabled) → { data, isLoading, isError, error, mutate }
//   - enabled=false: skip fetch entirely (used while the panel is collapsed
//     so the network round-trip is paid only on expand — plan §4.8 AC#1
//     "懒加载 / 不进页面就调端点")
//   - dedupingInterval = 30 000 ms (plan §4.8 AC#5 — 与 backend Q5-B TTL 协同)
//   - no polling (plan §4.8 AC#5 — 员工查 KB 是被动)
//
// 5 类错误处理（plan §4.8 AC#4 / errata-13 §2.4 + spec §5）:
//   503  → message.error "知识库服务暂时不可用，请稍后再试"
//   404  → message.warning "文档已被移除，列表已刷新" + auto mutate
//   401/403 → message.error "无权访问此知识库"（不重试）
//   500  → message.error "知识库服务异常"
//   网络/其它 → message.error "网络连接失败"
//
// AbortError (cancelled by unmount / mutate) 不弹 toast — 是预期路径。
import { useCallback, useEffect, useRef, useState } from "react";
import { message } from "antd";
import { ApiError, listKbFiles, type KbFile } from "@/lib/api";

const DEDUP_MS = 30_000;

interface CacheEntry {
  data: KbFile[];
  timestamp: number;
}

// Module-level cache shared by every useKbFiles caller in the SPA — 30s
// dedup happens at this layer, not inside the hook closure, so two
// simultaneous mounts (panel + future sibling) reuse the same payload.
const cache = new Map<string, CacheEntry>();
const inflight = new Map<string, Promise<KbFile[]>>();

// Test-only escape hatch — used in vitest beforeEach to clear shared state
// between cases. Not exported in production callers' surface.
export function _resetKbFilesCacheForTests(): void {
  cache.clear();
  inflight.clear();
}

// 5-class error → toast text mapper. Pure function so the test can assert
// the exact strings (守 feedback_pre_existing_error_strict_validation —
// no fuzzy matching). Returns ``null`` for the AbortError case so callers
// know to skip the toast.
export const KB_ERROR_TEXT = {
  unreachable: "知识库服务暂时不可用，请稍后再试",
  notFound: "文档已被移除，列表已刷新",
  forbidden: "无权访问此知识库",
  serverError: "知识库服务异常",
  network: "网络连接失败",
} as const;

export interface UseKbFilesResult {
  data: KbFile[] | undefined;
  isLoading: boolean;
  isError: boolean;
  error: Error | null;
  mutate: () => Promise<void>;
}

function isAbort(err: unknown): boolean {
  return (
    err instanceof DOMException && err.name === "AbortError"
  ) || (err instanceof Error && err.name === "AbortError");
}

// Look up the cache, returning fresh data only if still inside the dedup
// window. Stale entries are evicted — this keeps the Map from growing
// without bound across long sessions.
function readFresh(appId: string): KbFile[] | undefined {
  const entry = cache.get(appId);
  if (!entry) return undefined;
  if (Date.now() - entry.timestamp > DEDUP_MS) {
    cache.delete(appId);
    return undefined;
  }
  return entry.data;
}

export function useKbFiles(
  appId: string,
  enabled: boolean,
): UseKbFilesResult {
  const [data, setData] = useState<KbFile[] | undefined>(() =>
    enabled ? readFresh(appId) : undefined,
  );
  // Initial isLoading mirrors what the useEffect tail will do — when the
  // panel mounts expanded and the cache has no fresh entry, the first
  // paint should show Spin, not the empty-state flash that "false → true
  // after one frame" produces.
  const [isLoading, setIsLoading] = useState<boolean>(() =>
    enabled && readFresh(appId) === undefined,
  );
  const [error, setError] = useState<Error | null>(null);

  // ``cancelledRef`` flips on unmount and on each re-run; we read it in
  // the async tail so a slow fetch can't write into a torn-down tree.
  const cancelledRef = useRef(false);

  const doFetch = useCallback(
    async (force: boolean): Promise<void> => {
      if (!enabled) return;

      if (!force) {
        const cached = readFresh(appId);
        if (cached !== undefined) {
          setData(cached);
          setError(null);
          setIsLoading(false);
          return;
        }
      } else {
        cache.delete(appId);
      }

      // Coalesce concurrent callers on the same appId — second caller
      // attaches to the already-in-flight promise rather than firing a
      // duplicate request. We deliberately don't pass an AbortSignal:
      // letting the request finish + populating the cache benefits any
      // future expand inside the dedup window, even if the current
      // panel un-mounted before the response landed.
      let p = inflight.get(appId);
      if (!p) {
        p = (async () => {
          try {
            const rows = await listKbFiles(appId);
            cache.set(appId, { data: rows, timestamp: Date.now() });
            return rows;
          } finally {
            inflight.delete(appId);
          }
        })();
        inflight.set(appId, p);
      }

      setIsLoading(true);
      setError(null);
      try {
        const rows = await p;
        if (cancelledRef.current) return;
        setData(rows);
        setError(null);
      } catch (err) {
        if (isAbort(err) || cancelledRef.current) return;
        const e = err instanceof Error ? err : new Error(String(err));
        setError(e);
        const status = err instanceof ApiError ? err.status : undefined;
        if (status === 503) {
          message.error(KB_ERROR_TEXT.unreachable);
        } else if (status === 404) {
          message.warning(KB_ERROR_TEXT.notFound);
          // 404 path: invalidate the cache and silently kick a refresh so
          // the list reflects upstream deletion (plan §4.8 AC#4 字面
          // "列表自动刷新（mutate）").
          cache.delete(appId);
          // Defer the refetch one tick — avoid recursion through the same
          // setState frame.
          setTimeout(() => {
            if (!cancelledRef.current) {
              void doFetch(true);
            }
          }, 0);
        } else if (status === 401 || status === 403) {
          message.error(KB_ERROR_TEXT.forbidden);
        } else if (status === 500) {
          message.error(KB_ERROR_TEXT.serverError);
        } else {
          message.error(KB_ERROR_TEXT.network);
        }
      } finally {
        if (!cancelledRef.current) setIsLoading(false);
      }
    },
    [appId, enabled],
  );

  // On enabled flip (false → true) or appId change, fetch.
  useEffect(() => {
    cancelledRef.current = false;
    if (enabled) {
      void doFetch(false);
    } else {
      // Closed panel — drop in-memory data so a re-expand reads from
      // cache (or fetches if expired), instead of showing stale.
      setData(undefined);
      setError(null);
      setIsLoading(false);
    }
    return () => {
      cancelledRef.current = true;
    };
  }, [appId, enabled, doFetch]);

  const mutate = useCallback(async () => {
    await doFetch(true);
  }, [doFetch]);

  return {
    data,
    isLoading,
    isError: error !== null,
    error,
    mutate,
  };
}
