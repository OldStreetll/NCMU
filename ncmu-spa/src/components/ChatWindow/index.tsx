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
import { ChatInput } from "./ChatInput";
import { type ChatMessage, type MessageRole } from "./MessageBubble";
import { MessageList } from "./MessageList";

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

export function ChatWindow({
  appId,
  sessionId,
  onSessionCreated = () => {},
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

      try {
        for await (const evt of streamChat(
          appId,
          { query, conversation_id: activeConvRef.current },
          ctl.signal,
        )) {
          if (evt.event === "ncmu.session_created") {
            const d = evt.data as SessionCreatedData;
            const newId = d.session_id;
            if (newId && newId !== activeConvRef.current) {
              activeConvRef.current = newId;
              onSessionCreated(newId);
            }
          } else if (evt.event === "message") {
            const d = evt.data as MessageData;
            const piece = typeof d.answer === "string" ? d.answer : "";
            if (!piece) continue;
            setMessages((prev) => {
              if (assistantIdx < 0 || assistantIdx >= prev.length) return prev;
              const next = prev.slice();
              next[assistantIdx] = {
                ...next[assistantIdx],
                content: next[assistantIdx].content + piece,
              };
              return next;
            });
          } else if (evt.event === "message_end") {
            const d = evt.data as MessageEndData;
            setMessages((prev) => {
              if (assistantIdx < 0 || assistantIdx >= prev.length) return prev;
              const next = prev.slice();
              next[assistantIdx] = {
                ...next[assistantIdx],
                message_id: d.message_id,
                retriever_resources: d.retriever_resources,
              };
              return next;
            });
          } else if (evt.event === "error") {
            showSseError(evt.data as ErrorData);
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
    [appId, onSessionCreated, showSseError],
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
