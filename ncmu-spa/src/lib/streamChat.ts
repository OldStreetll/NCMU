// SSE async generator for POST {API_BASE}{endpointPath}.
// `endpointPath` defaults to `/chat/${appId}` (Phase 1 chat baseline).
// EventSource is unusable here: it cannot send Authorization headers, and the
// backend expects POST + JSON body, not GET. Hence fetch + ReadableStream.
//
// Cancellation contract (M-4):
// - caller passes AbortController.signal; abort() before/during fetch rejects.
// - try/finally guarantees reader.cancel() on any exit (return / throw / break).
// - reader.cancel() failures are swallowed: the connection is already useless.
//
// TASK-70a (PLAN-FIX-3 F-NEW-1 + H-NEW-1 + PLAN-FIX-4 F-FRESH-2): adds
// (a) `endpointPath?: string` parameter — workflow / advanced-chat / agent-chat
//     pages pass `/workflow/apps/${appId}/run` (RELATIVE to API_BASE; never
//     include the `/api/v1/ncmu` prefix — F-FRESH-2 double-prefix 404 trap);
// (b) `body` type widened to `Record<string, unknown>` so callers can ship
//     either chat shape `{query, conversation_id}` or workflow shape
//     `{inputs: {...}}` through the same primitive;
// (c) StreamEventName union extended with 5 NCMU envelope event names
//     (node_started / node_finished / workflow_finished / agent_thought /
//     tool_call) plus `(string & {})` future-compat tail.

import { getJwt } from "@/lib/auth";
import { API_BASE } from "@/lib/api";

export interface SessionCreatedData {
  session_id: string;
  conversation_id?: string;
}

export interface MessageData {
  answer?: string;
  [k: string]: unknown;
}

export interface MessageEndData {
  message_id?: string;
  retriever_resources?: unknown[];
  metadata?: { retriever_resources?: unknown[]; [k: string]: unknown };
  [k: string]: unknown;
}

export interface ErrorData {
  code: number;
  message: string;
  [k: string]: unknown;
}

// --- TASK-70a: NCMU SSE envelope types (path B / inline) -------------------
// Why inline here rather than `@/lib/sse-types`: plan TASK-70a line 1617
// references `@/lib/sse-types` which TASK-70 (B3 batch) was scheduled to
// create first, but TASK-70 has not run yet (current dep gate: 67a/67b/68/69
// committed + INDEP four-pass). Pane 0 / Boss decided path B
// ([INTENT-CHECK-ACK] 2026-05-09) — colocate with streamChat.ts since this
// file is the natural SSE-types module. TASK-70 will refactor these out
// into ncmu-spa/src/lib/sse-types.ts via [SCOPE-CHANGE] protocol; consumers
// that import from "@/lib/streamChat" today will switch to
// "@/lib/sse-types" then.
//
// Field names + literals mirror backend
// `ncmu-backend/src/ncmu_backend/schemas/sse_events.py` 5 sub-schemas + the
// envelope (TASK-68, commit e6a1bc1). Pydantic `dict[str, Any]` maps to TS
// `Record<string, unknown>`. NIT-INDEP68-1 (TASK-68 backend Union ordering
// sensitivity): keep `ping` / `error` envelope-data branch on the
// `Record<string, unknown>` tail of the union — none of the keys
// node_id / node_type / thought / tool_name / status / tool_input may
// appear on those control-plane payloads, otherwise structural narrowing
// could mis-attribute them to a sibling member.

export interface NodeStartedData {
  node_id: string;
  node_type: string;
  title?: string | null;
  inputs?: Record<string, unknown>;
}

export interface NodeFinishedData {
  node_id: string;
  node_type: string;
  // H2 (PLAN-FIX-2) terminal status mapping: 4 collapsed values.
  status: "succeeded" | "failed" | "stopped" | "exception";
  outputs?: Record<string, unknown>;
  elapsed_ms?: number | null;
  error?: string | null;
}

export interface WorkflowFinishedData {
  // partial-succeeded → succeeded per H2 mapping.
  status: "succeeded" | "failed" | "stopped" | "exception";
  outputs?: Record<string, unknown>;
  total_elapsed_ms?: number | null;
  error?: string | null;
}

export interface AgentThoughtData {
  thought: string;
  observation?: string | null;
  tool_name?: string | null;
}

export interface ToolCallData {
  tool_name: string;
  tool_input: Record<string, unknown>;
  tool_output?: unknown;
  status: "calling" | "completed" | "failed";
}

export type NcmuSseEventType =
  | "node_started"
  | "node_finished"
  | "workflow_finished"
  | "agent_thought"
  | "tool_call"
  | "ping"
  | "error";

// The envelope every NCMU workflow SSE frame ships in. Matches backend
// `NcmuSseEvent` Pydantic model; backend `routes.py:96+109` serializes
// the entire envelope to the SSE `data:` line, so frontend SSE parsers
// receive THIS shape (not the inner per-event sub-schema).
export interface NcmuSseEvent {
  event_type: NcmuSseEventType;
  run_id: string;
  // ISO-8601 timestamp string (Pydantic datetime → JSON string).
  timestamp: string;
  data:
    | NodeStartedData
    | NodeFinishedData
    | WorkflowFinishedData
    | AgentThoughtData
    | ToolCallData
    | Record<string, unknown>;
}
// --- end TASK-70a inline types ----------------------------------------------

export type StreamEventName =
  | "ncmu.session_created"
  | "message"
  | "message_end"
  | "error"
  | "ping"
  // TASK-70a: NCMU envelope event names (5 mode-specific frames). The
  // SSE frame's `event:` line carries the same Literal value as
  // `NcmuSseEventType` (see backend routes.py:109 yielding
  // `evt.event_type`); the trailing `(string & {})` keeps the union
  // open for future event names without breaking existing narrowing.
  | "node_started"
  | "node_finished"
  | "workflow_finished"
  | "agent_thought"
  | "tool_call"
  | (string & {});

export interface StreamEvent {
  event: StreamEventName;
  data: unknown;
}

export async function* streamChat(
  appId: string,
  // TASK-70a: widened from `{query, conversation_id}` so callers can ship
  // workflow body shape `{inputs: {...}}` through the same primitive.
  body: Record<string, unknown>,
  signal?: AbortSignal,
  // TASK-70a: optional override; defaults to `/chat/${appId}` (Phase 1
  // chat baseline). Path is RELATIVE to `API_BASE` — never prepend
  // `/api/v1/ncmu` (F-FRESH-2: double-prefix would 404).
  endpointPath?: string,
): AsyncGenerator<StreamEvent> {
  const jwt = getJwt();
  if (!jwt) throw new Error("no jwt");

  const path = endpointPath ?? `/chat/${appId}`;
  const resp = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${jwt}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
    signal,
  });
  if (!resp.ok) {
    const t = await resp.text();
    throw new Error(`HTTP ${resp.status}: ${t}`);
  }
  if (!resp.body) throw new Error("no body");

  const reader = resp.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += value;
      let idx: number;
      while ((idx = buffer.indexOf("\n\n")) >= 0) {
        const frame = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        const evt = parseSseFrame(frame);
        if (evt) yield evt;
      }
    }
  } finally {
    try {
      await reader.cancel();
    } catch {
      // intentional
    }
  }
}

export function parseSseFrame(frame: string): StreamEvent | null {
  let event: StreamEventName = "message";
  let data: unknown = null;
  for (const line of frame.split("\n")) {
    // Heartbeat / SSE comment frames start with ":" — drop them.
    if (line.startsWith(":")) continue;
    if (line.startsWith("event: ")) {
      event = line.slice(7).trim();
    } else if (line.startsWith("data: ")) {
      try {
        data = JSON.parse(line.slice(6));
      } catch {
        // tolerate malformed data lines
      }
    }
  }
  if (data === null) return null;
  return { event, data };
}
