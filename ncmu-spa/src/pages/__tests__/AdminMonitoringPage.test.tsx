// TASK-MON-2 — AdminMonitoringPage tests.
//
// Mocks `@/lib/api`'s `api()` helper so the page never touches fetch. Each
// test resolves the api Promise with real contract values (守
// `feedback_tdd_mock_vs_real_api` SOP 10 — no "never resolves / never
// rejects" mocks; the loading test resolves AFTER the assertion).
//
// Field-level assertions (守 `feedback_pre_existing_error_strict_validation`)
// over the frozen MON-1 contract: each metric renders its specific number,
// the 4 config-readiness health lights flip green/grey on the boolean, and
// the two nullable fields (avg_kb_processing_seconds / last_dify_app_sync_at)
// degrade to "—". A dedicated test asserts NO secret value leaks into the
// readiness block (only labels + boolean state).
//
// Auth setup via `setAuth()` (mirrors AdminHomePage.test pattern).

import {
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("@/lib/api", async (importActual) => {
  const actual = await importActual<typeof import("@/lib/api")>();
  return { ...actual, api: vi.fn() };
});

import { api, ApiError } from "@/lib/api";
import { setAuth, type AuthUser } from "@/lib/auth";
import { AdminMonitoringPage } from "@/pages/AdminMonitoringPage";
import type { MonitoringOut } from "@/hooks/useAdminMonitoring";

const mockApi = vi.mocked(api);

const sampleAdmin: AuthUser = {
  id: "a0000001-0000-4000-8000-000000000001",
  name: "张三",
  is_active: true,
  is_admin: true,
};

// jsdom lacks matchMedia which antd 5 Grid useBreakpoint pulls.
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
  sessionStorage.clear();
  setAuth("jwt-token-admin", sampleAdmin);
  mockApi.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/admin/monitoring"]}>
      <AdminMonitoringPage />
    </MemoryRouter>,
  );
}

function monitoringFixture(
  overrides: Partial<MonitoringOut> = {},
): MonitoringOut {
  return {
    business: {
      user_total: 120,
      user_active: 88,
      user_with_dingtalk: 64,
      new_users_7d: 9,
      kb_application_status: {
        pending: 5,
        in_progress: 3,
        done: 12,
        rejected: 1,
        cancelled: 2,
      },
      kb_pending_backlog: 5,
      new_kb_applications_7d: 7,
      avg_kb_processing_seconds: 3600,
      app_total: 13,
      app_active: 10,
      last_dify_app_sync_at: "2026-06-04T08:30:00Z",
      tag_bindings: [
        { tag_id: "t0000001-0000-4000-8000-000000000001", name: "研发部", app_count: 4, user_count: 20 },
        { tag_id: "t0000002-0000-4000-8000-000000000002", name: "市场部", app_count: 2, user_count: 11 },
      ],
    },
    dingtalk: {
      config_readiness: {
        app_key: true,
        app_secret: true,
        corp_id: true,
        login_redirect_uri: false,
      },
      department_tag_mapping_count: 6,
      synced_account_count: 64,
      tags_routing_enabled: true,
      routing_affected_user_count: 50,
      routing_affected_app_count: 8,
    },
    generated_at: "2026-06-04T09:00:00Z",
    ...overrides,
  };
}

describe("AdminMonitoringPage", () => {
  it("renders skeleton while the snapshot is loading", async () => {
    // Pending promise (resolved after the assertion to avoid the
    // feedback_tdd_mock_vs_real_api "never resolves" ban).
    let resolve!: (v: MonitoringOut) => void;
    mockApi.mockReturnValue(
      new Promise<MonitoringOut>((r) => {
        resolve = r;
      }),
    );
    const { container } = renderPage();
    expect(container.querySelector(".ant-skeleton")).not.toBeNull();
    await act(async () => {
      resolve(monitoringFixture());
    });
  });

  it("calls api with the BASE-relative /admin/monitoring path exactly once", async () => {
    mockApi.mockResolvedValue(monitoringFixture());
    renderPage();
    await screen.findByTestId("mon-user-total");
    expect(mockApi).toHaveBeenCalledTimes(1);
    expect(mockApi).toHaveBeenCalledWith("/admin/monitoring");
  });

  it("renders both blocks (业务运营 / 钉钉集成)", async () => {
    mockApi.mockResolvedValue(monitoringFixture());
    renderPage();
    await screen.findByTestId("mon-block-business");
    expect(screen.getByText("业务运营")).toBeInTheDocument();
    expect(screen.getByText("钉钉集成")).toBeInTheDocument();
  });

  it("renders business metrics with field-level values", async () => {
    mockApi.mockResolvedValue(monitoringFixture());
    renderPage();
    const userTotal = await screen.findByTestId("mon-user-total");
    expect(within(userTotal).getByText("用户总数")).toBeInTheDocument();
    expect(within(userTotal).getByText("120")).toBeInTheDocument();
    expect(within(screen.getByTestId("mon-user-active")).getByText("88")).toBeInTheDocument();
    expect(within(screen.getByTestId("mon-user-dingtalk")).getByText("64")).toBeInTheDocument();
    expect(within(screen.getByTestId("mon-new-users-7d")).getByText("9")).toBeInTheDocument();
    expect(within(screen.getByTestId("mon-app-total")).getByText("13")).toBeInTheDocument();
    expect(within(screen.getByTestId("mon-app-active")).getByText("10")).toBeInTheDocument();
    expect(within(screen.getByTestId("mon-kb-backlog")).getByText("5")).toBeInTheDocument();
    expect(within(screen.getByTestId("mon-new-kb-7d")).getByText("7")).toBeInTheDocument();
  });

  it("renders the 5 KB-status distribution counts (including 0-friendly fixed keys)", async () => {
    mockApi.mockResolvedValue(monitoringFixture());
    renderPage();
    expect(
      within(await screen.findByTestId("mon-kb-status-pending")).getByText("5"),
    ).toBeInTheDocument();
    expect(within(screen.getByTestId("mon-kb-status-in_progress")).getByText("3")).toBeInTheDocument();
    expect(within(screen.getByTestId("mon-kb-status-done")).getByText("12")).toBeInTheDocument();
    expect(within(screen.getByTestId("mon-kb-status-rejected")).getByText("1")).toBeInTheDocument();
    expect(within(screen.getByTestId("mon-kb-status-cancelled")).getByText("2")).toBeInTheDocument();
  });

  it("renders all 5 KB-status keys even when every count is 0", async () => {
    mockApi.mockResolvedValue(
      monitoringFixture({
        business: {
          ...monitoringFixture().business,
          kb_application_status: {
            pending: 0,
            in_progress: 0,
            done: 0,
            rejected: 0,
            cancelled: 0,
          },
        },
      }),
    );
    renderPage();
    for (const key of ["pending", "in_progress", "done", "rejected", "cancelled"]) {
      const row = await screen.findByTestId(`mon-kb-status-${key}`);
      expect(within(row).getByText("0")).toBeInTheDocument();
    }
  });

  it("renders config-readiness health lights green when true / grey when false", async () => {
    mockApi.mockResolvedValue(monitoringFixture());
    renderPage();
    const appKey = await screen.findByTestId("mon-readiness-app_key");
    expect(appKey.getAttribute("data-ok")).toBe("true");
    expect(screen.getByTestId("mon-readiness-app_secret").getAttribute("data-ok")).toBe("true");
    expect(screen.getByTestId("mon-readiness-corp_id").getAttribute("data-ok")).toBe("true");
    // login_redirect_uri is false in the fixture → grey light.
    expect(
      screen.getByTestId("mon-readiness-login_redirect_uri").getAttribute("data-ok"),
    ).toBe("false");
    // antd Badge status class reflects the colour token.
    expect(appKey.querySelector(".ant-badge-status-success")).not.toBeNull();
    expect(
      screen
        .getByTestId("mon-readiness-login_redirect_uri")
        .querySelector(".ant-badge-status-default"),
    ).not.toBeNull();
  });

  it("never leaks a secret value in the readiness block (labels + boolean only)", async () => {
    mockApi.mockResolvedValue(monitoringFixture());
    renderPage();
    const block = await screen.findByTestId("mon-config-readiness");
    // Only the 4 human labels render; the contract carries no secret value, and
    // the page must not invent one. Assert the block text is exactly the labels.
    expect(block).toHaveTextContent("App Key");
    expect(block).toHaveTextContent("App Secret");
    expect(block).toHaveTextContent("Corp ID");
    expect(block).toHaveTextContent("登录回调地址");
    // No "true"/"false" raw boolean string and no secret-ish token rendered.
    expect(block.textContent ?? "").not.toMatch(/true|false|secret=|key=|[A-Za-z0-9]{20,}/);
  });

  it("renders dingtalk integration metrics with field-level values", async () => {
    mockApi.mockResolvedValue(monitoringFixture());
    renderPage();
    expect(
      within(await screen.findByTestId("mon-dept-tag-mapping")).getByText("6"),
    ).toBeInTheDocument();
    expect(within(screen.getByTestId("mon-synced-accounts")).getByText("64")).toBeInTheDocument();
    expect(within(screen.getByTestId("mon-routing-users")).getByText("50")).toBeInTheDocument();
    expect(within(screen.getByTestId("mon-routing-apps")).getByText("8")).toBeInTheDocument();
  });

  it("reflects tags_routing_enabled state", async () => {
    mockApi.mockResolvedValue(monitoringFixture());
    renderPage();
    const enabled = await screen.findByTestId("mon-routing-enabled");
    expect(enabled.getAttribute("data-enabled")).toBe("true");
    expect(within(enabled).getByText("已启用")).toBeInTheDocument();
  });

  it("shows 未启用 when tags_routing_enabled is false", async () => {
    mockApi.mockResolvedValue(
      monitoringFixture({
        dingtalk: { ...monitoringFixture().dingtalk, tags_routing_enabled: false },
      }),
    );
    renderPage();
    const enabled = await screen.findByTestId("mon-routing-enabled");
    expect(enabled.getAttribute("data-enabled")).toBe("false");
    expect(within(enabled).getByText("未启用")).toBeInTheDocument();
  });

  it("renders the tag bindings table rows", async () => {
    mockApi.mockResolvedValue(monitoringFixture());
    renderPage();
    const block = await screen.findByTestId("mon-tag-bindings");
    expect(within(block).getByText("研发部")).toBeInTheDocument();
    expect(within(block).getByText("市场部")).toBeInTheDocument();
  });

  it("shows an empty state when there are no tag bindings", async () => {
    mockApi.mockResolvedValue(
      monitoringFixture({
        business: { ...monitoringFixture().business, tag_bindings: [] },
      }),
    );
    renderPage();
    const block = await screen.findByTestId("mon-tag-bindings");
    expect(within(block).getByText("暂无标签绑定")).toBeInTheDocument();
  });

  it("degrades avg_kb_processing_seconds to — when null", async () => {
    mockApi.mockResolvedValue(
      monitoringFixture({
        business: { ...monitoringFixture().business, avg_kb_processing_seconds: null },
      }),
    );
    renderPage();
    const avg = await screen.findByTestId("mon-avg-processing");
    expect(avg).toHaveTextContent("—");
  });

  it("renders avg_kb_processing_seconds as seconds when present", async () => {
    mockApi.mockResolvedValue(monitoringFixture());
    renderPage();
    const avg = await screen.findByTestId("mon-avg-processing");
    expect(avg).toHaveTextContent("3600 秒");
  });

  it("degrades last_dify_app_sync_at to — when null", async () => {
    mockApi.mockResolvedValue(
      monitoringFixture({
        business: { ...monitoringFixture().business, last_dify_app_sync_at: null },
      }),
    );
    renderPage();
    const sync = await screen.findByTestId("mon-last-sync");
    expect(sync).toHaveTextContent("—");
  });

  it("renders last_dify_app_sync_at as a friendly local datetime (not raw ISO)", async () => {
    const iso = "2026-06-04T08:30:00Z";
    mockApi.mockResolvedValue(monitoringFixture());
    renderPage();
    const sync = await screen.findByTestId("mon-last-sync");
    // Localized via new Date(iso).toLocaleString("zh-CN") — compute the same
    // way so the assertion stays deterministic across host timezones.
    expect(sync).toHaveTextContent(new Date(iso).toLocaleString("zh-CN"));
    // And it must no longer be the raw ISO string.
    expect(sync).not.toHaveTextContent(iso);
  });

  it("renders generated_at as a friendly local datetime (not raw ISO)", async () => {
    const iso = "2026-06-04T09:00:00Z";
    mockApi.mockResolvedValue(monitoringFixture());
    renderPage();
    const gen = await screen.findByTestId("mon-generated-at");
    expect(gen).toHaveTextContent(new Date(iso).toLocaleString("zh-CN"));
    expect(gen).not.toHaveTextContent(iso);
  });

  it("surfaces an error alert when the request fails", async () => {
    mockApi.mockRejectedValue(new ApiError(500, undefined, "monitoring unavailable"));
    renderPage();
    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });
    expect(screen.getByRole("alert")).toHaveTextContent("monitoring unavailable");
  });
});
