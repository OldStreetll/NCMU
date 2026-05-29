import { useEffect, useState } from "react";
import { getJwt } from "@/lib/auth";
import type { ParameterSchema } from "@/components/workflow/DynamicInputForm";
import { streamChat } from "@/lib/streamChat";
import type { NcmuSseEvent } from "@/lib/sse-types";

// TASK-PC3-A: Personal-KB application shape — mirrors backend
// `schemas/personal_kb.py:ApplicationOut`. Status literal kept narrow so
// MyKbPage's STATUS_COLOR / STATUS_LABEL maps are exhaustive against the
// type system (TS 5.5 catches missing branches the moment backend adds a
// 6th value — pre-existing `feedback_status_enum_cross_stack_sync` SOP).
export type PersonalKbApplicationStatus =
  | "pending"
  | "in_progress"
  | "done"
  | "rejected"
  | "cancelled";

export interface PersonalKbApplication {
  id: string;
  user_id: string;
  kb_name_suggested: string | null;
  description: string | null;
  status: PersonalKbApplicationStatus;
  fastgpt_dataset_id: string | null;
  dify_app_id: string | null;
  admin_processed_by: string | null;
  admin_processed_at: string | null;
  rejection_reason: string | null;
  created_at: string;
}

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
    // dev-login + dev/users return 404 with detail={code,message}; FastAPI's
    // default HTTPException returns `detail: "<string>"`. TASK-PC3-B widens
    // the handler so personal_kb admin endpoints (409 "该申请已被其他管理员认领"
    // etc.) surface their detail string as err.message instead of dropping
    // it back to "Conflict" statusText.
    const rawDetail = (body as unknown as {
      detail?: { code?: number; message?: string } | string;
    }).detail;
    const detailObj =
      typeof rawDetail === "object" && rawDetail !== null ? rawDetail : undefined;
    const detailStr = typeof rawDetail === "string" ? rawDetail : undefined;
    const code = detailObj?.code ?? body.code;
    const message =
      detailObj?.message ?? detailStr ?? body.message ?? resp.statusText;
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

// --- TASK-PE-03 — list-apps endpoint -----------------------------------
//
// `App` mirrors backend `schemas/apps.py:AppOut` (Phase 1 fixed `type:
// "kb_qa"`). PE-03 Path C (Boss 2026-05-28): plan §3 字面 imported a
// hallucinated `fetchApps` + `@tanstack/react-query` + an `icon` field
// that AppOut doesn't carry. Resolution: add the missing one-liner here
// (mirrors listMyApplications below), keep the project's useState +
// useEffect + api() helper pattern in the page (no react-query), and
// fall back to a mode-based emoji for the missing icon.
export interface App {
  id: string;
  name: string;
  type: "kb_qa";
  mode: string;
  description?: string | null;
}

// GET /api/v1/ncmu/apps — list the Apps the current user can chat with
// (shared via DifyConsoleClient ∪ owner via app_owners + dify_apps cache
// — see backend apps/services.py:list_apps_for_user).
export async function fetchApps(): Promise<App[]> {
  return api<App[]>("/apps");
}

// --- TASK-PC3-A — Personal-KB endpoints --------------------------------
//
// Backend prefix is `/api/v1/ncmu/personal-kb/applications` (PC2-FIX 2026-05-23,
// reuses the existing ncmu BASE so nginx ^~ /api/v1/ncmu/ + vite proxy stay
// unchanged — see prior wire-shape T2 INTENT-CHECK). All three helpers go
// through `api()` for GET / and inline fetch for POST multipart + DELETE 204
// (the JSON-only api() helper would set Content-Type: application/json on
// the multipart body and choke on the empty 204 response respectively).

export interface SubmitApplicationInput {
  kb_name_suggested: string;
  description?: string;
  files: File[];
}

// GET /api/v1/ncmu/personal-kb/applications — list current user's apps.
export async function listMyApplications(): Promise<PersonalKbApplication[]> {
  return api<PersonalKbApplication[]>("/personal-kb/applications");
}

// POST /api/v1/ncmu/personal-kb/applications — multipart submit. Browser
// auto-sets `Content-Type: multipart/form-data; boundary=...` only when
// we DO NOT pre-set the header — hence the inline fetch (api() would
// force application/json and clobber the multipart boundary).
export async function submitApplication(
  input: SubmitApplicationInput,
): Promise<PersonalKbApplication> {
  const form = new FormData();
  form.append("kb_name_suggested", input.kb_name_suggested);
  if (input.description !== undefined && input.description !== "") {
    form.append("description", input.description);
  }
  for (const f of input.files) {
    form.append("files", f, f.name);
  }
  const jwt = getJwt();
  if (!jwt) throw new ApiError(401, undefined, "no jwt");
  const resp = await fetch(`${BASE}/personal-kb/applications`, {
    method: "POST",
    headers: { Authorization: `Bearer ${jwt}` },
    body: form,
  });
  if (!resp.ok) {
    let body: { code?: number; message?: string; detail?: unknown } = {};
    try {
      body = await resp.json();
    } catch {
      // non-json response — fall through with statusText
    }
    const detail = (body as { detail?: unknown }).detail;
    let message: string = resp.statusText;
    let code: number | undefined = body.code;
    if (typeof detail === "string") {
      message = detail;
    } else if (detail && typeof detail === "object") {
      const d = detail as { code?: number; message?: string };
      code = d.code ?? code;
      message = d.message ?? message;
    } else if (body.message) {
      message = body.message;
    }
    throw new ApiError(resp.status, code, message);
  }
  return resp.json() as Promise<PersonalKbApplication>;
}

// DELETE /api/v1/ncmu/personal-kb/applications/{id} — withdraw a pending app.
// Returns 204 No Content on success (api() would explode on resp.json()).
export async function cancelApplication(applicationId: string): Promise<void> {
  const jwt = getJwt();
  if (!jwt) throw new ApiError(401, undefined, "no jwt");
  const resp = await fetch(
    `${BASE}/personal-kb/applications/${encodeURIComponent(applicationId)}`,
    { method: "DELETE", headers: { Authorization: `Bearer ${jwt}` } },
  );
  if (!resp.ok) {
    let body: { code?: number; message?: string; detail?: unknown } = {};
    try {
      body = await resp.json();
    } catch {
      // empty body / non-json — statusText falls through
    }
    const detail = (body as { detail?: unknown }).detail;
    let message: string = resp.statusText;
    let code: number | undefined = body.code;
    if (typeof detail === "string") {
      message = detail;
    } else if (detail && typeof detail === "object") {
      const d = detail as { code?: number; message?: string };
      code = d.code ?? code;
      message = d.message ?? message;
    } else if (body.message) {
      message = body.message;
    }
    throw new ApiError(resp.status, code, message);
  }
  // 204 — no body to parse.
}

// --- TASK-PC3-B — admin /admin/personal-kb endpoints ------------------------
//
// Wire shape grounded against ncmu-backend/src/ncmu_backend/schemas/personal_kb.py
// + personal_kb/admin_routes.py (Boss-confirmed 2026-05-23 after PC2-FIX wires
// the router under the `/api/v1/ncmu/admin/personal-kb` prefix that nginx
// `^~ /api/v1/ncmu/` actually routes to ncmu-backend). The api() helper above
// prepends BASE="/api/v1/ncmu" so callers pass `/admin/personal-kb/...` and
// land at the full backend route.
//
// Status enum is the cross-stack mirror of backend's `ApplicationStatus`
// Literal — keep both sides in lock-step per `feedback_status_enum_cross_stack_sync`.

export type ApplicationStatus =
  | "pending"
  | "in_progress"
  | "done"
  | "rejected"
  | "cancelled";

export interface FileMeta {
  id: string;
  original_filename: string;
  file_size_bytes: number;
  content_type: string | null;
  sha256: string | null;
  uploaded_at: string;
}

export interface ApplicationOut {
  id: string;
  user_id: string;
  kb_name_suggested: string | null;
  description: string | null;
  status: ApplicationStatus;
  fastgpt_dataset_id: string | null;
  dify_app_id: string | null;
  admin_processed_by: string | null;
  admin_processed_at: string | null;
  rejection_reason: string | null;
  created_at: string;
}

export interface ApplicationDetailOut extends ApplicationOut {
  files: FileMeta[];
}

export interface DispatchIn {
  dataset_id: string;
  dify_app_id: string;
  kb_name_final: string;
}

export interface RejectIn {
  rejection_reason: string;
}

// GET /admin/personal-kb/applications — admin pulls the FULL list (no `?status=`
// filter param) per r3 INDEP-2 I-INDEP2-3 path-b: Segmented filtering happens
// client-side, and PendingBadge counts off the same list.
export function listPendingApplications(): Promise<ApplicationOut[]> {
  return api<ApplicationOut[]>("/admin/personal-kb/applications");
}

// GET /admin/personal-kb/applications/{id} — detail + eagerly-loaded files.
export function getApplicationDetail(
  applicationId: string,
): Promise<ApplicationDetailOut> {
  return api<ApplicationDetailOut>(
    `/admin/personal-kb/applications/${applicationId}`,
  );
}

// POST /admin/personal-kb/applications/{id}/claim — pending → in_progress.
// 409 `仅待处理状态可认领` if someone else already claimed.
export function claimApplication(
  applicationId: string,
): Promise<ApplicationOut> {
  return api<ApplicationOut>(
    `/admin/personal-kb/applications/${applicationId}/claim`,
    { method: "POST" },
  );
}

// POST /admin/personal-kb/applications/{id}/dispatch — in_progress → done.
// 409 `请先认领申请` if status != in_progress; 409 `KB 名称冲突或外部资源不可用`
// on IntegrityError; 422 from backend SQLSTATE 22001 gate if kb_name_final >200.
export function dispatchApplication(
  applicationId: string,
  body: DispatchIn,
): Promise<ApplicationOut> {
  return api<ApplicationOut>(
    `/admin/personal-kb/applications/${applicationId}/dispatch`,
    { method: "POST", body: JSON.stringify(body) },
  );
}

// POST /admin/personal-kb/applications/{id}/reject — pending|in_progress → rejected.
export function rejectApplication(
  applicationId: string,
  body: RejectIn,
): Promise<ApplicationOut> {
  return api<ApplicationOut>(
    `/admin/personal-kb/applications/${applicationId}/reject`,
    { method: "POST", body: JSON.stringify(body) },
  );
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

// --- TASK-PE-06 — admin /admin/users endpoints -----------------------------
//
// Wire shape grounded against ncmu-backend/src/ncmu_backend/admin/users/
// {routes,schemas}.py. Mirrors the dict-shape error envelope used by other
// admin endpoints (``{detail: {code, message}}``); ``ApiError.code`` will
// carry 1010 (dingtalk duplicate) / 1011 (not found) / 1201 (forbidden).

export interface AdminUserOut {
  id: string;
  name: string;
  dingtalk_userid: string | null;
  dept_path: string | null;
  is_active: boolean;
  is_admin: boolean;
  tags: unknown[];
}

export interface AdminUserList {
  total: number;
  items: AdminUserOut[];
}

export interface AdminUserCreateBody {
  name: string;
  dingtalk_userid?: string | null;
  dept_path?: string | null;
}

export interface AdminUserUpdateBody {
  name?: string;
  dingtalk_userid?: string | null;
  dept_path?: string | null;
  is_active?: boolean;
}

export interface AdminUserListParams {
  page?: number;
  size?: number;
  include_inactive?: boolean;
  search?: string;
}

// GET /api/v1/ncmu/admin/users — server-side pagination + search.
export function listAdminUsers(
  params: AdminUserListParams = {},
): Promise<AdminUserList> {
  const qs = new URLSearchParams();
  if (params.page !== undefined) qs.set("page", String(params.page));
  if (params.size !== undefined) qs.set("size", String(params.size));
  if (params.include_inactive) qs.set("include_inactive", "true");
  if (params.search) qs.set("search", params.search);
  const suffix = qs.toString();
  return api<AdminUserList>(`/admin/users${suffix ? `?${suffix}` : ""}`);
}

// POST /api/v1/ncmu/admin/users — single create.
export function createAdminUser(
  body: AdminUserCreateBody,
): Promise<AdminUserOut> {
  return api<AdminUserOut>("/admin/users", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// PATCH /api/v1/ncmu/admin/users/{user_id} — partial update.
export function updateAdminUser(
  userId: string,
  body: AdminUserUpdateBody,
): Promise<AdminUserOut> {
  return api<AdminUserOut>(`/admin/users/${encodeURIComponent(userId)}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

// DELETE /api/v1/ncmu/admin/users/{user_id} — soft delete (is_active=false).
// Returns 204 No Content; api() would explode on resp.json(), so use the
// same direct-fetch pattern as ``cancelApplication`` above.
export async function deactivateAdminUser(userId: string): Promise<void> {
  const jwt = getJwt();
  if (!jwt) throw new ApiError(401, undefined, "no jwt");
  const resp = await fetch(
    `${BASE}/admin/users/${encodeURIComponent(userId)}`,
    { method: "DELETE", headers: { Authorization: `Bearer ${jwt}` } },
  );
  if (!resp.ok) {
    let body: { code?: number; message?: string; detail?: unknown } = {};
    try {
      body = await resp.json();
    } catch {
      // empty body / non-json — statusText falls through
    }
    const detail = (body as { detail?: unknown }).detail;
    let message: string = resp.statusText;
    let code: number | undefined = body.code;
    if (typeof detail === "string") {
      message = detail;
    } else if (detail && typeof detail === "object") {
      const d = detail as { code?: number; message?: string };
      code = d.code ?? code;
      message = d.message ?? message;
    } else if (body.message) {
      message = body.message;
    }
    throw new ApiError(resp.status, code, message);
  }
  // 204 — no body to parse.
}

// --- TASK-PE-05 — admin /admin/tags endpoints -------------------------------
//
// Wire shape grounded against ncmu-backend/src/ncmu_backend/admin/tags/
// {routes,schemas}.py. Mirrors the dict-shape error envelope used by other
// admin endpoints (``{detail: {code, message}}``); ``ApiError.code`` will
// carry 1012 (duplicate name) / 1013 (not found) / 1201 (forbidden).

export interface AdminTagOut {
  id: string;
  name: string;
  description: string | null;
  app_count: number;
  user_count: number;
}

export interface AdminTagCreateBody {
  name: string;
  description?: string | null;
}

export interface AdminTagUpdateBody {
  name?: string;
  description?: string | null;
}

// GET /api/v1/ncmu/admin/tags — list all tags + COUNT JOIN (no pagination v1).
export function listAdminTags(): Promise<AdminTagOut[]> {
  return api<AdminTagOut[]>("/admin/tags");
}

// POST /api/v1/ncmu/admin/tags — single create.
export function createAdminTag(
  body: AdminTagCreateBody,
): Promise<AdminTagOut> {
  return api<AdminTagOut>("/admin/tags", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// PATCH /api/v1/ncmu/admin/tags/{tag_id} — partial update.
export function updateAdminTag(
  tagId: string,
  body: AdminTagUpdateBody,
): Promise<AdminTagOut> {
  return api<AdminTagOut>(`/admin/tags/${encodeURIComponent(tagId)}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

// DELETE /api/v1/ncmu/admin/tags/{tag_id} — hard delete (FK CASCADE clears
// app_tags + user_tags join rows). Returns 204 No Content; api() would
// explode on resp.json(), so use the direct-fetch pattern (mirror PE-06
// ``deactivateAdminUser``).
export async function deleteAdminTag(tagId: string): Promise<void> {
  const jwt = getJwt();
  if (!jwt) throw new ApiError(401, undefined, "no jwt");
  const resp = await fetch(
    `${BASE}/admin/tags/${encodeURIComponent(tagId)}`,
    { method: "DELETE", headers: { Authorization: `Bearer ${jwt}` } },
  );
  if (!resp.ok) {
    let body: { code?: number; message?: string; detail?: unknown } = {};
    try {
      body = await resp.json();
    } catch {
      // empty body / non-json — statusText falls through
    }
    const detail = (body as { detail?: unknown }).detail;
    let message: string = resp.statusText;
    let code: number | undefined = body.code;
    if (typeof detail === "string") {
      message = detail;
    } else if (detail && typeof detail === "object") {
      const d = detail as { code?: number; message?: string };
      code = d.code ?? code;
      message = d.message ?? message;
    } else if (body.message) {
      message = body.message;
    }
    throw new ApiError(resp.status, code, message);
  }
  // 204 — no body to parse.
}
