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

// --- TASK-70a / TASK-C (B-NEW-32) 维度 32 schema 单源: NCMU SSE envelope types
// canonical source ------------------------------------------------------------
// Pre-TASK-C history: this module held an inline duplicate of the 5
// sub-schemas + envelope (TASK-70a path B / colocated with streamChat) while
// `@/lib/sse-types` was rolled out by TASK-70b-1. TASK-C 维度 32 closes that
// backlog by re-exporting the canonical types from sse-types so the SPA has
// a single source of truth (matches backend `schemas/sse_events.py` 5 sub-
// schemas + NcmuSseEvent envelope — TASK-68 commit e6a1bc1).
//
// Note: `ErrorData` / `MessageData` / `MessageEndData` / `SessionCreatedData`
// (Phase 1 chat-mode types declared above) stay LOCAL to streamChat.ts —
// they describe the original Dify chat SSE event payloads, NOT NCMU's
// workflow envelope; sse-types is intentionally narrowed to the envelope.
export type {
  NodeStartedData,
  NodeFinishedData,
  WorkflowFinishedData,
  AgentThoughtData,
  ToolCallData,
  NcmuSseEventType,
  NcmuSseEvent,
} from "@/lib/sse-types";
// --- end re-export ----------------------------------------------------------

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
