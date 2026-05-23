// TASK-31 overwrites the TASK-29 stub. TASK-32 wires the SessionList rail
// into the aside slot. TASK-FIX-50 (B-NEW-50) moves to a two-id model.
//
// Two-id model (TASK-FIX-50):
// - The backend has TWO session-identification regimes:
//     * REST endpoints (/sessions/<id>/messages, PATCH, DELETE) key off
//       `ChatSession.id` — the NCMU UUID.
//     * The streaming-chat orchestrator keys off
//       `ChatSession.dify_conversation_id` — the upstream Dify id.
//   The previously-shipped one-id model (passing only NCMU UUID through
//   the chain) caused 9001 on every send into a historical session.
// - ChatPage now holds both ids of the active session and forwards each
//   to the right consumer:
//     * SessionList.selectedId  ← NCMU UUID (matches URL slot).
//     * ChatWindow.sessionId    ← NCMU UUID (drives /sessions/<x>/messages
//                                history fetch).
//     * ChatWindow.conversationId ← dify_conversation_id (drives the
//                                streaming body's conversation_id field).
//
// URL contract (PLAN-FIX-2 Q2 + PLAN-FIX-4 C9-2 + TASK-32 fork-join close,
// preserved):
// - URL :sessionId carries the NCMU UUID (routes.tsx is unchanged).
// - URL only seeds the initial mount; browser back/forward / programmatic
//   navigate do NOT mutate activeSession.
// - SessionList onChange (rail click) DOES update activeSession AND
//   navigate — that path triggers history refetch via ChatWindow's
//   [sessionId] effect.
// - onSessionCreated (called from ChatWindow when Dify mints a new conv id):
//     * navigates URL replace so the address bar reflects the new NCMU id
//     * refetches SessionList so the new session shows up in the rail
//     * does NOT touch activeSession — doing so would abort the just-
//       started stream and refetch an empty history (PLAN-FIX-4 C9-2).
//   ChatWindow's internal activeConvRef already holds the freshly-minted
//   dify_conversation_id (it pulls it directly from the session_created
//   envelope), so the next submit in the same conversation routes
//   correctly without ChatPage tracking it.
//
// Bookmark scenario (`/chat/<NCMU_UUID>` opened cold):
// - activeSession initialised from URL with id = NCMU UUID and
//   dify_conversation_id = null (we don't know it yet).
// - ChatWindow loads /sessions/<NCMU_UUID>/messages OK (REST keyed by
//   NCMU id) and self-resolves the dify_conversation_id from the first
//   row's `conversation_id` field for subsequent submits — see ChatWindow
//   [sessionId, conversationId] effect.
import { useCallback, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ChatWindow } from "@/components/ChatWindow";
import AppKbContentPanel from "@/components/AppKbContentPanel";
import {
  SessionList,
  type Session,
  type SessionListHandle,
} from "@/components/SessionList";

// TASK-PLAN-FIX-1 / Phase 1 default: a single Dify [KB] App. Multi-app
// switching ships in a later phase; until then the appId is fixed.
const DEFAULT_APP_ID = "default";

// Slim active-session shape — `dify_conversation_id` may be null in the
// cold-bookmark case where only the URL UUID is known up-front.
type ActiveSession = {
  id: string;
  dify_conversation_id: string | null;
};

export default function ChatPage() {
  const params = useParams<{ sessionId?: string }>();
  const navigate = useNavigate();

  // useState lazy initializer — mount-time URL snapshot, NOT reactive.
  // URL slot carries the NCMU UUID; bookmark mount sets the dify side null
  // and ChatWindow back-fills it from the messages-fetch response.
  const [activeSession, setActiveSession] = useState<ActiveSession | null>(
    () =>
      params.sessionId
        ? { id: params.sessionId, dify_conversation_id: null }
        : null,
  );
  const sessionListRef = useRef<SessionListHandle>(null);

  const handleSessionCreated = useCallback(
    (newNcmuId: string) => {
      navigate(`/chat/${newNcmuId}`, { replace: true });
      // Refresh the rail so the freshly-minted session appears.
      void sessionListRef.current?.refetch();
    },
    [navigate],
  );

  // Rail click — flip active session AND mirror URL. Both moves are needed:
  // setState drives ChatWindow's [sessionId] / [conversationId] effects;
  // navigate keeps the URL bookmarkable (URL slot = NCMU UUID).
  const handleSessionPick = useCallback(
    (session: Session) => {
      setActiveSession({
        id: session.id,
        dify_conversation_id: session.dify_conversation_id,
      });
      navigate(`/chat/${session.id}`, { replace: true });
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
          selectedId={activeSession?.id ?? undefined}
          onChange={handleSessionPick}
        />
      </aside>
      <main
        style={{
          flex: 1,
          minWidth: 0,
          display: "flex",
          flexDirection: "column",
        }}
      >
        {/* TASK-PC3-C: KB content panel sits above ChatWindow as a layout
            element — plan §4.8 禁区字面 "不嵌入 ChatWindow". The panel is
            collapsed by default and lazy-loads on expand. */}
        <AppKbContentPanel appId={DEFAULT_APP_ID} />
        <div style={{ flex: 1, minHeight: 0 }}>
          <ChatWindow
            appId={DEFAULT_APP_ID}
            sessionId={activeSession?.id ?? null}
            conversationId={activeSession?.dify_conversation_id ?? null}
            onSessionCreated={handleSessionCreated}
          />
        </div>
      </main>
    </div>
  );
}
