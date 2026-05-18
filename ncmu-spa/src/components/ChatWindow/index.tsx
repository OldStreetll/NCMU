import { notification } from "antd";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import {
  type ErrorData,
  type MessageData,
  type MessageEndData,
  type SessionCreatedData,
  streamChat,
} from "@/lib/streamChat";
// TASK-C (B-NEW-32) 维度 32: NCMU envelope types now sourced from the
// canonical sse-types module (streamChat re-exports them but importing
// directly avoids the extra indirection).
import type { NcmuSseEvent, WorkflowFinishedData } from "@/lib/sse-types";
import { ChatInput } from "./ChatInput";
import { type ChatMessage, type MessageRole } from "./MessageBubble";
import { MessageList } from "./MessageList";

// TASK-70a: 5 NCMU envelope event names. The streamChat consumer's switch
// `default` branch matches against this set to decide whether to forward
// the parsed envelope to `onNcmuEvent` (set lookup is O(1)). Keep this in
// sync with `SseEventType` / `NcmuSseEventType` in @/lib/sse-types — the
// Literal there is authoritative (TASK-C 维度 32 schema 单源); this Set is
// only for runtime membership testing.
const NCMU_EVENT_NAMES: ReadonlySet<string> = new Set<string>([
  "node_started",
  "node_finished",
  "workflow_finished",
  "agent_thought",
  "tool_call",
]);

export interface ChatWindowProps {
  appId: string;
  // NCMU UUID (`ChatSession.id`) of the active session. Identifies the row
  // for the REST endpoints — drives the `/sessions/<sessionId>/messages`
  // history fetch below and seeds the URL slot. Caller-controlled: initial
  // mount value comes from URL param, post-mount changes (only via
  // SessionList click in TASK-32) trigger history refetch. URL-driven
  // changes must NOT update this prop (AC#4 C9-2 hard rule).
  sessionId: string | null;
  // TASK-FIX-50 (B-NEW-50): dify upstream `conversation_id` of the active
  // session. SEPARATE from `sessionId` because the backend uses two
  // identification regimes — REST endpoints key off NCMU UUID
  // (sessions/routes.py:88), the streaming-chat orchestrator keys off
  // dify_conversation_id (chat/orchestrator.py:109). Used to seed
  // activeConvRef so the streaming body's `conversation_id` field routes
  // to the right upstream conversation. Omitted / null in the cold-bookmark
  // mount case: ChatWindow self-resolves it from the messages-fetch
  // response below (the Dify message rows carry `conversation_id`).
  conversationId?: string | null;
  // Notify parent when the backend creates a new conversation mid-stream.
  // The argument is the NCMU UUID (drives URL navigate + rail refetch);
  // ChatWindow has already updated its internal activeConvRef with the
  // dify_conversation_id from the same envelope, so the parent does NOT
  // need to track that side. Parent MUST NOT update its own sessionId
  // state from this — that would invalidate the in-flight stream by
  // triggering the [sessionId] history-refetch effect below.
  onSessionCreated?: (sessionId: string) => void;
  // TASK-70a (PLAN-FIX-3 H-NEW-1): forward NCMU SSE envelope events
  // (`node_started` / `node_finished` / `workflow_finished` / `agent_thought`
  // / `tool_call`) to the parent page so it can render a sider node-flow
  // panel etc. The callback receives the FULL envelope (NcmuSseEvent) — the
  // discriminator is `evt.event_type`; inner per-event payload sits at
  // `evt.data` (a sub-schema or `Record<string, unknown>` for ping/error).
  // Backend `routes.py:96+109` confirms this shape: the SSE frame's `data:`
  // line is `evt.model_dump_json()` of the entire envelope.
  onNcmuEvent?: (evt: NcmuSseEvent) => void;
  // TASK-70a (PLAN-FIX-3 F-NEW-1 + PLAN-FIX-4 F-FRESH-2): override the
  // default `/chat/${appId}` endpoint. The string MUST be RELATIVE to
  // `API_BASE` (= "/api/v1/ncmu"), e.g. "/workflow/apps/X/run" — passing
  // the full path "/api/v1/ncmu/workflow/apps/X/run" will yield a
  // double-prefix 404 (F-FRESH-2). When this prop is set, ChatWindow
  // additionally:
  //   (1) switches the request body to the workflow 3-layer shape
  //       `{inputs: {inputs: {}, query, conversation_id}}` per backend
  //       advanced_chat.py:58-64 / agent_chat.py:50-56 — see buildBody
  //       comment below (TASK-81 / B-NEW-26b);
  //   (2) injects `workflow_finished` event's inner `data.outputs.answer`
  //       into the assistant bubble — see PLAN-FIX-4 H-FRESH-3 below.
  streamEndpointOverride?: string;
}

// REWORK-31 MF-1: GET /api/v1/ncmu/sessions/:id/messages is a Dify passthrough
// (backend MessagesOut: {data, has_more, limit}). Each record represents one
// Q/A turn — fan out into up to two ChatMessage rows (user, assistant) so the
// MessageList renders the same alternating bubble shape that streaming events
// build up live. Earlier shape `{messages: [{role, content, ...}]}` was
// mock-self-consistent but wrong against the real backend (3rd instance of
// memory feedback_tdd_mock_vs_real_api).
interface DifyMessageRow {
  id: string;
  query?: string;
  answer?: string;
  retriever_resources?: unknown[];
  created_at?: number;
  conversation_id?: string;
}

interface MessagesResponse {
  data: DifyMessageRow[];
  has_more: boolean;
  limit: number;
}

function flattenDifyMessages(rows: DifyMessageRow[]): ChatMessage[] {
  const flat: ChatMessage[] = [];
  for (const r of rows) {
    if (r.query) {
      flat.push({
        role: "user",
        content: r.query,
        message_id: `${r.id}-q`,
      });
    }
    if (r.answer) {
      flat.push({
        role: "assistant",
        content: r.answer,
        message_id: r.id,
        retriever_resources: r.retriever_resources,
      });
    }
  }
  return flat;
}

// TASK-70a body adapter; TASK-81 (B-NEW-26b) lifts the workflow branch to
// the real 3-layer wire shape.
//
// Chat endpoint expects flat `{query, conversation_id}` per Phase 1
// contract — unchanged here.
//
// Workflow endpoint expects 3 layers per backend orchestrator contracts
// (advanced_chat.py:58-64 + agent_chat.py:50-56 — the two orchestrators
// ChatWindow override traffic actually targets):
//   body = { inputs: { inputs: <form_vars>, query: <text>, conversation_id: <conv> } }
//          ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
//          POST root    Pydantic-stripped         backend orchestrator
//                       outer dict                inner reads
// `<form_vars>` defaults to `{}` here because ChatWindow has no DynamicInputForm
// concept — AdvancedChatPage / AgentChatPage today do not collect form vars
// (the chat-style turn provides only `query` text). Adding a form-vars prop
// is out of scope for TASK-81 (B-NEW-26b); pre-fix the buildBody output
// was `{ inputs: { query, conversation_id } }` (one layer short) which
// caused backend `inputs.get("inputs", {})` to return `{}` AND
// `inputs.get("query", "")` to return `""` — Dify upstream saw empty
// vars + empty query and 400/stalled.
//
// `conversationId ?? ""` because the orchestrator default is `""` not null
// and the backend reads `inputs.get("conversation_id", "")`.
//
// TASK-FIX-50: param renamed `sessionId` → `conversationId` to reflect the
// post-bug-fix semantics — the value here is `activeConvRef.current` which
// holds the Dify upstream conversation_id, not the NCMU session UUID.
function buildBody(
  text: string,
  conversationId: string | null,
  useWorkflowShape: boolean,
): Record<string, unknown> {
  if (useWorkflowShape) {
    return {
      inputs: {
        inputs: {},
        query: text,
        conversation_id: conversationId ?? "",
      },
    };
  }
  return { query: text, conversation_id: conversationId };
}

export function ChatWindow({
  appId,
  sessionId,
  conversationId = null,
  onSessionCreated = () => {},
  onNcmuEvent,
  streamEndpointOverride,
}: ChatWindowProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [streaming, setStreaming] = useState(false);

  // Tracks the dify_conversation_id that subsequent submits should continue
  // (TASK-FIX-50: NOT the NCMU session UUID — the streaming-chat orchestrator
  // queries `ChatSession.dify_conversation_id == conversation_id`). Seeded
  // from the `conversationId` prop, back-filled from the messages-fetch
  // response in the cold-bookmark case, and rewritten in-place when the
  // backend emits ncmu.session_created mid-stream. Kept in a ref so post-
  // mint mutation does NOT trigger the history-fetch effect.
  const activeConvRef = useRef<string | null>(conversationId);
  const abortRef = useRef<AbortController | null>(null);

  // unmount cleanup — must abort any in-flight stream
  useEffect(
    () => () => {
      abortRef.current?.abort();
    },
    [],
  );

  // sessionId / conversationId prop change: post-mount switch initiated by
  // parent (e.g. TASK-32 SessionList click). Abort current stream, sync
  // activeConvRef, fetch fresh history. Initial mount also runs this once.
  useEffect(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    // Prefer the explicit conversationId prop (rail-click path); the cold-
    // bookmark fallback below resolves it from the messages response when
    // the parent only knew the NCMU UUID.
    activeConvRef.current = conversationId;

    if (!sessionId) {
      setMessages([]);
      return;
    }

    let cancelled = false;
    api<MessagesResponse>(`/sessions/${sessionId}/messages`)
      .then((resp) => {
        if (cancelled) return;
        setMessages(flattenDifyMessages(resp.data));
        // TASK-FIX-50 bookmark fallback: when the parent mounts from a URL
        // it knows only the NCMU UUID, so `conversationId` arrives null.
        // The Dify message rows carry `conversation_id` (the upstream id);
        // adopt the first row's value so a subsequent submit on this
        // bookmarked URL routes to the right upstream conversation rather
        // than starting a new one.
        if (!activeConvRef.current && resp.data.length > 0) {
          const fromHistory = resp.data[0]?.conversation_id;
          if (fromHistory) activeConvRef.current = fromHistory;
        }
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        const msg = e instanceof ApiError ? e.message : "加载历史失败";
        notification.error({ message: "加载历史失败", description: msg });
      });

    return () => {
      cancelled = true;
    };
  }, [sessionId, conversationId]);

  const showSseError = useCallback((err: ErrorData) => {
    // PLAN-FIX-1: backend collapses Dify SSE error strings + INSERT/JWT/param
    // into single NCMU code 9001; SPA shows generic title + backend `message`
    // as subtitle (carries the upstream cause).
    const title = err.code === 9001 ? "AI 服务暂不可用，请稍后重试" : `错误 ${err.code}`;
    notification.error({ message: title, description: err.message });
  }, []);

  const handleSubmit = useCallback(
    async (query: string) => {
      // If a previous stream is still draining, abort before starting a new one.
      abortRef.current?.abort();
      const ctl = new AbortController();
      abortRef.current = ctl;

      // Optimistic append: user message + assistant placeholder. Track the
      // assistant slot index so streaming chunks attach to the right bubble.
      let assistantIdx = -1;
      setMessages((prev) => {
        const next = [
          ...prev,
          { role: "user" as MessageRole, content: query },
          { role: "assistant" as MessageRole, content: "" },
        ];
        assistantIdx = next.length - 1;
        return next;
      });
      setStreaming(true);

      // TASK-70a: route + body shape both key off streamEndpointOverride.
      const useWorkflowShape = streamEndpointOverride !== undefined;
      const body = buildBody(query, activeConvRef.current, useWorkflowShape);

      try {
        for await (const evt of streamChat(
          appId,
          body,
          ctl.signal,
          streamEndpointOverride,
        )) {
          switch (evt.event) {
            case "ncmu.session_created": {
              // TASK-FIX-50 (B-NEW-50): backend `chat/orchestrator.py:184-189`
              // emits BOTH `session_id` (NCMU UUID, drives URL + rail) AND
              // `dify_conversation_id` (drives streaming routing). Pre-fix
              // we stored `session_id` in activeConvRef which then went into
              // the next submit's body — orchestrator's
              // `ChatSession.dify_conversation_id == conversation_id` lookup
              // never matched and we got 9001 on every send into the freshly-
              // minted session. The two ids must be tracked separately.
              //
              // streamChat.ts `SessionCreatedData` declares
              // `conversation_id?` but the actual envelope ships
              // `dify_conversation_id`; aligning the type is out of scope
              // for TASK-FIX-50 (streamChat.ts is restricted), so widen the
              // cast locally and prefer the actual field name with a
              // fallback for forward-compat.
              const d = evt.data as SessionCreatedData & {
                dify_conversation_id?: string;
              };
              const newNcmuId = d.session_id;
              const newDifyConvId =
                d.dify_conversation_id ?? d.conversation_id ?? null;
              if (newDifyConvId && newDifyConvId !== activeConvRef.current) {
                activeConvRef.current = newDifyConvId;
              }
              if (newNcmuId) {
                onSessionCreated(newNcmuId);
              }
              break;
            }
            case "message": {
              const d = evt.data as MessageData;
              const piece = typeof d.answer === "string" ? d.answer : "";
              if (!piece) break;
              setMessages((prev) => {
                if (assistantIdx < 0 || assistantIdx >= prev.length) return prev;
                const next = prev.slice();
                next[assistantIdx] = {
                  ...next[assistantIdx],
                  content: next[assistantIdx].content + piece,
                };
                return next;
              });
              break;
            }
            case "message_end": {
              const d = evt.data as MessageEndData;
              setMessages((prev) => {
                if (assistantIdx < 0 || assistantIdx >= prev.length) return prev;
                const next = prev.slice();
                next[assistantIdx] = {
                  ...next[assistantIdx],
                  message_id: d.message_id,
                  retriever_resources:
                    d.metadata?.retriever_resources ?? d.retriever_resources ?? [],
                };
                return next;
              });
              break;
            }
            case "error":
              showSseError(evt.data as ErrorData);
              break;
            case "ping":
              // SSE heartbeat — nothing to do.
              break;
            default: {
              // TASK-70a: NCMU envelope events
              // (node_started / node_finished / workflow_finished /
              // agent_thought / tool_call). Backend
              // `ncmu_backend/workflow/routes.py:96+109` serializes the
              // entire NcmuSseEvent envelope to the SSE `data:` line — so
              // `evt.data` here is the envelope object, not the inner
              // sub-schema.
              if (onNcmuEvent && NCMU_EVENT_NAMES.has(evt.event)) {
                onNcmuEvent(evt.data as NcmuSseEvent);
              }
              // PLAN-FIX-4 H-FRESH-3: chat-area injection for
              // advanced-chat / agent-chat / workflow pages.
              //
              // When `streamEndpointOverride` is set we are on the
              // workflow endpoint, where Phase 1 `message` / `message_end`
              // events ARE NOT emitted by the orchestrator: it accumulates
              // upstream and ships the final answer as the
              // `workflow_finished` envelope's
              // `envelope.data.outputs.answer`. Without this injection the
              // assistant bubble would stay empty forever (sider
              // node-flow visible but main chat area blank — the
              // H-FRESH-3 finding).
              //
              // The guard `streamEndpointOverride !== undefined` keeps
              // the chat path completely untouched: in chat mode
              // `message` / `message_end` build the bubble and
              // `workflow_finished` is never emitted by Phase 1 backend
              // anyway.
              //
              // PLAN-vs-BASELINE NOTE (baseline-first per task brief
              // 2026-05-09): plan TASK-70a line 1670 shorthand
              // `(evt.data as {outputs?: {answer?: string}})` skips the
              // envelope layer, which would compile but always read
              // `undefined` against real backend output. Plan AC#4(b)
              // explicitly states the envelope shape
              // `{event_type, run_id, timestamp, data}`, and backend
              // `routes.py:96` confirms it via
              // `evt.model_dump(mode="json")`. Hence we narrow through
              // `(evt.data as NcmuSseEvent).data` to reach the inner
              // `WorkflowFinishedData.outputs.answer`. AC#4(f)'s
              // shorthand `evt.data.outputs.answer = "hello world"` is
              // interpreted as the test mock's intended outcome — the
              // mock emits the envelope shape and the bubble's
              // textContent ends up `"hello world"`.
              if (
                streamEndpointOverride !== undefined &&
                evt.event === "workflow_finished"
              ) {
                const env = evt.data as NcmuSseEvent | null;
                const wf = (env?.data ?? {}) as Partial<WorkflowFinishedData>;
                const outputs = wf.outputs as
                  | { answer?: unknown }
                  | undefined;
                const answer = outputs?.answer;
                if (typeof answer === "string" && answer.length > 0) {
                  setMessages((prev) => {
                    if (assistantIdx < 0 || assistantIdx >= prev.length) {
                      return prev;
                    }
                    const next = prev.slice();
                    next[assistantIdx] = {
                      ...next[assistantIdx],
                      content: answer,
                    };
                    return next;
                  });
                }
              }
              // Other unknown events keep the prior pre-TASK-70a silent
              // ignore behaviour.
              break;
            }
          }
        }
      } catch (e: unknown) {
        if (e instanceof Error && e.name === "AbortError") return;
        const msg = e instanceof Error ? e.message : "对话失败";
        notification.error({ message: "对话失败", description: msg });
      } finally {
        if (abortRef.current === ctl) {
          abortRef.current = null;
        }
        setStreaming(false);
      }
    },
    [
      appId,
      onSessionCreated,
      showSseError,
      onNcmuEvent,
      streamEndpointOverride,
    ],
  );

  return (
    <div
      data-slot="chat-window"
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        minHeight: 0,
      }}
    >
      <MessageList messages={messages} />
      <ChatInput onSubmit={handleSubmit} disabled={streaming} />
    </div>
  );
}

export default ChatWindow;
