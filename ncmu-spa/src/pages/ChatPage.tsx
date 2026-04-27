// TASK-31 overwrites the TASK-29 stub. TASK-32 will extend this file to
// import + render @/components/SessionList inside the aside placeholder.
//
// Architecture (PLAN-FIX-2 Q2 + PLAN-FIX-4 C9-2):
// - activeSessionId is internal state; URL :sessionId only seeds the initial
//   value. Browser back/forward / programmatic navigate do NOT mutate it.
// - onSessionCreated callback ONLY navigates the URL (replace). It MUST NOT
//   update activeSessionId, or the [sessionId] effect inside ChatWindow will
//   abort the just-started stream and refetch an empty history.
// - Future SessionList click (TASK-32) will call setActiveSessionId AND
//   navigate(replace) — that path intentionally triggers history refetch.
import { useCallback, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ChatWindow } from "@/components/ChatWindow";

// TASK-PLAN-FIX-1 / Phase 1 default: a single Dify [KB] App. Multi-app
// switching ships in a later phase; until then the appId is fixed.
const DEFAULT_APP_ID = "default";

export default function ChatPage() {
  const params = useParams<{ sessionId?: string }>();
  const navigate = useNavigate();

  // useState lazy initializer — mount-time URL snapshot, NOT reactive.
  const [activeSessionId] = useState<string | null>(() => params.sessionId ?? null);

  const handleSessionCreated = useCallback(
    (newId: string) => {
      navigate(`/chat/${newId}`, { replace: true });
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
        {/* TASK-32 wires SessionList */}
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
