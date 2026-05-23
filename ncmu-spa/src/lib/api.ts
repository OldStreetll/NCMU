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
// double-prefix trap).
//
// ★ TASK-81 (B-NEW-26b) — 3-layer wire shape: the POST body MUST be
//   `{ inputs: { inputs: <form_vars>, query?: <text>, conversation_id?: <conv> } }`
//   per backend orchestrator contracts (commit c320cd9):
//     - completion.py:43-48      reads inputs.get("inputs", {}) + inputs.get("query", "")
//     - workflow.py:51-55        reads inputs.get("inputs", {}) (no query / no conv)
//     - advanced_chat.py:58-64   reads all 3 inner fields
//     - agent_chat.py:50-56      reads all 3 inner fields
//   Pydantic `WorkflowRunRequest.inputs: dict[str, Any]` strips one layer at
//   the endpoint boundary, so the orchestrator's local `inputs` is the
//   INNER dict — meaning the wire body needs two `inputs` keys nested.
//   Pre-fix the SPA shipped `{ inputs: <form_values> }` (one layer short),
//   so backend `inputs.get("inputs", {})` returned `{}` and `inputs.get("query", "")`
//   returned "" → upstream Dify saw empty form vars + empty query and would
//   400 or stall (B-NEW-26b production bug).
//
// The callback receives one of two shapes per frame:
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

// TASK-81 (B-NEW-26b): explicit inner-layer fields the backend orchestrators
// read off Pydantic's stripped `inputs` dict. All optional — only `inputs`
// (form vars) is universal across the 4 modes; `query` is required by
// completion / advanced-chat / agent-chat but ignored by workflow; and
// `conversation_id` is required by advanced-chat / agent-chat only. Callers
// pass whichever fields apply to their target mode; runWorkflow serializes
// only the fields explicitly set so untouched modes don't see junk keys.
export interface RunWorkflowInputs {
  inputs?: Record<string, unknown>;
  query?: string;
  conversation_id?: string;
}

export async function runWorkflow(
  appId: string,
  inputs: RunWorkflowInputs,
  onChunk: (chunk: WorkflowChunk) => void,
  signal?: AbortSignal,
): Promise<void> {
  // TASK-81 (B-NEW-26b): construct the inner pydantic dict explicitly so
  // the POST body is `{ inputs: { inputs, query?, conversation_id? } }` —
  // the 3-layer wire shape backend orchestrators actually consume. See the
  // module-top comment for the per-mode field map.
  const inner: Record<string, unknown> = { inputs: inputs.inputs ?? {} };
  if (inputs.query !== undefined) inner.query = inputs.query;
  if (inputs.conversation_id !== undefined) {
    inner.conversation_id = inputs.conversation_id;
  }
  for await (const evt of streamChat(
    appId,
    { inputs: inner },
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

// --- TASK-PC3-C (Phase 2C) — KB content panel (errata-13 §2.5) ------------
//
// Two helpers used by ``AppKbContentPanel`` to list / download the files
// behind an App's bound KBs.
//
// Backend wire shape (byte-exact with ncmu-backend FastGPTReadOnlyClient
// `FileMeta` —守 feedback_tdd_mock_vs_real_api SOP 11):
//   {
//     file_id: str,
//     original_filename: str,
//     size_bytes: int,
//     uploaded_at: str,
//     content_type: str,
//   }
//
// Endpoints (PC2-FIX prefix decision — `/api/v1/ncmu/kbs/...` so nginx
// `^~ /api/v1/ncmu/` reaches ncmu-backend; the prior `/v1/kbs/...` shape
// would have hit the Dify upstream — see docker/nginx/dev.conf.template).

export interface KbFile {
  file_id: string;
  original_filename: string;
  size_bytes: number;
  uploaded_at: string;
  content_type: string;
}

// GET /api/v1/ncmu/kbs/{appId}/files → list[KbFile]
export function listKbFiles(appId: string): Promise<KbFile[]> {
  return api<KbFile[]>(`/kbs/${encodeURIComponent(appId)}/files`);
}

// Resolve the download URL for a single KB file. Callers pass this to
// ``window.open`` so the browser handles the stream natively (plan §4.8
// AC#3 — no front-end blob buffering; backend sets Content-Disposition).
export function getKbFileDownloadUrl(appId: string, fileId: string): string {
  return `${BASE}/kbs/${encodeURIComponent(appId)}/files/${encodeURIComponent(fileId)}/download`;
}
