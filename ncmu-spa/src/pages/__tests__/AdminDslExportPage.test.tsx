// TASK-PE-10 — AdminDslExportPage wiring tests.
//
// Covers wire shape (candidate GET + export POST URL/method/body field
// names) against backend admin/dsl/routes.py + key UI flows (list render /
// default-all-selected / include_secret red warning toggle / ZIP download
// blob trigger). Real Response objects in fetch mocks (守 SOP 10 in
// feedback_tdd_mock_vs_real_api).
//
// antd v5 autoInsertSpaceInButton — 2-CJK-char buttons render with a space
// ("打 包" 不会发生在本页：按钮文案 >2 字含括号；getByRole name 用 /…/ 子串).

import {
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import {
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import AdminDslExportPage from "@/pages/AdminDslExportPage";
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

// ----- fetch + download stubs --------------------------------------------- #
function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function zipResponse(): Response {
  return new Response(new Blob(["PK-zip-bytes"]), {
    status: 200,
    headers: {
      "Content-Type": "application/zip",
      "Content-Disposition":
        'attachment; filename="ncmu-dify-apps-export-20260530-1530.zip"',
    },
  });
}

const CANDIDATES = [
  { dify_app_id: "app-a", name: "销售助手", mode: "chat" },
  { dify_app_id: "app-b", name: "HR Bot", mode: "agent-chat" },
];

const fetchMock = vi.fn();
let clickSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  // jsdom implements neither — stub so downloadDslZip's blob path is inert.
  URL.createObjectURL = vi.fn(() => "blob:mock-url");
  URL.revokeObjectURL = vi.fn();
  clickSpy = vi
    .spyOn(HTMLAnchorElement.prototype, "click")
    .mockImplementation(() => {});
  setAuth("test-jwt-admin", {
    id: "a0000001-0000-4000-8000-000000000001",
    name: "张三",
    is_active: true,
    is_admin: true,
  });
});

afterEach(() => {
  clearAuth();
  clickSpy.mockRestore();
  vi.unstubAllGlobals();
});

function renderPage() {
  return render(
    <MemoryRouter>
      <AdminDslExportPage />
    </MemoryRouter>,
  );
}

describe("AdminDslExportPage", () => {
  it("loads candidates and defaults to all selected", async () => {
    fetchMock.mockImplementation((url: string) => {
      if (url.includes("/admin/apps/dsl-candidates")) {
        return Promise.resolve(jsonResponse(200, CANDIDATES));
      }
      throw new Error(`unexpected fetch ${url}`);
    });

    renderPage();

    expect(await screen.findByText("销售助手")).toBeInTheDocument();
    expect(screen.getByText("HR Bot")).toBeInTheDocument();
    // default全选 → button label carries the count (2).
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /打.*包下载 ZIP（2）/ }),
      ).toBeInTheDocument();
    });
  });

  it("toggles the include_secret red warning", async () => {
    fetchMock.mockResolvedValue(jsonResponse(200, CANDIDATES));
    renderPage();
    await screen.findByText("销售助手");

    expect(
      screen.queryByTestId("include-secret-warning"),
    ).not.toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("checkbox", { name: /包含密钥/ }),
    );
    expect(
      await screen.findByTestId("include-secret-warning"),
    ).toBeInTheDocument();
  });

  it("POSTs the selected app_ids + include_secret=false and triggers download", async () => {
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url.includes("/admin/apps/dsl-candidates")) {
        return Promise.resolve(jsonResponse(200, CANDIDATES));
      }
      if (url.includes("/admin/apps/dsl-export")) {
        return Promise.resolve(zipResponse());
      }
      throw new Error(`unexpected fetch ${url} ${init?.method}`);
    });

    renderPage();
    await screen.findByText("销售助手");
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: /打.*包下载 ZIP（2）/ }),
      ).toBeEnabled(),
    );

    fireEvent.click(
      screen.getByRole("button", { name: /打.*包下载 ZIP/ }),
    );

    await waitFor(() => {
      const exportCall = fetchMock.mock.calls.find((c) =>
        String(c[0]).includes("/admin/apps/dsl-export"),
      );
      expect(exportCall).toBeTruthy();
      const [url, init] = exportCall!;
      expect(url).toBe("/api/v1/ncmu/admin/apps/dsl-export");
      expect(init.method).toBe("POST");
      expect(init.headers.Authorization).toBe("Bearer test-jwt-admin");
      expect(JSON.parse(init.body)).toEqual({
        app_ids: ["app-a", "app-b"],
        include_secret: false,
      });
    });
    // download actually fired (anchor click + objectURL lifecycle)
    await waitFor(() => expect(clickSpy).toHaveBeenCalled());
    expect(URL.createObjectURL).toHaveBeenCalled();
    expect(URL.revokeObjectURL).toHaveBeenCalled();
  });

  it("sends include_secret=true when the box is checked", async () => {
    fetchMock.mockImplementation((url: string) => {
      if (url.includes("/admin/apps/dsl-candidates")) {
        return Promise.resolve(jsonResponse(200, CANDIDATES));
      }
      if (url.includes("/admin/apps/dsl-export")) {
        return Promise.resolve(zipResponse());
      }
      throw new Error(`unexpected fetch ${url}`);
    });

    renderPage();
    await screen.findByText("销售助手");
    fireEvent.click(screen.getByRole("checkbox", { name: /包含密钥/ }));
    fireEvent.click(
      screen.getByRole("button", { name: /打.*包下载 ZIP/ }),
    );

    await waitFor(() => {
      const exportCall = fetchMock.mock.calls.find((c) =>
        String(c[0]).includes("/admin/apps/dsl-export"),
      );
      expect(exportCall).toBeTruthy();
      expect(JSON.parse(exportCall![1].body).include_secret).toBe(true);
    });
  });
});
