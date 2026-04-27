// TASK-31: user/assistant bubble shell. TASK-32 will append a chip strip
// (retriever_resources) below assistant content; the props slot for that is
// `retrieverResources` already exposed here so the wire is type-safe later.

export type MessageRole = "user" | "assistant";

export interface ChatMessage {
  role: MessageRole;
  content: string;
  message_id?: string;
  retriever_resources?: unknown[];
}

export function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";
  return (
    <div
      data-role={message.role}
      style={{
        display: "flex",
        justifyContent: isUser ? "flex-end" : "flex-start",
        margin: "8px 0",
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
  );
}
