import { useEffect, useRef } from "react";
import { type ChatMessage, MessageBubble } from "./MessageBubble";

export function MessageList({ messages }: { messages: ChatMessage[] }) {
  const tailRef = useRef<HTMLDivElement | null>(null);

  // Auto-scroll to the latest message as new chunks stream in. jsdom (test
  // env) doesn't implement scrollIntoView, so feature-detect.
  useEffect(() => {
    const el = tailRef.current;
    if (el && typeof el.scrollIntoView === "function") {
      el.scrollIntoView({ block: "end", behavior: "smooth" });
    }
  }, [messages.length, messages[messages.length - 1]?.content]);

  return (
    <div
      data-slot="message-list"
      style={{
        flex: 1,
        overflowY: "auto",
        padding: "16px",
        display: "flex",
        flexDirection: "column",
      }}
    >
      {messages.map((m, i) => (
        <MessageBubble key={m.message_id ?? `idx-${i}`} message={m} />
      ))}
      <div ref={tailRef} />
    </div>
  );
}
