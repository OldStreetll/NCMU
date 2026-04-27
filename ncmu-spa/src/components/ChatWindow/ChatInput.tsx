import { Button, Input } from "antd";
import { type KeyboardEvent, useState } from "react";

export interface ChatInputProps {
  onSubmit: (query: string) => void | Promise<void>;
  disabled?: boolean;
  placeholder?: string;
}

export function ChatInput({ onSubmit, disabled, placeholder }: ChatInputProps) {
  const [value, setValue] = useState("");

  const submit = () => {
    const q = value.trim();
    if (!q || disabled) return;
    setValue("");
    void onSubmit(q);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    // Enter submits; Shift+Enter inserts newline.
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <div
      data-slot="chat-input"
      style={{
        display: "flex",
        gap: 8,
        padding: 12,
        borderTop: "1px solid #f0f0f0",
        alignItems: "flex-end",
      }}
    >
      <Input.TextArea
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={onKeyDown}
        autoSize={{ minRows: 1, maxRows: 6 }}
        placeholder={placeholder ?? "输入问题（Enter 发送 / Shift+Enter 换行）"}
        disabled={disabled}
      />
      <Button type="primary" onClick={submit} disabled={disabled || !value.trim()}>
        发送
      </Button>
    </div>
  );
}
