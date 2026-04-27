// TASK-31 overwrites the TASK-29 stub. TASK-32 (this revision) wires the
// SessionList rail into the aside slot.
//
// Architecture (PLAN-FIX-2 Q2 + PLAN-FIX-4 C9-2 + TASK-32 fork-join close):
// - activeSessionId is internal state; URL :sessionId only seeds the initial
//   value. Browser back/forward / programmatic navigate do NOT mutate it.
// - onSessionCreated (called from ChatWindow when Dify mints a new conv id):
//     * navigates URL replace so the address bar reflects the new session id
//     * refetches SessionList so the new session shows up in the rail
//     * MUST NOT touch activeSessionId — doing so would abort the just-started
//       stream and refetch an empty history (PLAN-FIX-4 C9-2).
// - SessionList onChange (rail click) DOES setActiveSessionId AND navigate —
//   that path intentionally triggers history refetch via ChatWindow's
//   [sessionId] effect.
import { useCallback, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ChatWindow } from "@/components/ChatWindow";
import { SessionList, type SessionListHandle } from "@/components/SessionList";

// TASK-PLAN-FIX-1 / Phase 1 default: a single Dify [KB] App. Multi-app
// switching ships in a later phase; until then the appId is fixed.
const DEFAULT_APP_ID = "default";

export default function ChatPage() {
  const params = useParams<{ sessionId?: string }>();
  const navigate = useNavigate();

  // useState lazy initializer — mount-time URL snapshot, NOT reactive.
  const [activeSessionId, setActiveSessionId] = useState<string | null>(
    () => params.sessionId ?? null,
  );
  const sessionListRef = useRef<SessionListHandle>(null);

  const handleSessionCreated = useCallback(
    (newId: string) => {
      navigate(`/chat/${newId}`, { replace: true });
      // Refresh the rail so the freshly-minted session appears.
      void sessionListRef.current?.refetch();
    },
    [navigate],
  );

  // Rail click — flip active session AND mirror URL. Both moves are needed:
  // setState drives ChatWindow's [sessionId] effect (history refetch); navigate
  // keeps the URL bookmarkable without touching browser history depth.
  const handleSessionPick = useCallback(
    (id: string) => {
      setActiveSessionId(id);
      navigate(`/chat/${id}`, { replace: true });
    },
    [navigate],
  );

  return (
    <div
      data-slot="chat-page"
      style={{
        display: "flex",
        height: "100vh",
        minHeight: 0,
      }}
    >
      <aside
        data-slot="session-list"
        style={{ width: 280, borderRight: "1px solid #f0f0f0", overflowY: "auto" }}
      >
        <SessionList
          ref={sessionListRef}
          selectedId={activeSessionId ?? undefined}
          onChange={handleSessionPick}
        />
      </aside>
      <main style={{ flex: 1, minWidth: 0 }}>
        <ChatWindow
          appId={DEFAULT_APP_ID}
          sessionId={activeSessionId}
          onSessionCreated={handleSessionCreated}
        />
      </main>
    </div>
  );
}
