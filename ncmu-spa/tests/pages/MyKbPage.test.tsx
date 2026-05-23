// TASK-PC3-A — MyKbPage tests.
//
// Mocks `@/lib/api` so the hook + page never touch fetch. Each test
// constructs the api response with real Promise resolutions (守
// `feedback_tdd_mock_vs_real_api` SOP 10 — no "never resolves / never
// rejects" mocks). Fixture URLs match the BASE-relative path strings
// the page would issue against the real backend.

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
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

vi.mock("@/lib/api", async (importActual) => {
  const actual = await importActual<typeof import("@/lib/api")>();
  return {
    ...actual,
    listMyApplications: vi.fn(),
    submitApplication: vi.fn(),
    cancelApplication: vi.fn(),
  };
});

import { notification } from "antd";
import { MyKbPage } from "@/pages/MyKbPage";
import {
  ApiError,
  cancelApplication,
  listMyApplications,
  type PersonalKbApplication,
} from "@/lib/api";

const mockList = vi.mocked(listMyApplications);
const mockCancel = vi.mocked(cancelApplication);

// Antd 5 Grid useBreakpoint + Modal pull window.matchMedia. jsdom does
// not implement it — polyfill in beforeAll (mirror CompletionPage.test).
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
  mockList.mockReset();
  mockCancel.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
  // Re-assert document.visibilityState to "visible" so cross-test state
  // does not leak (the polling test mutates `hidden`).
  Object.defineProperty(document, "hidden", {
    configurable: true,
    get: () => false,
  });
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    get: () => "visible",
  });
});

function fixtureApp(
  overrides: Partial<PersonalKbApplication> = {},
): PersonalKbApplication {
  return {
    id: "00000000-0000-4000-8000-000000000001",
    user_id: "00000000-0000-4000-8000-0000000000aa",
    kb_name_suggested: "研发部 OKR KB",
    description: "OKR 文档归集",
    status: "pending",
    fastgpt_dataset_id: null,
    dify_app_id: null,
    admin_processed_by: null,
    admin_processed_at: null,
    rejection_reason: null,
    created_at: new Date(Date.now() - 5 * 60 * 1000).toISOString(),
    ...overrides,
  };
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/my-kb"]}>
      <Routes>
        <Route path="/my-kb" element={<MyKbPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("<MyKbPage>", () => {
  it("AC#1(a) — fetches + renders applications with status badges + page landmark", async () => {
    mockList.mockResolvedValueOnce([
      fixtureApp({ id: "id-pending", status: "pending" }),
      fixtureApp({
        id: "id-done",
        status: "done",
        dify_app_id: "dify-app-X",
        kb_name_suggested: "财务季报 KB",
      }),
      fixtureApp({
        id: "id-rejected",
        status: "rejected",
        rejection_reason: "文件不符合规范",
        kb_name_suggested: "法务合同 KB",
      }),
    ]);

    renderPage();
    expect(screen.getByTestId("my-kb-page")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /申请新建知识库/ }),
    ).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByTestId("my-kb-application-list")).toBeInTheDocument();
    });
    const list = screen.getByTestId("my-kb-application-list");
    expect(within(list).getByTestId("my-kb-application-item-id-pending")).toBeInTheDocument();
    expect(within(list).getByTestId("my-kb-application-item-id-done")).toBeInTheDocument();
    expect(within(list).getByTestId("my-kb-application-item-id-rejected")).toBeInTheDocument();
    // Status badges: literals 待处理 / 已完成 / 已拒绝 — scope to list since the
    // Segmented filter chips above also carry these labels.
    expect(within(list).getByText("待处理")).toBeInTheDocument();
    expect(within(list).getByText("已完成")).toBeInTheDocument();
    expect(within(list).getByText("已拒绝")).toBeInTheDocument();
    // Rejection reason inline alert
    expect(
      within(list).getByText("拒绝原因：文件不符合规范"),
    ).toBeInTheDocument();
    // listMyApplications was called once (initial fetch).
    expect(mockList).toHaveBeenCalledTimes(1);
  });

  it("AC#1(b) — Segmented status filter narrows the list", async () => {
    mockList.mockResolvedValueOnce([
      fixtureApp({ id: "p1", status: "pending" }),
      fixtureApp({ id: "p2", status: "pending" }),
      fixtureApp({
        id: "d1",
        status: "done",
        dify_app_id: "dify-1",
      }),
    ]);
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("my-kb-application-item-p1")).toBeInTheDocument();
    });
    expect(screen.getByTestId("my-kb-application-item-d1")).toBeInTheDocument();

    // Click on "已完成" filter chip in the Segmented control.
    const filter = screen.getByTestId("my-kb-status-filter");
    fireEvent.click(within(filter).getByText("已完成"));

    await waitFor(() => {
      expect(screen.queryByTestId("my-kb-application-item-p1")).not.toBeInTheDocument();
    });
    expect(screen.queryByTestId("my-kb-application-item-p2")).not.toBeInTheDocument();
    expect(screen.getByTestId("my-kb-application-item-d1")).toBeInTheDocument();
  });

  it("AC#4 — pending row [撤回] → Popconfirm 确认 → cancelApplication + refetch", async () => {
    mockList
      .mockResolvedValueOnce([
        fixtureApp({ id: "to-cancel", status: "pending" }),
      ])
      .mockResolvedValueOnce([
        fixtureApp({ id: "to-cancel", status: "cancelled" }),
      ]);
    mockCancel.mockResolvedValueOnce(undefined);
    const successSpy = vi
      .spyOn(notification, "success")
      .mockImplementation(() => undefined);

    renderPage();
    const cancelBtn = await screen.findByTestId("my-kb-cancel-button-to-cancel");
    fireEvent.click(cancelBtn);
    // Popconfirm renders an "确认" button — click it.
    const confirmBtn = await screen.findByRole("button", { name: /确\s?认/ });
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(mockCancel).toHaveBeenCalledWith("to-cancel");
    });
    await waitFor(() => {
      expect(successSpy).toHaveBeenCalledTimes(1);
    });
    // Refetch fires — listMyApplications called twice total (initial + post-cancel).
    await waitFor(() => {
      expect(mockList).toHaveBeenCalledTimes(2);
    });
    // After refetch the row's status badge re-renders as 已撤回 — scope to
    // the list since the Segmented filter chip also carries this label.
    await waitFor(() => {
      expect(
        within(screen.getByTestId("my-kb-application-list")).getByText(
          "已撤回",
        ),
      ).toBeInTheDocument();
    });
  });

  it("AC#4 fail — cancelApplication rejects with 409 → notification.error showing body.detail", async () => {
    mockList.mockResolvedValueOnce([
      fixtureApp({ id: "stuck", status: "pending" }),
    ]);
    mockCancel.mockRejectedValueOnce(
      new ApiError(409, undefined, "仅待处理状态可撤回"),
    );
    const errorSpy = vi
      .spyOn(notification, "error")
      .mockImplementation(() => undefined);

    renderPage();
    fireEvent.click(await screen.findByTestId("my-kb-cancel-button-stuck"));
    fireEvent.click(await screen.findByRole("button", { name: /确\s?认/ }));

    await waitFor(() => {
      expect(errorSpy).toHaveBeenCalledTimes(1);
    });
    expect(errorSpy.mock.calls[0]![0]).toEqual(
      expect.objectContaining({
        message: "撤回失败",
        description: "仅待处理状态可撤回",
      }),
    );
  });

  it("AC#1 empty — list resolves empty → Empty 占位渲染", async () => {
    mockList.mockResolvedValueOnce([]);
    renderPage();
    await waitFor(() => {
      expect(screen.getByText("暂无申请记录")).toBeInTheDocument();
    });
  });

  it("AC#1 error — list rejects with 5xx → Alert 红条 + error.message", async () => {
    mockList.mockRejectedValueOnce(
      new ApiError(503, undefined, "service unavailable"),
    );
    renderPage();
    await waitFor(() => {
      expect(screen.getByText("加载申请列表失败")).toBeInTheDocument();
    });
    expect(screen.getByText("service unavailable")).toBeInTheDocument();
  });

  // AC#5 — polling 60s. Use fake timers to advance the setInterval tick
  // and assert listMyApplications was re-invoked. Each tick must complete
  // a real promise resolution (mockResolvedValueOnce) so the hook's
  // setState path runs —守 SOP 10 mock 真实化.
  it("AC#5 — polling fires listMyApplications every 60s", async () => {
    vi.useFakeTimers();
    mockList
      .mockResolvedValueOnce([fixtureApp({ id: "a1", status: "pending" })])
      .mockResolvedValueOnce([fixtureApp({ id: "a1", status: "in_progress" })])
      .mockResolvedValue([fixtureApp({ id: "a1", status: "done", dify_app_id: "x" })]);

    renderPage();
    // Drain microtasks so the initial fetchOnce promise resolves.
    await act(async () => {
      await Promise.resolve();
    });
    expect(mockList).toHaveBeenCalledTimes(1);

    // Tick 1 — 60 s elapsed.
    await act(async () => {
      vi.advanceTimersByTime(60_000);
      await Promise.resolve();
    });
    expect(mockList).toHaveBeenCalledTimes(2);

    // Tick 2 — 120 s total.
    await act(async () => {
      vi.advanceTimersByTime(60_000);
      await Promise.resolve();
    });
    expect(mockList).toHaveBeenCalledTimes(3);
  });

  // AC#5 — when document is hidden (tab inactive) the polling tick must
  // not fire fresh fetches. We mutate `document.hidden` + dispatch the
  // visibilitychange event the hook listens for.
  it("AC#5 — polling pauses while document.hidden", async () => {
    vi.useFakeTimers();
    mockList.mockResolvedValueOnce([
      fixtureApp({ id: "v1", status: "pending" }),
    ]);
    renderPage();
    await act(async () => {
      await Promise.resolve();
    });
    expect(mockList).toHaveBeenCalledTimes(1);

    // Flip to hidden + dispatch visibilitychange so the hook stops the
    // interval. After the next 60 s tick, listMyApplications stays at 1.
    Object.defineProperty(document, "hidden", {
      configurable: true,
      get: () => true,
    });
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => "hidden",
    });
    document.dispatchEvent(new Event("visibilitychange"));

    await act(async () => {
      vi.advanceTimersByTime(60_000);
      await Promise.resolve();
    });
    expect(mockList).toHaveBeenCalledTimes(1);
  });

  it("AC#1 — [+ 申请新建知识库] button opens the modal", async () => {
    mockList.mockResolvedValueOnce([]);
    renderPage();
    await waitFor(() => {
      expect(screen.getByText("暂无申请记录")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("my-kb-new-button"));
    // Modal title — antd Modal renders body into a portal, but jsdom
    // attaches portals to document.body so getByText still finds it.
    await waitFor(() => {
      expect(screen.getByText("申请新建知识库")).toBeInTheDocument();
    });
  });
});
