// TASK-PC3-B — AdminPersonalKbPage wiring tests.
//
// Covers: admin route-level guard / list render / Segmented filter (default
// pending) / PendingBadge alert / 30s polling cadence / row-click navigation.
//
// Fetch is mocked with real Response objects (SOP 10: no never-resolving
// stubs) so the api() helper's body-parsing branch executes for real.

import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { RequireAuth } from "@/components/RequireAuth";
import { AdminPersonalKbPage } from "@/pages/AdminPersonalKbPage";
import type { ApplicationOut } from "@/lib/api";
import { setAuth, clearAuth } from "@/lib/auth";

// ----- jsdom polyfills antd 5 Grid breakpoints rely on ---------------------
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

// ----- fixtures ------------------------------------------------------------
const APP_PENDING_1: ApplicationOut = {
  id: "11111111-1111-4111-8111-111111111111",
  user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  kb_name_suggested: "工程手册",
  description: "运维团队工程手册",
  status: "pending",
  fastgpt_dataset_id: null,
  dify_app_id: null,
  admin_processed_by: null,
  admin_processed_at: null,
  rejection_reason: null,
  created_at: "2026-05-23T10:00:00Z",
};

const APP_PENDING_2: ApplicationOut = {
  ...APP_PENDING_1,
  id: "22222222-2222-4222-8222-222222222222",
  kb_name_suggested: "财务规范",
};

const APP_IN_PROGRESS: ApplicationOut = {
  ...APP_PENDING_1,
  id: "33333333-3333-4333-8333-333333333333",
  kb_name_suggested: "销售流程",
  status: "in_progress",
  admin_processed_by: "admin-uuid-9999",
  admin_processed_at: "2026-05-23T11:00:00Z",
};

const APP_DONE: ApplicationOut = {
  ...APP_PENDING_1,
  id: "44444444-4444-4444-8444-444444444444",
  kb_name_suggested: "客户案例",
  status: "done",
  admin_processed_by: "admin-uuid-9999",
  admin_processed_at: "2026-05-23T12:00:00Z",
  fastgpt_dataset_id: "66e2a7f8a9b0c1d2e3f45678",
  dify_app_id: "abc12def",
};

const SAMPLE_LIST: ApplicationOut[] = [
  APP_PENDING_1,
  APP_PENDING_2,
  APP_IN_PROGRESS,
  APP_DONE,
];

// ----- fetch mock helpers --------------------------------------------------
function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  clearAuth();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

function setAdminAuth() {
  setAuth("test-jwt-admin", {
    id: "admin-uuid-9999",
    name: "Admin",
    is_active: true,
    is_admin: true,
  });
}

function setNonAdminAuth() {
  setAuth("test-jwt-user", {
    id: "user-uuid-0001",
    name: "User",
    is_active: true,
    is_admin: false,
  });
}

function renderApp(initial: string = "/admin/personal-kb") {
  return render(
    <MemoryRouter initialEntries={[initial]}>
      <Routes>
        <Route path="/" element={<div data-testid="home-placeholder">home</div>} />
        <Route
          path="/login"
          element={<div data-testid="login-placeholder">login</div>}
        />
        <Route
          path="/admin/personal-kb"
          element={
            <RequireAuth requireAdmin>
              <AdminPersonalKbPage />
            </RequireAuth>
          }
        />
        <Route
          path="/admin/personal-kb/applications/:id"
          element={<div data-testid="detail-placeholder">detail</div>}
        />
      </Routes>
    </MemoryRouter>,
  );
}

// ----- tests ---------------------------------------------------------------
describe("<AdminPersonalKbPage> (TASK-PC3-B)", () => {
  it("unauthenticated → redirected to /login (RequireAuth guard)", async () => {
    fetchMock.mockResolvedValue(jsonResponse(200, []));
    renderApp();
    await waitFor(() =>
      expect(screen.getByTestId("login-placeholder")).toBeInTheDocument(),
    );
    expect(screen.queryByTestId("admin-personal-kb-page")).not.toBeInTheDocument();
  });

  it("non-admin user → redirected to / (requireAdmin guard)", async () => {
    setNonAdminAuth();
    fetchMock.mockResolvedValue(jsonResponse(200, []));
    renderApp();
    await waitFor(() =>
      expect(screen.getByTestId("home-placeholder")).toBeInTheDocument(),
    );
    expect(screen.queryByTestId("admin-personal-kb-page")).not.toBeInTheDocument();
  });

  it("admin user → list renders + PendingBadge counts pending+in_progress", async () => {
    setAdminAuth();
    fetchMock.mockResolvedValueOnce(jsonResponse(200, SAMPLE_LIST));
    renderApp();

    await waitFor(() =>
      expect(screen.getByTestId("admin-personal-kb-page")).toBeInTheDocument(),
    );

    // Default filter = pending → only 2 pending rows visible.
    expect(
      screen.getByTestId(`admin-personal-kb-row-${APP_PENDING_1.id}`),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId(`admin-personal-kb-row-${APP_PENDING_2.id}`),
    ).toBeInTheDocument();
    expect(
      screen.queryByTestId(`admin-personal-kb-row-${APP_IN_PROGRESS.id}`),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId(`admin-personal-kb-row-${APP_DONE.id}`),
    ).not.toBeInTheDocument();

    // PendingBadge: pending=2, in_progress=1 → total 3.
    const badge = screen.getByTestId("admin-pending-badge");
    expect(badge.textContent).toContain("当前待办 3 个");
    expect(badge.textContent).toContain("pending: 2");
    expect(badge.textContent).toContain("in_progress: 1");
  });

  it("Segmented switch to 'done' filters table client-side without re-fetch", async () => {
    setAdminAuth();
    fetchMock.mockResolvedValue(jsonResponse(200, SAMPLE_LIST));
    renderApp();

    await waitFor(() =>
      expect(screen.getByTestId("admin-personal-kb-page")).toBeInTheDocument(),
    );
    const initialFetchCount = fetchMock.mock.calls.length;

    const segmented = screen.getByTestId("admin-personal-kb-filter");
    // Antd Segmented renders each option as a clickable label.
    const doneLabel = within(segmented).getByText("已完成");
    fireEvent.click(doneLabel);

    await waitFor(() =>
      expect(
        screen.getByTestId(`admin-personal-kb-row-${APP_DONE.id}`),
      ).toBeInTheDocument(),
    );
    expect(
      screen.queryByTestId(`admin-personal-kb-row-${APP_PENDING_1.id}`),
    ).not.toBeInTheDocument();
    // No additional fetch — segmented filter is purely client-side.
    expect(fetchMock.mock.calls.length).toBe(initialFetchCount);
  });

  it("PendingBadge hides when pending+in_progress sum is zero", async () => {
    setAdminAuth();
    fetchMock.mockResolvedValueOnce(jsonResponse(200, [APP_DONE]));
    renderApp();
    await waitFor(() =>
      expect(screen.getByTestId("admin-personal-kb-page")).toBeInTheDocument(),
    );
    expect(screen.queryByTestId("admin-pending-badge")).not.toBeInTheDocument();
  });

  it("polling: fetches again after 30s", async () => {
    setAdminAuth();
    fetchMock.mockResolvedValue(jsonResponse(200, SAMPLE_LIST));
    vi.useFakeTimers({ shouldAdvanceTime: true });
    renderApp();

    await waitFor(() =>
      expect(screen.getByTestId("admin-personal-kb-page")).toBeInTheDocument(),
    );
    const after1 = fetchMock.mock.calls.length;
    expect(after1).toBeGreaterThanOrEqual(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(fetchMock.mock.calls.length).toBeGreaterThan(after1);
  });

  it("row click → navigates to /admin/personal-kb/applications/:id", async () => {
    setAdminAuth();
    fetchMock.mockResolvedValue(jsonResponse(200, SAMPLE_LIST));
    renderApp();

    await waitFor(() =>
      expect(screen.getByTestId("admin-personal-kb-page")).toBeInTheDocument(),
    );

    const row = screen.getByTestId(`admin-personal-kb-row-${APP_PENDING_1.id}`);
    fireEvent.click(row);

    await waitFor(() =>
      expect(screen.getByTestId("detail-placeholder")).toBeInTheDocument(),
    );
  });
});
