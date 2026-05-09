import { notification } from "antd";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import {
  type ErrorData,
  type MessageData,
  type MessageEndData,
  type NcmuSseEvent,
  type SessionCreatedData,
  type WorkflowFinishedData,
  streamChat,
} from "@/lib/streamChat";
import { ChatInput } from "./ChatInput";
import { type ChatMessage, type MessageRole } from "./MessageBubble";
import { MessageList } from "./MessageList";

// TASK-70a: 5 NCMU envelope event names. The streamChat consumer's switch
// `default` branch matches against this set to decide whether to forward
// the parsed envelope to `onNcmuEvent` (set lookup is O(1)). Keep this in
// sync with `NcmuSseEventType` in @/lib/streamChat — the Literal there is
// authoritative; this Set is only for runtime membership testing.
const NCMU_EVENT_NAMES: ReadonlySet<string> = new Set<string>([
  "node_started",
  "node_finished",
  "workflow_finished",
  "agent_thought",
  "tool_call",
]);

export interface ChatWindowProps {
  appId: string;
  // Caller-controlled active session — initial mount value comes from URL param,
  // post-mount changes (only via SessionList click in TASK-32) trigger history
  // refetch. URL-driven changes must NOT update this prop (AC#4 C9-2 hard rule).
  sessionId: string | null;
  // Notify parent when the backend creates a new conversation mid-stream.
  // Parent's sole job: navigate(replace:true). Parent MUST NOT update its
  // own sessionId state from this — that would invalidate the in-flight
  // stream by triggering the [sessionId] history-refetch effect below.
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
  //   (1) switches the request body to the workflow shape
  //       `{inputs: {query, conversation_id}}` (backend
  //       WorkflowRunRequest schema);
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

// TASK-70a body adapter. Workflow endpoint expects `{inputs: {...}}` per
// backend WorkflowRunRequest schema; chat endpoint expects flat
// `{query, conversation_id}` per Phase 1 contract. `useWorkflowShape` keys
// off whether `streamEndpointOverride` is set (chat path = undefined).
// `sessionId ?? ""` for workflow shape because WorkflowRunRequest's
// `conversation_id` is a Pydantic str field (no `Optional[]`).
function buildBody(
  text: string,
  sessionId: string | null,
  useWorkflowShape: boolean,
): Record<string, unknown> {
  if (useWorkflowShape) {
    return { inputs: { query: text, conversation_id: sessionId ?? "" } };
  }
  return { query: text, conversation_id: sessionId };
}

export function ChatWindow({
  appId,
  sessionId,
  onSessionCreated = () => {},
  onNcmuEvent,
  streamEndpointOverride,
}: ChatWindowProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [streaming, setStreaming] = useState(false);

  // Tracks the conversation that subsequent submits should continue. Starts
  // from `sessionId` prop and is rewritten in-place when the backend emits
  // ncmu.session_created mid-stream — does NOT trigger a re-render of the
  // history fetch effect (kept in a ref).
  const activeConvRef = useRef<string | null>(sessionId);
  const abortRef = useRef<AbortController | null>(null);

  // unmount cleanup — must abort any in-flight stream
  useEffect(
    () => () => {
      abortRef.current?.abort();
    },
    [],
  );

  // sessionId prop change: post-mount switch initiated by parent (e.g.
  // TASK-32 SessionList click). Abort current stream, sync activeConvRef,
  // fetch fresh history. Initial mount also runs this once.
  useEffect(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    activeConvRef.current = sessionId;

    if (!sessionId) {
      setMessages([]);
      return;
    }

    let cancelled = false;
    api<MessagesResponse>(`/sessions/${sessionId}/messages`)
      .then((resp) => {
        if (cancelled) return;
        setMessages(flattenDifyMessages(resp.data));
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        const msg = e instanceof ApiError ? e.message : "加载历史失败";
        notification.error({ message: "加载历史失败", description: msg });
      });

    return () => {
      cancelled = true;
    };
  }, [sessionId]);

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
              const d = evt.data as SessionCreatedData;
              const newId = d.session_id;
              if (newId && newId !== activeConvRef.current) {
                activeConvRef.current = newId;
                onSessionCreated(newId);
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
