// TASK-FIX-50 (B-NEW-50) — ChatPage two-id wiring.
//
// The bug under test: pre-fix, SessionList click sent only `session.id`
// (NCMU UUID) through ChatPage to ChatWindow, which then put it into the
// streaming body's `conversation_id` field. The backend orchestrator
// queries `ChatSession.dify_conversation_id == conversation_id`, so the
// NCMU UUID never matched and every send into a historical session got
// 9001. Plan B (this task) splits the wire into two ids:
//
//   SessionList.onChange  →  full Session row (both ids).
//   ChatPage state         →  ActiveSession {id, dify_conversation_id}.
//   ChatWindow.sessionId   →  NCMU UUID (drives /sessions/<x>/messages
//                             history fetch + URL slot).
//   ChatWindow.conversationId
//                          →  dify_conversation_id (drives streaming body's
//                             conversation_id field).
//
// Cases below verify the wiring at the ChatPage level (ChatWindow is
// mocked so we can assert exactly which prop got which id).

import {
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { setAuth } from "@/lib/auth";
import type { Session } from "@/components/SessionList";

// Hoisted spy — the vi.mock factory is hoisted above imports, so the spy
// must be hoisted too in order to be referenced from the factory.
const { chatWindowSpy } = vi.hoisted(() => ({ chatWindowSpy: vi.fn() }));

vi.mock("@/components/ChatWindow", () => ({
  // Bare-bones component that records every props snapshot it receives.
  // Re-renders are normal here (parent state changes flow as new props),
  // so the test inspects `mock.calls.at(-1)` for the latest snapshot.
  ChatWindow: (props: Record<string, unknown>) => {
    chatWindowSpy(props);
    return <div data-testid="mock-chat-window" />;
  },
}));

// MUST come after vi.mock so the lazy resolution sees the mocked module.
import ChatPage from "@/pages/ChatPage";

const sampleSessions: Session[] = [
  {
    id: "11111111-1111-4111-8111-111111111111",
    dify_app_id: "app-1",
    dify_conversation_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    title: "年假咨询",
    created_at: "2026-04-25T10:00:00Z",
    updated_at: "2026-04-26T11:00:00Z",
  },
  {
    id: "22222222-2222-4222-8222-222222222222",
    dify_app_id: "app-1",
    dify_conversation_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    title: "薪资问题",
    created_at: "2026-04-24T09:00:00Z",
    updated_at: "2026-04-25T10:00:00Z",
  },
];

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    statusText: "",
  } as unknown as Response;
}

// Antd 5 Modal/Form pull in the Grid useBreakpoint hook which calls
// window.matchMedia. jsdom does not implement it; polyfill once here.
beforeAll(() => {
  if (!window.matchMedia) {
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      writable: true,
      value: (query: string): MediaQueryList =>
        ({
          matches: false,
          media: query,
          onchange: null,
          addListener: () => {},
          removeListener: () => {},
          addEventListener: () => {},
          removeEventListener: () => {},
          dispatchEvent: () => false,
        }) as MediaQueryList,
    });
  }
});

beforeEach(() => {
  setAuth("test-jwt", {
    id: "a0000001-0000-4000-8000-000000000001",
    name: "tester",
    is_active: true,
    is_admin: false,
  });
  chatWindowSpy.mockClear();
  // SessionList GETs /api/v1/ncmu/sessions on mount.
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve(
        jsonResponse({ items: sampleSessions, next_cursor: null }),
      ),
    ),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/chat" element={<ChatPage />} />
        <Route path="/chat/:sessionId" element={<ChatPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

function lastChatWindowProps(): {
  sessionId: string | null;
  conversationId: string | null;
} {
  const calls = chatWindowSpy.mock.calls;
  expect(calls.length).toBeGreaterThan(0);
  const props = calls.at(-1)?.[0] as {
    sessionId: string | null;
    conversationId: string | null;
  };
  return props;
}

describe("<ChatPage> two-id wiring (TASK-FIX-50 / B-NEW-50)", () => {
  it("AC#1 — fresh /chat mount: ChatWindow sessionId=null AND conversationId=null", async () => {
    renderAt("/chat");
    // The ChatWindow mock renders synchronously on first paint.
    await waitFor(() => expect(chatWindowSpy).toHaveBeenCalled());
    const p = lastChatWindowProps();
    expect(p.sessionId).toBeNull();
    expect(p.conversationId).toBeNull();
  });

  it("AC#2 — rail click forwards both NCMU UUID AND dify_conversation_id to ChatWindow as separate props", async () => {
    renderAt("/chat");

    // Wait for SessionList to fetch + render rows.
    const item = await screen.findByText("薪资问题");
    fireEvent.click(item);

    await waitFor(() => {
      const p = lastChatWindowProps();
      // sessionId = NCMU UUID — drives /sessions/<x>/messages history fetch
      // + URL slot. (REST endpoints key off ChatSession.id.)
      expect(p.sessionId).toBe(sampleSessions[1].id);
      // conversationId = dify_conversation_id — drives streaming body's
      // conversation_id. (Orchestrator keys off
      // ChatSession.dify_conversation_id.) THIS is the field that fixes
      // the historical-session 9001 bug.
      expect(p.conversationId).toBe(sampleSessions[1].dify_conversation_id);
      // Distinct id spaces — guards against any future regression that
      // collapses them back into one.
      expect(p.sessionId).not.toBe(p.conversationId);
    });
  });

  it("AC#3 — cold-bookmark mount /chat/<NCMU_UUID>: sessionId=URL, conversationId=null (ChatWindow self-resolves from /messages)", async () => {
    // URL slot carries the NCMU UUID; the dify_conversation_id is unknown
    // up-front and ChatWindow back-fills it from the messages-fetch
    // response (covered separately in ChatWindow tests). At ChatPage level
    // the contract is: sessionId mirrors URL, conversationId starts null.
    renderAt(`/chat/${sampleSessions[0].id}`);
    await waitFor(() => expect(chatWindowSpy).toHaveBeenCalled());
    const p = lastChatWindowProps();
    expect(p.sessionId).toBe(sampleSessions[0].id);
    expect(p.conversationId).toBeNull();
  });
});
