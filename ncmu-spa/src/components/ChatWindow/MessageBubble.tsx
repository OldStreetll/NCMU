// TASK-31: user/assistant bubble shell. TASK-32 (this revision) extends the
// assistant variant with a retriever_resources chip strip rendered below the
// bubble. retriever_resources is shaped per Dify's message_end frame; the
// strip uses RetrieverChip for each entry.

import { RetrieverChip, type RetrieverResource } from "@/components/RetrieverChip";

export type MessageRole = "user" | "assistant";

export interface ChatMessage {
  role: MessageRole;
  content: string;
  message_id?: string;
  retriever_resources?: unknown[];
}

export function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";
  const resources = !isUser
    ? ((message.retriever_resources ?? []) as RetrieverResource[])
    : [];
  const hasResources = resources.length > 0;

  return (
    <div data-role={message.role} style={{ margin: "8px 0" }}>
      <div
        style={{
          display: "flex",
          justifyContent: isUser ? "flex-end" : "flex-start",
        }}
      >
        <div
          style={{
            maxWidth: "75%",
            padding: "8px 12px",
            borderRadius: 8,
            background: isUser ? "#1677ff" : "#f5f5f5",
            color: isUser ? "#fff" : "#000",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
          }}
        >
          {message.content || (message.role === "assistant" ? "…" : "")}
        </div>
      </div>
      {hasResources && (
        <div
          data-slot="retriever-chips"
          style={{ display: "flex", justifyContent: "flex-start", marginTop: 4 }}
        >
          <div style={{ maxWidth: "75%", display: "flex", flexWrap: "wrap" }}>
            {resources.map((r, i) => (
              <RetrieverChip
                key={r.segment_id ?? r.document_id ?? `chip-${i}`}
                resource={r}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
