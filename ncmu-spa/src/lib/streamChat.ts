// SSE async generator for POST /api/v1/ncmu/chat/{appId}.
// EventSource is unusable here: it cannot send Authorization headers, and the
// backend expects POST + JSON body, not GET. Hence fetch + ReadableStream.
//
// Cancellation contract (M-4):
// - caller passes AbortController.signal; abort() before/during fetch rejects.
// - try/finally guarantees reader.cancel() on any exit (return / throw / break).
// - reader.cancel() failures are swallowed: the connection is already useless.

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

export type StreamEventName =
  | "ncmu.session_created"
  | "message"
  | "message_end"
  | "error"
  | "ping"
  | (string & {});

export interface StreamEvent {
  event: StreamEventName;
  data: unknown;
}

export async function* streamChat(
  appId: string,
  body: { query: string; conversation_id: string | null },
  signal?: AbortSignal,
): AsyncGenerator<StreamEvent> {
  const jwt = getJwt();
  if (!jwt) throw new Error("no jwt");

  const resp = await fetch(`${API_BASE}/chat/${appId}`, {
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
