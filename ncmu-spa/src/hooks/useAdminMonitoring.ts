// useAdminMonitoring — TASK-MON-2 admin monitoring snapshot hook.
//
// Vanilla useState + useEffect + api() pattern (mirrors useAdminStats in
// @/hooks/useAdminStats). The project does not pull @tanstack/react-query so
// the QueryResult<T> shape is hand-rolled here for caller-side parity.
//
// Endpoint: GET /api/v1/ncmu/admin/monitoring (require_admin gate). api()
// auto-prefixes BASE = /api/v1/ncmu so the path argument is BASE-relative:
// "/admin/monitoring". The response contract (MonitoringOut) is frozen by
// MON-1 — field names/shapes here mirror the backend schema verbatim; do not
// rename without updating the MON-1 schema.

import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";

// business.kb_application_status — 5 fixed keys, 0 included (never sparse).
export interface KbApplicationStatus {
  pending: number;
  in_progress: number;
  done: number;
  rejected: number;
  cancelled: number;
}

export interface TagBinding {
  tag_id: string;
  name: string;
  app_count: number;
  user_count: number;
}

export interface BusinessMetrics {
  user_total: number;
  user_active: number;
  user_with_dingtalk: number;
  new_users_7d: number;
  kb_application_status: KbApplicationStatus;
  kb_pending_backlog: number; // = kb_application_status.pending
  new_kb_applications_7d: number;
  avg_kb_processing_seconds: number | null; // null when no done rows
  app_total: number;
  app_active: number;
  last_dify_app_sync_at: string | null; // datetime | null
  tag_bindings: TagBinding[];
}

// dingtalk.config_readiness — health lights only, no secret values.
export interface ConfigReadiness {
  app_key: boolean;
  app_secret: boolean;
  corp_id: boolean;
  login_redirect_uri: boolean;
}

export interface DingtalkMetrics {
  config_readiness: ConfigReadiness;
  department_tag_mapping_count: number;
  synced_account_count: number; // = business.user_with_dingtalk (proxy)
  tags_routing_enabled: boolean;
  routing_affected_user_count: number;
  routing_affected_app_count: number;
}

export interface MonitoringOut {
  business: BusinessMetrics;
  dingtalk: DingtalkMetrics;
  generated_at: string; // datetime UTC
}

export interface QueryResult<T> {
  data: T | undefined;
  isLoading: boolean;
  isError: boolean;
  error: Error | null;
}

export function useAdminMonitoring(): QueryResult<MonitoringOut> {
  const [data, setData] = useState<MonitoringOut | undefined>(undefined);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setError(null);
    api<MonitoringOut>("/admin/monitoring")
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
