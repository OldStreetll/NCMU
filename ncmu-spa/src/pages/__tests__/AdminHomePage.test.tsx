// TASK-PE-04 — AdminHomePage tests.
//
// Mocks `@/lib/api`'s `api()` helper so the page never touches fetch.
// Each test resolves the api Promise with real values (守
// `feedback_tdd_mock_vs_real_api` SOP 10 — no "never resolves /
// never rejects" mocks). Field-level assertions (守
// `feedback_pre_existing_error_strict_validation`) — verify each of
// the 5 count cards renders its specific number, not just that the
// page renders.
//
// Auth setup via `setAuth()` (mirrors PE-01 AdminLayout.test pattern).

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
import { AdminHomePage } from "@/pages/AdminHomePage";
import type { AdminStats } from "@/hooks/useAdminStats";

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
    <MemoryRouter initialEntries={["/admin"]}>
      <AdminHomePage />
    </MemoryRouter>,
  );
}

function statsFixture(overrides: Partial<AdminStats> = {}): AdminStats {
  return {
    tag_count: 0,
    user_count: 5,
    app_count: 0,
    pending_personal_kb_count: 0,
    external_kb_config_count: 0,
    ...overrides,
  };
}

describe("AdminHomePage", () => {
  it("renders skeleton while stats are loading", async () => {
    // Pending promise (resolved after assertion to avoid feedback_tdd_mock_vs_real_api ban)
    let resolve!: (v: AdminStats) => void;
    mockApi.mockReturnValue(
      new Promise<AdminStats>((r) => {
        resolve = r;
      }),
    );
    const { container } = renderPage();
    expect(container.querySelector(".ant-skeleton")).not.toBeNull();
    await act(async () => {
      resolve(statsFixture());
    });
  });

  it("renders 5 count cards with field-level values", async () => {
    mockApi.mockResolvedValue(
      statsFixture({
        tag_count: 7,
        user_count: 42,
        app_count: 13,
        pending_personal_kb_count: 3,
        external_kb_config_count: 2,
      }),
    );
    renderPage();
    // Wait for one card to render then assert all 5.
    const tagCard = await screen.findByTestId("admin-stat-tags");
    expect(within(tagCard).getByText("标签管理")).toBeInTheDocument();
    expect(within(tagCard).getByText(/7\s*个标签/)).toBeInTheDocument();
    const userCard = screen.getByTestId("admin-stat-users");
    expect(within(userCard).getByText("用户管理")).toBeInTheDocument();
    expect(within(userCard).getByText(/42\s*个用户/)).toBeInTheDocument();
    const appCard = screen.getByTestId("admin-stat-apps");
    expect(within(appCard).getByText("App 管理")).toBeInTheDocument();
    expect(within(appCard).getByText(/13\s*个 App/)).toBeInTheDocument();
    const kbCard = screen.getByTestId("admin-stat-personal-kb");
    expect(within(kbCard).getByText("个人 KB")).toBeInTheDocument();
    expect(within(kbCard).getByText(/3\s*个待办/)).toBeInTheDocument();
    const extCard = screen.getByTestId("admin-stat-kb-configs");
    expect(within(extCard).getByText("外部 KB")).toBeInTheDocument();
    expect(within(extCard).getByText(/2\s*个配置/)).toBeInTheDocument();
  });

  it("calls api with the BASE-relative /admin/stats path exactly once", async () => {
    mockApi.mockResolvedValue(statsFixture());
    renderPage();
    await screen.findByTestId("admin-stat-tags");
    expect(mockApi).toHaveBeenCalledTimes(1);
    expect(mockApi).toHaveBeenCalledWith("/admin/stats");
  });

  it("highlights pending Personal KB card when count > 0", async () => {
    mockApi.mockResolvedValue(statsFixture({ pending_personal_kb_count: 4 }));
    renderPage();
    const kbCard = await screen.findByTestId("admin-stat-personal-kb");
    // The highlighted card carries an extra data attribute we can lock.
    expect(kbCard.getAttribute("data-highlight")).toBe("true");
  });

  it("does not highlight pending Personal KB card when count = 0", async () => {
    mockApi.mockResolvedValue(statsFixture({ pending_personal_kb_count: 0 }));
    renderPage();
    const kbCard = await screen.findByTestId("admin-stat-personal-kb");
    expect(kbCard.getAttribute("data-highlight")).toBe("false");
  });

  it("renders 2 DSL tool cards under 工具 heading", async () => {
    mockApi.mockResolvedValue(statsFixture());
    renderPage();
    await screen.findByTestId("admin-stat-tags");
    expect(screen.getByText("工具")).toBeInTheDocument();
    expect(screen.getByTestId("admin-tool-dsl-import")).toBeInTheDocument();
    expect(screen.getByTestId("admin-tool-dsl-export")).toBeInTheDocument();
    expect(
      within(screen.getByTestId("admin-tool-dsl-import")).getByText("DSL 导入"),
    ).toBeInTheDocument();
    expect(
      within(screen.getByTestId("admin-tool-dsl-export")).getByText("DSL 导出"),
    ).toBeInTheDocument();
  });

  it("wires each card to its admin route href", async () => {
    mockApi.mockResolvedValue(statsFixture());
    renderPage();
    await screen.findByTestId("admin-stat-tags");
    expect(
      screen.getByTestId("admin-stat-tags").querySelector("a")?.getAttribute("href"),
    ).toBe("/admin/tags");
    expect(
      screen.getByTestId("admin-stat-users").querySelector("a")?.getAttribute("href"),
    ).toBe("/admin/users");
    expect(
      screen.getByTestId("admin-stat-apps").querySelector("a")?.getAttribute("href"),
    ).toBe("/admin/apps");
    expect(
      screen
        .getByTestId("admin-stat-personal-kb")
        .querySelector("a")
        ?.getAttribute("href"),
    ).toBe("/admin/personal-kb");
    expect(
      screen
        .getByTestId("admin-stat-kb-configs")
        .querySelector("a")
        ?.getAttribute("href"),
    ).toBe("/admin/kb-configs");
    expect(
      screen
        .getByTestId("admin-tool-dsl-import")
        .querySelector("a")
        ?.getAttribute("href"),
    ).toBe("/admin/apps/dsl-import");
    expect(
      screen
        .getByTestId("admin-tool-dsl-export")
        .querySelector("a")
        ?.getAttribute("href"),
    ).toBe("/admin/apps/dsl-export");
  });

  it("surfaces an error alert when the stats request fails", async () => {
    mockApi.mockRejectedValue(new ApiError(500, undefined, "stats unavailable"));
    renderPage();
    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });
    expect(screen.getByRole("alert")).toHaveTextContent("stats unavailable");
  });
});
