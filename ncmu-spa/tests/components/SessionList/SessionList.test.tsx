import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { SessionList, type Session } from "@/components/SessionList";
import { setAuth } from "@/lib/auth";

const sampleSessions: Session[] = [
  {
    id: "11111111-1111-4111-8111-111111111111",
    dify_app_id: "app-1",
    dify_conversation_id: "conv-1",
    title: "年假咨询",
    created_at: "2026-04-25T10:00:00Z",
    updated_at: "2026-04-26T11:00:00Z",
  },
  {
    id: "22222222-2222-4222-8222-222222222222",
    dify_app_id: "app-1",
    dify_conversation_id: "conv-2",
    title: "薪资问题",
    created_at: "2026-04-24T09:00:00Z",
    updated_at: "2026-04-25T10:00:00Z",
  },
];

type FetchMock = ReturnType<typeof vi.fn>;
let fetchMock: FetchMock;

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    statusText: "",
  } as unknown as Response;
}

function noContentResponse(): Response {
  // 204 No Content — api() unconditionally calls resp.json() which throws
  // SyntaxError on empty body in real fetch; mirror that here so the
  // SessionList swallow-SyntaxError path is exercised.
  return {
    ok: true,
    status: 204,
    json: async () => {
      throw new SyntaxError("Unexpected end of JSON input");
    },
    statusText: "",
  } as unknown as Response;
}

function queueListResponse(sessions: Session[] = sampleSessions): void {
  fetchMock.mockImplementationOnce(() =>
    Promise.resolve(jsonResponse({ items: sessions, next_cursor: null })),
  );
}

// Antd 5 Modal + Form pull in the Grid useBreakpoint hook which calls
// window.matchMedia. jsdom does not implement it, so polyfill once here.
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
  });
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("<SessionList>", () => {
  it("AC#1 — fetches and renders sessions on mount", async () => {
    queueListResponse();
    render(<SessionList />);
    expect(await screen.findByText("年假咨询")).toBeInTheDocument();
    expect(screen.getByText("薪资问题")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/ncmu/sessions");
    expect((init.headers as Record<string, string>)["Authorization"]).toBe("Bearer test-jwt");
  });

  it("AC#2 — right-click on a session item opens the rename / delete menu", async () => {
    queueListResponse();
    render(<SessionList />);
    const item = await screen.findByText("年假咨询");
    fireEvent.contextMenu(item);
    expect(await screen.findByText("重命名")).toBeInTheDocument();
    expect(screen.getByText("删除")).toBeInTheDocument();
  });

  it("AC#3 — rename submits PATCH and updates the list title in place", async () => {
    queueListResponse();
    fetchMock.mockImplementationOnce(() =>
      Promise.resolve(
        jsonResponse({
          ...sampleSessions[0],
          title: "年假咨询（更新）",
          updated_at: "2026-04-27T12:00:00Z",
        }),
      ),
    );

    render(<SessionList />);
    const item = await screen.findByText("年假咨询");
    fireEvent.contextMenu(item);
    fireEvent.click(await screen.findByText("重命名"));

    const dialog = await screen.findByRole("dialog");
    const input = within(dialog).getByTestId("rename-input") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "年假咨询（更新）" } });
    // Antd 5 inserts a space between two CJK chars in button text → 保 存
    fireEvent.click(within(dialog).getByRole("button", { name: /^保\s?存$/ }));

    await waitFor(() => {
      expect(screen.getByText("年假咨询（更新）")).toBeInTheDocument();
    });
    expect(screen.queryByText("年假咨询", { selector: "div" })).toBeNull();

    // Find the PATCH call (skip GET /sessions); verify URL + method + body.
    const patchCall = fetchMock.mock.calls.find(
      (c) => (c[1] as RequestInit | undefined)?.method === "PATCH",
    );
    expect(patchCall).toBeDefined();
    const [patchUrl, patchInit] = patchCall as [string, RequestInit];
    expect(patchUrl).toBe(`/api/v1/ncmu/sessions/${sampleSessions[0].id}`);
    expect(JSON.parse(patchInit.body as string)).toEqual({ title: "年假咨询（更新）" });
  });

  it("AC#4 — delete asks for confirmation then DELETEs and removes the item", async () => {
    queueListResponse();
    fetchMock.mockImplementationOnce(() => Promise.resolve(noContentResponse()));

    render(<SessionList />);
    const item = await screen.findByText("年假咨询");
    fireEvent.contextMenu(item);
    fireEvent.click(await screen.findByText("删除"));

    // Antd Modal.confirm renders into a role="dialog" portal.
    const confirmDialog = await screen.findByRole("dialog");
    expect(within(confirmDialog).getByText(/确认删除会话/)).toBeInTheDocument();
    // Two-CJK-char button text "删除" is auto-spaced by Antd 5 → "删 除".
    fireEvent.click(within(confirmDialog).getByRole("button", { name: /^删\s?除$/ }));

    await waitFor(() => {
      expect(screen.queryByText("年假咨询")).toBeNull();
    });
    expect(screen.getByText("薪资问题")).toBeInTheDocument();

    const deleteCall = fetchMock.mock.calls.find(
      (c) => (c[1] as RequestInit | undefined)?.method === "DELETE",
    );
    expect(deleteCall).toBeDefined();
    expect(deleteCall![0]).toBe(`/api/v1/ncmu/sessions/${sampleSessions[0].id}`);
  });

  it("AC#5 — clicking a session fires onChange with the full Session object (TASK-FIX-50 / B-NEW-50)", async () => {
    queueListResponse();
    const onChange = vi.fn();
    render(<SessionList onChange={onChange} />);
    const item = await screen.findByText("薪资问题");
    fireEvent.click(item);
    expect(onChange).toHaveBeenCalledTimes(1);
    // Whole row, not just the id — the consumer needs both `id` (NCMU UUID,
    // for /sessions/<x>/messages + URL) and `dify_conversation_id` (for the
    // streaming-chat body's conversation_id). Pre-fix we passed only
    // `session.id` and historical-session sends got 9001 because the
    // orchestrator queries by dify_conversation_id, not NCMU UUID.
    expect(onChange).toHaveBeenCalledWith(sampleSessions[1]);
    const arg = onChange.mock.calls[0][0] as Session;
    expect(arg.id).toBe(sampleSessions[1].id);
    expect(arg.dify_conversation_id).toBe(sampleSessions[1].dify_conversation_id);
    expect(arg.dify_conversation_id).not.toBe(arg.id); // distinct id spaces
  });
});
