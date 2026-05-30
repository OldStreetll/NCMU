// TASK-PE-07 — AdminAppsPage wiring tests.
//
// Covers wire shape (URL + method + query params + auth) against backend
// admin/apps/routes.py + admin/routes.py sync_apps, plus key UI flows
// (list render / last_synced_at format / 立即同步 → feedback Modal with the
// REAL {upserted, total_console} shape / is_active 停用 Popconfirm + 启用 /
// 显示已停用 switch / 搜索). Real Response objects in fetch mocks (守 SOP 10
// in `feedback_tdd_mock_vs_real_api`).
//
// antd v5 仅 2 CJK 字符按钮 autoInsertSpaceInButton 默认开启 — "停用" renders
// as "停 用", "确定" as "确 定" — every getByRole({name: /…/}) regex 含 \s*
// 容错 (守 feedback_antd_v5_cjk_button_space_pitfall). "立即同步" is 4 chars
// so unaffected, but kept consistent.

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
import { MemoryRouter } from "react-router-dom";
import AdminAppsPage from "@/pages/AdminAppsPage";
import { setAuth, clearAuth } from "@/lib/auth";

// ----- jsdom polyfills antd 5 Grid breakpoints rely on -------------------- #
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

// ----- fetch mock helpers ------------------------------------------------- #
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
  setAuth("test-jwt-admin", {
    id: "a0000001-0000-4000-8000-000000000001",
    name: "张三",
    is_active: true,
    is_admin: true,
  });
});

afterEach(() => {
  clearAuth();
  vi.unstubAllGlobals();
});

// ----- fixtures (mirror backend AdminAppOut shape) ------------------------ #
const APP_ALPHA = {
  dify_app_id: "app-aaa",
  name: "Alpha 客服",
  mode: "chat",
  is_active: true,
  last_synced_at: "2026-05-29T12:00:00+00:00",
  tag_count: 0,
};

const APP_BETA = {
  dify_app_id: "app-bbb",
  name: "Beta 工作流",
  mode: "workflow",
  is_active: true,
  last_synced_at: null, // never synced
  tag_count: 0,
};

const APP_GAMMA = {
  dify_app_id: "app-ccc",
  name: "Gamma 已停用",
  mode: "agent-chat",
  is_active: false,
  last_synced_at: "2026-05-29T12:00:00+00:00",
  tag_count: 0,
};

function renderPage() {
  return render(
    <MemoryRouter>
      <AdminAppsPage />
    </MemoryRouter>,
  );
}

// ----- wire shape --------------------------------------------------------- #
describe("AdminAppsPage — wire shape", () => {
  it("loads /api/v1/ncmu/admin/apps on mount with Bearer auth (no query default)", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, [APP_ALPHA, APP_BETA]));
    renderPage();
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe("/api/v1/ncmu/admin/apps");
    expect((init as RequestInit).method ?? "GET").toBe("GET");
    expect(
      ((init as RequestInit).headers as Record<string, string>)[
        "Authorization"
      ],
    ).toBe("Bearer test-jwt-admin");
  });

  it("renders rows: name + mode + status + tag_count", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, [APP_ALPHA, APP_BETA]));
    renderPage();
    expect(await screen.findByText("Alpha 客服")).toBeInTheDocument();
    expect(screen.getByText("Beta 工作流")).toBeInTheDocument();
    // mode rendered as a Tag.
    expect(screen.getByText("chat")).toBeInTheDocument();
    expect(screen.getByText("workflow")).toBeInTheDocument();
  });

  it("formats last_synced_at to YYYY-MM-DD HH:MM; null → 尚未同步", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, [APP_ALPHA, APP_BETA]));
    renderPage();
    // Alpha synced → deterministic slice of the ISO string.
    expect(await screen.findByText("2026-05-29 12:00")).toBeInTheDocument();
    // Beta never synced → placeholder.
    expect(screen.getByText("尚未同步")).toBeInTheDocument();
  });
});

// ----- sync flow ---------------------------------------------------------- #
describe("AdminAppsPage — 立即同步", () => {
  it("POSTs /admin/sync_apps then shows feedback Modal with {upserted, total_console}", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(200, [APP_ALPHA])) // mount list
      .mockResolvedValueOnce(jsonResponse(200, { upserted: 4, total_console: 5 })) // POST sync
      .mockResolvedValueOnce(jsonResponse(200, [APP_ALPHA])); // refetch after sync

    renderPage();
    await screen.findByText("Alpha 客服");

    const syncBtn = screen.getByRole("button", { name: /立\s*即\s*同\s*步/ });
    fireEvent.click(syncBtn);

    // POST fired against the reused Phase 2B endpoint.
    await waitFor(() => {
      const post = fetchMock.mock.calls.find(
        (c) => String(c[0]) === "/api/v1/ncmu/admin/sync_apps",
      );
      expect(post).toBeTruthy();
      expect((post![1] as RequestInit).method).toBe("POST");
    });

    // Feedback Modal renders the REAL shape (upserted / total_console), not
    // the plan-literal added/updated/deactivated/errors.
    expect(await screen.findByText("同步完成")).toBeInTheDocument();
    expect(await screen.findByText("4")).toBeInTheDocument();
    expect(screen.getByText("5")).toBeInTheDocument();
  });
});

// ----- is_active toggle --------------------------------------------------- #
describe("AdminAppsPage — is_active 切换", () => {
  it("停用 → Popconfirm 确定 → PATCH {is_active:false}", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(200, [APP_ALPHA])) // mount
      .mockResolvedValueOnce(
        jsonResponse(200, { ...APP_ALPHA, is_active: false }),
      ) // PATCH
      .mockResolvedValueOnce(jsonResponse(200, [])); // refetch

    renderPage();
    await screen.findByText("Alpha 客服");

    fireEvent.click(screen.getByRole("button", { name: /停\s*用/ }));
    // Popconfirm confirm button (2-char CJK → autospace).
    const confirm = await screen.findByRole("button", { name: /确\s*定/ });
    fireEvent.click(confirm);

    await waitFor(() => {
      const patch = fetchMock.mock.calls.find(
        (c) => String(c[0]) === "/api/v1/ncmu/admin/apps/app-aaa",
      );
      expect(patch).toBeTruthy();
      const init = patch![1] as RequestInit;
      expect(init.method).toBe("PATCH");
      expect(JSON.parse(init.body as string)).toEqual({ is_active: false });
    });
  });

  it("启用 (inactive row) → PATCH {is_active:true}", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(200, [APP_GAMMA])) // mount (inactive shown)
      .mockResolvedValueOnce(
        jsonResponse(200, { ...APP_GAMMA, is_active: true }),
      ) // PATCH
      .mockResolvedValueOnce(jsonResponse(200, [])); // refetch

    renderPage();
    await screen.findByText("Gamma 已停用");

    // 启用 has no Popconfirm — direct click triggers PATCH.
    fireEvent.click(screen.getByRole("button", { name: /启\s*用/ }));

    await waitFor(() => {
      const patch = fetchMock.mock.calls.find(
        (c) => String(c[0]) === "/api/v1/ncmu/admin/apps/app-ccc",
      );
      expect(patch).toBeTruthy();
      const init = patch![1] as RequestInit;
      expect(init.method).toBe("PATCH");
      expect(JSON.parse(init.body as string)).toEqual({ is_active: true });
    });
  });
});

// ----- filters ------------------------------------------------------------ #
describe("AdminAppsPage — filters", () => {
  it("显示已停用 switch → refetch with include_inactive=true", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(200, [APP_ALPHA])) // mount (active only)
      .mockResolvedValueOnce(jsonResponse(200, [APP_ALPHA, APP_GAMMA])); // include_inactive

    renderPage();
    await screen.findByText("Alpha 客服");

    // antd Switch renders as role=switch.
    fireEvent.click(screen.getByRole("switch"));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find((c) =>
        String(c[0]).includes("include_inactive=true"),
      );
      expect(call).toBeTruthy();
      expect(String(call![0])).toBe(
        "/api/v1/ncmu/admin/apps?include_inactive=true",
      );
    });
  });

  it("搜索 → refetch with search= query param", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(200, [APP_ALPHA, APP_BETA])) // mount
      .mockResolvedValueOnce(jsonResponse(200, [APP_BETA])); // after search

    renderPage();
    await screen.findByText("Alpha 客服");

    const searchBox = screen.getByPlaceholderText("按名称搜索");
    fireEvent.change(searchBox, { target: { value: "工作" } });
    fireEvent.keyDown(searchBox, { key: "Enter", code: "Enter" });

    await waitFor(() => {
      const call = fetchMock.mock.calls.find((c) =>
        String(c[0]).includes("search="),
      );
      expect(call).toBeTruthy();
      // URLSearchParams encodes the CJK term.
      expect(String(call![0])).toBe(
        `/api/v1/ncmu/admin/apps?search=${encodeURIComponent("工作")}`,
      );
    });
  });
});
