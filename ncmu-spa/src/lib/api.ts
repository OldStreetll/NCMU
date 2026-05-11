import { useEffect, useState } from "react";
import { getJwt } from "@/lib/auth";
import type { ParameterSchema } from "@/components/workflow/DynamicInputForm";
import { streamChat } from "@/lib/streamChat";
import type { NcmuSseEvent } from "@/lib/sse-types";

// E-1 修订：BASE = /api/v1/ncmu — Phase 0 已用 /api/* 反代 Dify，必须避开。
const BASE = "/api/v1/ncmu";

export class ApiError extends Error {
  status: number;
  code: number | undefined;
  constructor(status: number, code: number | undefined, message: string) {
    super(message);
    this.status = status;
    this.code = code;
    this.name = "ApiError";
  }
}

export async function api<T = unknown>(
  path: string,
  init: RequestInit = {},
  opts: { skipAuth?: boolean } = {},
): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((init.headers as Record<string, string>) ?? {}),
  };
  if (!opts.skipAuth) {
    const jwt = getJwt();
    if (!jwt) throw new ApiError(401, undefined, "no jwt");
    headers["Authorization"] = `Bearer ${jwt}`;
  }
  const resp = await fetch(`${BASE}${path}`, { ...init, headers });
  if (!resp.ok) {
    let body: { code?: number; message?: string } = {};
    try {
      body = await resp.json();
    } catch {
      // non-json response (eg. nginx 502 HTML); fall through with statusText
    }
    // dev-login + dev/users return 404 with detail={code,message}; FastAPI
    // wraps it as `detail: {...}` not flat — handle both shapes.
    const detail = (body as unknown as { detail?: { code?: number; message?: string } }).detail;
    const code = detail?.code ?? body.code;
    const message = detail?.message ?? body.message ?? resp.statusText;
    throw new ApiError(resp.status, code, message);
  }
  return resp.json() as Promise<T>;
}

export { BASE as API_BASE };

// --- TASK-76 (Phase 2B B3) — useAppParameters + runWorkflow ----------------
//
// useAppParameters: workflow / completion / advanced-chat / agent-chat pages
// pull their input form schema from this hook. Pattern mirrors
// useWorkflowRunList in `@/hooks/useWorkflowRuns` (useState + useEffect + the
// `api()` helper above) so the QueryResult shape stays identical across the
// SPA. INDEP-PLAN-3 M-FRESH-3: backend GET /apps/{id}/parameters is backlog
// (spec §8 #2) — production calls 404 today and the page falls back to the
// `parameters ?? []` empty form. TASK-77 reuses this hook unchanged.
//
// runWorkflow: thin async wrapper over `streamChat` that drives the workflow
// SSE endpoint `/workflow/apps/{appId}/run` (relative to API_BASE — F-FRESH-2
// double-prefix trap). The callback receives one of two shapes per frame:
//   - string         : reserved for a future per-token streaming mode. No
//                      current backend orchestrator emits this on the
//                      workflow endpoint — completion.py / advanced_chat.py /
//                      workflow.py / agent_chat.py all collapse upstream
//                      partial-text frames into a single `workflow_finished`
//                      envelope (see REWORK-76-INDEP C-INDEP-1). Kept in
//                      the union so a later orchestrator can light it up
//                      without changing this signature.
//   - NcmuSseEvent   : workflow-mode envelope frames (node_started /
//                      node_finished / workflow_finished / agent_thought /
//                      tool_call). The streamChat parser yields these with
//                      the FULL envelope on `evt.data` (backend routes.py
//                      serializes the whole NcmuSseEvent to the SSE data
//                      line — see streamChat.ts line 60-67 comment).
// Caller examples: TASK-76 CompletionPage reads
// `chunk.event_type === "workflow_finished"` → outputs.answer; TASK-77
// WorkflowRunPage iterates envelope frames into a node trace.

export interface QueryResult<T> {
  data: T | undefined;
  isLoading: boolean;
  isError: boolean;
  error: Error | null;
}

// GET /api/v1/ncmu/apps/{appId}/parameters (backend impl pending — see above).
export function useAppParameters(appId: string): QueryResult<ParameterSchema[]> {
  const [data, setData] = useState<ParameterSchema[] | undefined>(undefined);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setError(null);
    api<ParameterSchema[]>(`/apps/${appId}/parameters`)
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
  }, [appId]);

  return { data, isLoading, isError: error !== null, error };
}

export type WorkflowChunk = string | NcmuSseEvent;

export async function runWorkflow(
  appId: string,
  inputs: Record<string, unknown>,
  onChunk: (chunk: WorkflowChunk) => void,
  signal?: AbortSignal,
): Promise<void> {
  for await (const evt of streamChat(
    appId,
    { inputs },
    signal,
    `/workflow/apps/${appId}/run`,
  )) {
    if (evt.event === "message") {
      const ans = (evt.data as { answer?: unknown }).answer;
      if (typeof ans === "string" && ans.length > 0) onChunk(ans);
      continue;
    }
    if (
      evt.event === "node_started" ||
      evt.event === "node_finished" ||
      evt.event === "workflow_finished" ||
      evt.event === "agent_thought" ||
      evt.event === "tool_call"
    ) {
      onChunk(evt.data as NcmuSseEvent);
    }
    // ping / message_end / error / unknown event names — drop. The page
    // can still render a generic error via runWorkflow's rejection if
    // streamChat throws; per-frame `error` events go to the orchestrator.
  }
}
