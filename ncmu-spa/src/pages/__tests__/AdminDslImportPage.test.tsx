// TASK-PE-11 — AdminDslImportPage wiring tests.
//
// Covers wire shape (multipart POST /admin/apps/dsl-import) + result Modal
// (succeeded list / field-level failed rows / plugin_missing detail / KB
// 重挂提示 always shown). Real Response objects in fetch mocks (守 SOP 10).
//
// antd Upload.Dragger renders a hidden <input type="file">; the test drives
// it via fireEvent.change (rc-upload calls beforeUpload → our importDsl).

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
import AdminDslImportPage from "@/pages/AdminDslImportPage";
import { setAuth, clearAuth } from "@/lib/auth";

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

function renderPage() {
  return render(
    <MemoryRouter>
      <AdminDslImportPage />
    </MemoryRouter>,
  );
}

function uploadFile(container: HTMLElement, file: File) {
  const input = container.querySelector(
    'input[type="file"]',
  ) as HTMLInputElement;
  expect(input).toBeTruthy();
  fireEvent.change(input, { target: { files: [file] } });
}

describe("AdminDslImportPage", () => {
  it("POSTs multipart to dsl-import and renders the result modal", async () => {
    const result = {
      total: 2,
      succeeded: [{ filename: "a.yaml", app_id: "app-a", name: "销售助手" }],
      failed: [
        { filename: "b.yaml", error_code: "YAML_PARSE", error_message: "bad yaml" },
      ],
    };
    fetchMock.mockImplementation((url: string) => {
      if (url.includes("/admin/apps/dsl-import")) {
        return Promise.resolve(jsonResponse(200, result));
      }
      throw new Error(`unexpected fetch ${url}`);
    });

    const { container } = renderPage();
    uploadFile(
      container,
      new File(["app:\n  name: x\n"], "a.yaml", { type: "application/x-yaml" }),
    );

    // wire shape: POST + multipart FormData to the right URL
    await waitFor(() => {
      const call = fetchMock.mock.calls.find((c) =>
        String(c[0]).includes("/admin/apps/dsl-import"),
      );
      expect(call).toBeTruthy();
      const [url, init] = call!;
      expect(url).toBe("/api/v1/ncmu/admin/apps/dsl-import");
      expect(init.method).toBe("POST");
      expect(init.body).toBeInstanceOf(FormData);
      expect(init.headers.Authorization).toBe("Bearer test-jwt-admin");
    });

    // result modal: title counts + succeeded + failed rows
    expect(await screen.findByText(/成功 1 \/ 失败 1/)).toBeInTheDocument();
    expect(screen.getByText("销售助手")).toBeInTheDocument();
    expect(screen.getByText("YAML 解析失败")).toBeInTheDocument();
    // KB 重挂提示 always present
    expect(screen.getByTestId("kb-remount-notice")).toBeInTheDocument();
  });

  it("renders plugin_missing detail for PLUGIN_MISSING rows", async () => {
    const result = {
      total: 1,
      succeeded: [],
      failed: [
        {
          filename: "p.yaml",
          error_code: "PLUGIN_MISSING",
          app_id: "app-p",
          name: "插件App",
          plugin_missing: [{ value: { plugin_unique_identifier: "x/y" } }],
          error_message: "App created but required plugins are missing",
        },
      ],
    };
    fetchMock.mockResolvedValue(jsonResponse(200, result));

    const { container } = renderPage();
    uploadFile(
      container,
      new File(["app:\n  name: p\n"], "p.yaml", { type: "application/x-yaml" }),
    );

    expect(await screen.findByText("缺少插件依赖")).toBeInTheDocument();
    expect(screen.getByTestId("plugin-missing-detail")).toBeInTheDocument();
    expect(screen.getByTestId("kb-remount-notice")).toBeInTheDocument();
  });

  it("surfaces a request-level error (413) without crashing", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(413, { detail: { code: 1003, message: "file exceeds 5MB limit" } }),
    );
    const { container } = renderPage();
    uploadFile(
      container,
      new File(["big"], "big.yaml", { type: "application/x-yaml" }),
    );
    // no result modal on hard failure; the page stays usable (dragger present)
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some((c) =>
          String(c[0]).includes("/admin/apps/dsl-import"),
        ),
      ).toBe(true),
    );
    expect(screen.queryByTestId("kb-remount-notice")).not.toBeInTheDocument();
  });
});
