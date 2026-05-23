// TASK-PC3-C (Phase 2C) — AppKbContentPanel component tests.
//
// Coverage map vs plan §4.8 AC:
//   AC#1   collapse default closed + expand triggers fetch
//   AC#2   file list rendering + size humanisation + 0-file Empty
//   AC#3   download → window.open with `${API_BASE}/kbs/...` URL
//   AC#4   five error categories → message.error / .warning literal text
//   AC#5   30 s dedup (sequential expand-collapse-expand fires fetch once)
//   AC#7   vitest scenarios
//   AC#8   testid 字面
//   AC#9   mock fetch payload byte-exact with backend FastGPTReadOnlyClient FileMeta
//          (file_id / original_filename / size_bytes / uploaded_at / content_type)
//
// 守 feedback_pre_existing_error_strict_validation — all five toast
// assertions use `toHaveBeenCalledWith(<literal>)`, NOT toMatchObject /
// stringContaining; the literal Chinese text is the contract.
import {
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
  type MockInstance,
} from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { message } from "antd";
import AppKbContentPanel, { humanSize } from "@/components/AppKbContentPanel";
import {
  KB_ERROR_TEXT,
  _resetKbFilesCacheForTests,
} from "@/components/AppKbContentPanel/hooks/useKbFiles";
import { setAuth } from "@/lib/auth";

// Match backend ncmu_backend/fastgpt_readonly/client.py:FileMeta — byte-exact
// field names (守 feedback_tdd_mock_vs_real_api SOP 11 跨 component 边界).
const sampleFiles = [
  {
    file_id: "f-policy",
    original_filename: "员工手册.pdf",
    size_bytes: 524_288, // 512 KB
    uploaded_at: "2026-05-20T09:00:00Z",
    content_type: "application/pdf",
  },
  {
    file_id: "f-onboarding",
    original_filename: "入职流程.docx",
    size_bytes: 2_097_152, // 2 MB
    uploaded_at: "2026-05-21T10:00:00Z",
    content_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
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

function errorResponse(status: number, detail?: string): Response {
  const body = detail ? { detail: { code: status, message: detail } } : {};
  return {
    ok: false,
    status,
    json: async () => body,
    statusText: "",
  } as unknown as Response;
}

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

// antd 5 message.error / .warning share the same overloaded `MessageMethod`
// signature; aliasing once keeps both spy declarations narrow.
type MessageMethod = typeof message.error;
let errorSpy: MockInstance<MessageMethod>;
let warningSpy: MockInstance<MessageMethod>;

beforeEach(() => {
  _resetKbFilesCacheForTests();
  setAuth("test-jwt", {
    id: "a0000001-0000-4000-8000-000000000001",
    name: "tester",
    is_active: true,
    is_admin: false,
  });
  // antd 5 message singleton — spy + suppress real toast DOM noise. The
  // `() => never` returner is assignable to MessageMethod by parameter
  // contravariance (callers can pass extra args; never widens to MessageType).
  errorSpy = vi.spyOn(message, "error").mockImplementation(
    () => ({}) as never,
  );
  warningSpy = vi.spyOn(message, "warning").mockImplementation(
    () => ({}) as never,
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// AC#2 size humanisation — direct unit checks (cheap, no DOM mount).
describe("humanSize() — plan §4.8 AC#2 字面", () => {
  it("0 bytes → '0.0 KB'", () => {
    expect(humanSize(0)).toBe("0.0 KB");
  });
  it("under 1 MiB → KB with one decimal", () => {
    expect(humanSize(512)).toBe("0.5 KB");
    expect(humanSize(1024)).toBe("1.0 KB");
    expect(humanSize(524_288)).toBe("512.0 KB");
  });
  it("1 MiB exactly → '1.0 MB'", () => {
    expect(humanSize(1024 * 1024)).toBe("1.0 MB");
  });
  it("≥ 1 MiB → MB with one decimal", () => {
    expect(humanSize(2_097_152)).toBe("2.0 MB");
    expect(humanSize(5 * 1024 * 1024)).toBe("5.0 MB");
    expect(humanSize(10_485_760)).toBe("10.0 MB"); // 10 MiB
  });
});

describe("<AppKbContentPanel> — mount + expand", () => {
  it("AC#1 — mount renders Collapse (closed by default, no fetch fires)", () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    render(<AppKbContentPanel appId="default" />);
    expect(screen.getByTestId("app-kb-content-panel")).toBeInTheDocument();
    expect(screen.queryByTestId("app-kb-content-list")).toBeNull();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("AC#1 + AC#2 + AC#9 — expand triggers /api/v1/ncmu/kbs/<appId>/files with backend byte-exact fields → renders List", async () => {
    const fetchSpy = vi.fn(() => Promise.resolve(jsonResponse(sampleFiles)));
    vi.stubGlobal("fetch", fetchSpy);
    render(<AppKbContentPanel appId="default" />);
    fireEvent.click(screen.getByText("知识库内容"));

    // PC2-FIX prefix: /api/v1/ncmu/kbs/... — NOT the old /v1/kbs/...
    // (which would route to dify-nginx per docker/nginx/dev.conf.template).
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/v1/ncmu/kbs/default/files",
        expect.objectContaining({
          headers: expect.objectContaining({
            Authorization: "Bearer test-jwt",
          }),
        }),
      );
    });

    // List + each item by testid (plan §4.8 AC#8).
    await waitFor(() => {
      expect(screen.getByTestId("app-kb-content-list")).toBeInTheDocument();
    });
    expect(
      screen.getByTestId("app-kb-content-file-item-f-policy"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("app-kb-content-file-item-f-onboarding"),
    ).toBeInTheDocument();
    // Size字面: 512 KiB → "512.0 KB", 2 MiB → "2.0 MB".
    expect(screen.getByText(/员工手册\.pdf \(512\.0 KB\)/)).toBeInTheDocument();
    expect(screen.getByText(/入职流程\.docx \(2\.0 MB\)/)).toBeInTheDocument();
  });

  it("AC#2 — 0-file response renders antd Empty '暂无文档'", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse([]))));
    render(<AppKbContentPanel appId="default" />);
    fireEvent.click(screen.getByText("知识库内容"));
    await waitFor(() => {
      expect(screen.getByText("暂无文档")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("app-kb-content-list")).toBeNull();
  });
});

describe("<AppKbContentPanel> — download (plan §4.8 AC#3)", () => {
  it("download button click → window.open('/api/v1/ncmu/kbs/<appId>/files/<fileId>/download', '_self')", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(sampleFiles))));
    const openSpy = vi.fn();
    vi.stubGlobal("open", openSpy);
    render(<AppKbContentPanel appId="default" />);
    fireEvent.click(screen.getByText("知识库内容"));
    await waitFor(() => {
      expect(
        screen.getByTestId("app-kb-content-download-button-f-policy"),
      ).toBeInTheDocument();
    });

    fireEvent.click(
      screen.getByTestId("app-kb-content-download-button-f-policy"),
    );
    expect(openSpy).toHaveBeenCalledTimes(1);
    expect(openSpy).toHaveBeenCalledWith(
      "/api/v1/ncmu/kbs/default/files/f-policy/download",
      "_self",
    );
  });
});

describe("<AppKbContentPanel> — 5-class error toasts (plan §4.8 AC#4 字面)", () => {
  async function expandWith(status: number, detail = "boom"): Promise<void> {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(errorResponse(status, detail))),
    );
    render(<AppKbContentPanel appId="default" />);
    fireEvent.click(screen.getByText("知识库内容"));
  }

  it("503 → message.error '知识库服务暂时不可用，请稍后再试'", async () => {
    await expandWith(503);
    await waitFor(() => {
      expect(errorSpy).toHaveBeenCalledWith(KB_ERROR_TEXT.unreachable);
    });
    expect(KB_ERROR_TEXT.unreachable).toBe("知识库服务暂时不可用，请稍后再试");
  });

  it("404 → message.warning '文档已被移除，列表已刷新'", async () => {
    await expandWith(404);
    await waitFor(() => {
      expect(warningSpy).toHaveBeenCalledWith(KB_ERROR_TEXT.notFound);
    });
    expect(KB_ERROR_TEXT.notFound).toBe("文档已被移除，列表已刷新");
  });

  it("401 → message.error '无权访问此知识库'", async () => {
    await expandWith(401);
    await waitFor(() => {
      expect(errorSpy).toHaveBeenCalledWith(KB_ERROR_TEXT.forbidden);
    });
    expect(KB_ERROR_TEXT.forbidden).toBe("无权访问此知识库");
  });

  it("403 → message.error '无权访问此知识库' (same literal as 401)", async () => {
    await expandWith(403);
    await waitFor(() => {
      expect(errorSpy).toHaveBeenCalledWith(KB_ERROR_TEXT.forbidden);
    });
  });

  it("500 → message.error '知识库服务异常'", async () => {
    await expandWith(500);
    await waitFor(() => {
      expect(errorSpy).toHaveBeenCalledWith(KB_ERROR_TEXT.serverError);
    });
    expect(KB_ERROR_TEXT.serverError).toBe("知识库服务异常");
  });

  it("network failure (fetch rejects with TypeError) → message.error '网络连接失败'", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))),
    );
    render(<AppKbContentPanel appId="default" />);
    fireEvent.click(screen.getByText("知识库内容"));
    await waitFor(() => {
      expect(errorSpy).toHaveBeenCalledWith(KB_ERROR_TEXT.network);
    });
    expect(KB_ERROR_TEXT.network).toBe("网络连接失败");
  });
});

describe("<AppKbContentPanel> — 30 s dedup (plan §4.8 AC#5)", () => {
  it("expand → collapse → expand within window fires fetch only once", async () => {
    const fetchSpy = vi.fn(() => Promise.resolve(jsonResponse(sampleFiles)));
    vi.stubGlobal("fetch", fetchSpy);
    render(<AppKbContentPanel appId="default" />);

    // First expand — fires fetch.
    fireEvent.click(screen.getByText("知识库内容"));
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledTimes(1);
      expect(screen.getByTestId("app-kb-content-list")).toBeInTheDocument();
    });

    // Collapse.
    fireEvent.click(screen.getByText("知识库内容"));

    // Re-expand within the 30 s window — cache hit, no extra fetch.
    fireEvent.click(screen.getByText("知识库内容"));
    await waitFor(() => {
      expect(screen.getByTestId("app-kb-content-list")).toBeInTheDocument();
    });
    // The fetch counter must not have advanced.
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });
});
