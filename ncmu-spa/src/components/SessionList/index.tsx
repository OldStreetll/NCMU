import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useState,
} from "react";
import { Empty, Modal, Spin, message } from "antd";
import { ApiError, api } from "@/lib/api";
import { SessionItem } from "./SessionItem";
import { RenameModal } from "./RenameModal";

export type Session = {
  id: string;
  dify_app_id: string;
  dify_conversation_id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
};

type SessionListResponse = {
  items: Session[];
  next_cursor: string | null;
};

export type SessionListHandle = {
  refetch: () => Promise<void>;
};

export type SessionListProps = {
  // NCMU UUID (`Session.id`) of the active session — drives row highlight
  // and matches the URL slot `/chat/:sessionId` (which carries the NCMU id).
  selectedId?: string;
  // TASK-FIX-50 (B-NEW-50): forward the entire Session row, not just one
  // id. The consumer needs both `id` (NCMU UUID, for /sessions/<x>/messages
  // + URL) and `dify_conversation_id` (for the streaming-chat body's
  // conversation_id field — backend `chat/orchestrator.py:109` queries
  // `ChatSession.dify_conversation_id == conversation_id`). Passing only
  // `session.id` was the cause of the historical-session 9001 bug.
  onChange?: (session: Session) => void;
};

export const SessionList = forwardRef<SessionListHandle, SessionListProps>(
  function SessionList({ selectedId, onChange }, ref) {
    const [sessions, setSessions] = useState<Session[]>([]);
    const [loading, setLoading] = useState(true);
    const [renameTarget, setRenameTarget] = useState<Session | null>(null);
    const [modal, modalContextHolder] = Modal.useModal();
    const [messageApi, messageContextHolder] = message.useMessage();

    const fetchSessions = useCallback(async () => {
      setLoading(true);
      try {
        const data = await api<SessionListResponse>("/sessions");
        setSessions(data.items);
      } catch (err) {
        const msg = err instanceof ApiError ? err.message : "加载会话列表失败";
        messageApi.error(msg);
      } finally {
        setLoading(false);
      }
    }, [messageApi]);

    useImperativeHandle(ref, () => ({ refetch: fetchSessions }), [fetchSessions]);

    useEffect(() => {
      void fetchSessions();
    }, [fetchSessions]);

    const handleRenamed = useCallback((updated: Session) => {
      setSessions((prev) =>
        prev.map((s) => (s.id === updated.id ? { ...s, title: updated.title } : s)),
      );
      setRenameTarget(null);
    }, []);

    const handleDelete = useCallback(
      (target: Session) => {
        modal.confirm({
          title: "删除会话",
          content: `确认删除会话 "${target.title ?? "未命名"}" ？此操作不可撤销。`,
          okText: "删除",
          okButtonProps: { danger: true },
          cancelText: "取消",
          onOk: async () => {
            try {
              await api(`/sessions/${target.id}`, { method: "DELETE" }).catch(
                (err: unknown) => {
                  // Backend returns 204 No Content; api() unconditionally parses
                  // resp.json() and throws SyntaxError on empty body. Swallow that
                  // single case; real ApiError still surfaces.
                  if (err instanceof SyntaxError) return;
                  throw err;
                },
              );
              setSessions((prev) => prev.filter((s) => s.id !== target.id));
              messageApi.success("已删除");
            } catch (err) {
              const msg = err instanceof ApiError ? err.message : "删除失败";
              messageApi.error(msg);
              throw err;
            }
          },
        });
      },
      [messageApi, modal],
    );

    return (
      <div data-testid="session-list" style={{ height: "100%", overflowY: "auto" }}>
        {modalContextHolder}
        {messageContextHolder}
        {loading ? (
          <div style={{ display: "flex", justifyContent: "center", padding: 24 }}>
            <Spin />
          </div>
        ) : sessions.length === 0 ? (
          <Empty description="暂无会话" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        ) : (
          <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
            {sessions.map((session) => (
              <SessionItem
                key={session.id}
                session={session}
                selected={session.id === selectedId}
                onSelect={() => onChange?.(session)}
                onRename={() => setRenameTarget(session)}
                onDelete={() => handleDelete(session)}
              />
            ))}
          </ul>
        )}
        <RenameModal
          target={renameTarget}
          onCancel={() => setRenameTarget(null)}
          onRenamed={handleRenamed}
        />
      </div>
    );
  },
);

export default SessionList;
