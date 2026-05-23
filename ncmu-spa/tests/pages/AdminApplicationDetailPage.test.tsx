// TASK-PC3-B — AdminApplicationDetailPage wiring tests.
//
// Covers: dual-column render / required Form validation / dispatch happy +
// 409 / reject flow / naming-suggestion copy / claim flow 2 cases (N-C1).
//
// All fetches use real Response objects (SOP 10). The claim cases double as
// the "mock-vs-real cross-component" guard called out in the dispatch task —
// each mocked response is a real `Response` instance so the api() helper's
// body parsing actually runs, not a never-resolving stub.

import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AdminApplicationDetailPage } from "@/pages/AdminApplicationDetailPage";
import type { ApplicationDetailOut, ApplicationOut } from "@/lib/api";
import { setAuth, clearAuth } from "@/lib/auth";

// ----- jsdom polyfills -----------------------------------------------------
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
const APPLICATION_ID = "11111111-1111-4111-8111-111111111111";
const ADMIN_UUID = "abc-uuid-1234";

function makeDetail(
  overrides: Partial<ApplicationDetailOut> = {},
): ApplicationDetailOut {
  return {
    id: APPLICATION_ID,
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
    files: [
      {
        id: "f1111111-1111-4111-8111-111111111111",
        original_filename: "spec.pdf",
        file_size_bytes: 102400,
        content_type: "application/pdf",
        sha256: null,
        uploaded_at: "2026-05-23T09:55:00Z",
      },
    ],
    ...overrides,
  };
}

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
  // Admin auth required for all paths reaching this page.
  setAuth("test-jwt-admin", {
    id: ADMIN_UUID,
    name: "Admin",
    is_active: true,
    is_admin: true,
  });
});

afterEach(() => {
  clearAuth();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

function renderDetailAt(id: string = APPLICATION_ID) {
  return render(
    <MemoryRouter initialEntries={[`/admin/personal-kb/applications/${id}`]}>
      <Routes>
        <Route
          path="/admin/personal-kb"
          element={<div data-testid="list-placeholder">list</div>}
        />
        <Route
          path="/admin/personal-kb/applications/:id"
          element={<AdminApplicationDetailPage />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

// Filter the mock to inspect a specific URL/method pair.
function findFetchCall(
  urlPattern: RegExp,
  method: string = "GET",
): [string, RequestInit | undefined] | undefined {
  for (const call of fetchMock.mock.calls) {
    const url = typeof call[0] === "string" ? call[0] : String(call[0]);
    const init = call[1] as RequestInit | undefined;
    const m = (init?.method ?? "GET").toUpperCase();
    if (urlPattern.test(url) && m === method.toUpperCase()) {
      return [url, init];
    }
  }
  return undefined;
}

// ----- tests ---------------------------------------------------------------
describe("<AdminApplicationDetailPage> (TASK-PC3-B)", () => {
  it("(a) renders dual-column layout + left/right panels on detail load", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, makeDetail()));
    renderDetailAt();

    await waitFor(() =>
      expect(screen.getByTestId("admin-application-detail-page")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("admin-detail-left-panel")).toBeInTheDocument();
    expect(screen.getByTestId("admin-detail-right-panel")).toBeInTheDocument();
    expect(screen.getByTestId("admin-naming-suggestion")).toBeInTheDocument();
    expect(screen.getByTestId("admin-detail-file-list")).toBeInTheDocument();

    // Detail-load GET fired against the new prefix.
    const call = findFetchCall(
      /\/api\/v1\/ncmu\/admin\/personal-kb\/applications\/11111111/,
      "GET",
    );
    expect(call).toBeDefined();
  });

  it("(b) dispatch button disabled while status === 'pending'", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, makeDetail()));
    renderDetailAt();
    await waitFor(() =>
      expect(screen.getByTestId("admin-application-detail-page")).toBeInTheDocument(),
    );

    const dispatchBtn = screen.getByTestId("admin-dispatch-button");
    expect(dispatchBtn).toBeDisabled();
    // Reject still enabled while pending.
    expect(screen.getByTestId("admin-reject-button")).not.toBeDisabled();
  });

  it("(c) form Required validation: empty submit blocks dispatch", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(200, makeDetail({ status: "in_progress", admin_processed_by: ADMIN_UUID })),
    );
    renderDetailAt();
    await waitFor(() =>
      expect(screen.getByTestId("admin-application-detail-page")).toBeInTheDocument(),
    );

    const dispatchBtn = screen.getByTestId("admin-dispatch-button");
    fireEvent.click(dispatchBtn);
    // No dispatch POST emitted because Form validation refuses empty fields.
    await waitFor(() => {
      const dispatched = findFetchCall(/\/dispatch$/, "POST");
      expect(dispatched).toBeUndefined();
    });
  });

  it("(d) dispatch happy path → POST + navigate back to list", async () => {
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse(200, makeDetail({ status: "in_progress", admin_processed_by: ADMIN_UUID })),
      )
      .mockResolvedValueOnce(
        jsonResponse(200, {
          ...makeDetail({ status: "done" }),
          files: undefined,
        }),
      );
    renderDetailAt();

    await waitFor(() =>
      expect(screen.getByTestId("admin-application-detail-page")).toBeInTheDocument(),
    );

    fireEvent.change(screen.getByTestId("admin-form-dataset-id"), {
      target: { value: "66e2a7f8a9b0c1d2e3f45678" },
    });
    fireEvent.change(screen.getByTestId("admin-form-dify-app-id"), {
      target: { value: "abc12def" },
    });
    fireEvent.change(screen.getByTestId("admin-form-kb-name-final"), {
      target: { value: "工程手册-v2" },
    });

    fireEvent.click(screen.getByTestId("admin-dispatch-button"));

    await waitFor(() =>
      expect(screen.getByTestId("list-placeholder")).toBeInTheDocument(),
    );

    const dispatched = findFetchCall(/\/dispatch$/, "POST");
    expect(dispatched).toBeDefined();
    const body = JSON.parse(dispatched![1]!.body as string) as Record<string, unknown>;
    expect(body).toEqual({
      dataset_id: "66e2a7f8a9b0c1d2e3f45678",
      dify_app_id: "abc12def",
      kb_name_final: "工程手册-v2",
    });
  });

  it("(e) dispatch 409 → form-level Alert shows body.detail", async () => {
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse(200, makeDetail({ status: "in_progress", admin_processed_by: ADMIN_UUID })),
      )
      .mockResolvedValueOnce(
        jsonResponse(409, { detail: "KB 名称冲突或外部资源不可用" }),
      );
    renderDetailAt();
    await waitFor(() =>
      expect(screen.getByTestId("admin-application-detail-page")).toBeInTheDocument(),
    );

    fireEvent.change(screen.getByTestId("admin-form-dataset-id"), {
      target: { value: "ds-1" },
    });
    fireEvent.change(screen.getByTestId("admin-form-dify-app-id"), {
      target: { value: "app-1" },
    });
    fireEvent.change(screen.getByTestId("admin-form-kb-name-final"), {
      target: { value: "重名 KB" },
    });

    fireEvent.click(screen.getByTestId("admin-dispatch-button"));

    const alert = await screen.findByTestId("admin-dispatch-error");
    expect(alert.textContent).toContain("KB 名称冲突或外部资源不可用");

    // Page stays on detail (no nav to list).
    expect(screen.queryByTestId("list-placeholder")).not.toBeInTheDocument();
  });

  it("(f) reject flow: open modal → submit → navigate back", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(200, makeDetail()))
      .mockResolvedValueOnce(jsonResponse(200, makeDetail({ status: "rejected" })));
    renderDetailAt();

    await waitFor(() =>
      expect(screen.getByTestId("admin-application-detail-page")).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByTestId("admin-reject-button"));

    const modal = await screen.findByTestId("admin-reject-modal");
    const textarea = within(modal).getByTestId("admin-reject-reason");
    fireEvent.change(textarea, { target: { value: "材料不全" } });
    // antd Modal's OK button text "提交拒绝".
    const okBtn = within(modal).getByText("提交拒绝").closest("button")!;
    fireEvent.click(okBtn);

    await waitFor(() =>
      expect(screen.getByTestId("list-placeholder")).toBeInTheDocument(),
    );

    const rejected = findFetchCall(/\/reject$/, "POST");
    expect(rejected).toBeDefined();
    const body = JSON.parse(rejected![1]!.body as string) as Record<string, unknown>;
    expect(body).toEqual({ rejection_reason: "材料不全" });
  });

  it("(g) naming suggestion copy → calls navigator.clipboard.writeText", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
      writable: true,
    });
    fetchMock.mockResolvedValueOnce(jsonResponse(200, makeDetail()));
    renderDetailAt();

    await waitFor(() =>
      expect(screen.getByTestId("admin-application-detail-page")).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByTestId("admin-copy-chat-app-name"));
    expect(writeText).toHaveBeenCalledWith("工程手册");

    fireEvent.click(screen.getByTestId("admin-copy-chat-app-tag"));
    expect(writeText).toHaveBeenCalledWith("kb-工程手册");

    fireEvent.click(screen.getByTestId("admin-copy-dataset-name"));
    expect(writeText).toHaveBeenCalledWith("工程手册");
  });

  it(
    "(h) test_claim_pending_then_dispatch: claim 200 → badge flips + dispatch enables + claim hides",
    async () => {
      const detailPending = makeDetail({ status: "pending" });
      const detailInProgress: ApplicationOut = {
        ...detailPending,
        status: "in_progress",
        admin_processed_by: ADMIN_UUID,
        admin_processed_at: "2026-05-23T11:30:00Z",
      };
      // After claim, useAdminApplication invalidates → re-fetch detail.
      const detailInProgressFull: ApplicationDetailOut = {
        ...detailInProgress,
        files: detailPending.files,
      };

      fetchMock
        // 1. initial GET detail (pending)
        .mockResolvedValueOnce(jsonResponse(200, detailPending))
        // 2. POST claim → 200 ApplicationOut (no files key — backend's slim shape)
        .mockResolvedValueOnce(jsonResponse(200, detailInProgress))
        // 3. invalidate-triggered GET detail (in_progress, full)
        .mockResolvedValueOnce(jsonResponse(200, detailInProgressFull));

      renderDetailAt();

      await waitFor(() =>
        expect(screen.getByTestId("admin-application-detail-page")).toBeInTheDocument(),
      );

      // Pending baseline: claim visible, dispatch disabled.
      const claimBtn = screen.getByTestId("admin-claim-button");
      expect(claimBtn).toBeInTheDocument();
      expect(screen.getByTestId("admin-dispatch-button")).toBeDisabled();
      expect(screen.getByTestId("admin-status-badge").textContent).toBe("待处理");

      fireEvent.click(claimBtn);

      // After invalidate re-fetch resolves, status flips and claim hides.
      await waitFor(() => {
        const badge = screen.getByTestId("admin-status-badge");
        expect(badge.textContent).toBe(`处理中（${ADMIN_UUID} 占坑）`);
      });
      expect(screen.queryByTestId("admin-claim-button")).not.toBeInTheDocument();
      expect(screen.getByTestId("admin-dispatch-button")).not.toBeDisabled();

      // Verify the claim POST actually fired with the right URL + method.
      const claim = findFetchCall(/\/applications\/[\w-]+\/claim$/, "POST");
      expect(claim).toBeDefined();
    },
  );

  it(
    "(i) test_claim_409_already_claimed: badge stays + dispatch stays disabled + error surfaces",
    async () => {
      const detailPending = makeDetail({ status: "pending" });

      fetchMock
        // 1. initial GET detail (pending)
        .mockResolvedValueOnce(jsonResponse(200, detailPending))
        // 2. POST claim → 409 with body.detail in Chinese
        .mockResolvedValueOnce(
          jsonResponse(409, { detail: "该申请已被其他管理员认领" }),
        );

      renderDetailAt();
      await waitFor(() =>
        expect(screen.getByTestId("admin-application-detail-page")).toBeInTheDocument(),
      );

      fireEvent.click(screen.getByTestId("admin-claim-button"));

      // Wait for the antd toast to land in the portal.
      await waitFor(() => {
        const toast = document.body.textContent ?? "";
        expect(toast).toContain("该申请已被其他管理员认领");
      });

      // Badge unchanged: still "待处理".
      expect(screen.getByTestId("admin-status-badge").textContent).toBe("待处理");
      // Dispatch button still disabled (status never flipped).
      expect(screen.getByTestId("admin-dispatch-button")).toBeDisabled();
      // Claim button still visible (status still pending).
      expect(screen.getByTestId("admin-claim-button")).toBeInTheDocument();
    },
  );
});
