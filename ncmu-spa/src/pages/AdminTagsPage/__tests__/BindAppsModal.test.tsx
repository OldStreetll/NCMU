// TASK-PE-08 — BindAppsModal (tag→apps) wiring tests.
//
// Wire shape against admin/tags/routes.py: on open GETs both the full app
// list (useAdminApps, include_inactive=true) and the tag's current bound
// apps (GET /admin/tags/{id}/apps); on 保存 PUTs the replace-all body
// {app_ids:[...]} to /admin/tags/{id}/apps. Real Response objects in fetch
// mocks (守 SOP 10 feedback_tdd_mock_vs_real_api). antd 2-CJK button
// "保存" autoInsertSpace → /保\s*存/ regex (守 antd_v5_cjk pitfall).

import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { BindAppsModal } from "@/pages/AdminTagsPage/BindAppsModal";
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

const APP_A = {
  dify_app_id: "app-a",
  name: "Alpha 客服",
  mode: "chat",
  is_active: true,
  last_synced_at: null,
  tag_count: 0,
};
const APP_B = {
  dify_app_id: "app-b",
  name: "Beta 工作流",
  mode: "workflow",
  is_active: true,
  last_synced_at: null,
  tag_count: 0,
};
const TAG_ID = "11111111-1111-4000-8000-000000000001";

// Route the fetch mock by URL so the modal's two concurrent GETs resolve.
function routeFetch(boundAppIds: string[]) {
  fetchMock.mockImplementation((url: string) => {
    const u = String(url);
    if (u.includes("/admin/apps")) {
      return Promise.resolve(jsonResponse(200, [APP_A, APP_B]));
    }
    if (u.includes(`/admin/tags/${TAG_ID}/apps`)) {
      return Promise.resolve(
        jsonResponse(200, { tag_id: TAG_ID, app_ids: boundAppIds }),
      );
    }
    return Promise.resolve(jsonResponse(200, {}));
  });
}

function renderModal() {
  return render(
    <MemoryRouter>
      <BindAppsModal
        tagId={TAG_ID}
        tagName="技术"
        open
        onClose={() => {}}
        onSaved={() => {}}
      />
    </MemoryRouter>,
  );
}

describe("BindAppsModal — wire shape", () => {
  it("on open: GETs full app list (include_inactive) + tag's bound apps", async () => {
    routeFetch(["app-a"]);
    renderModal();
    await waitFor(() => {
      const urls = fetchMock.mock.calls.map((c) => String(c[0]));
      expect(urls.some((u) => u.includes("/admin/apps?include_inactive=true"))).toBe(true);
      expect(urls.some((u) => u === `/api/v1/ncmu/admin/tags/${TAG_ID}/apps`)).toBe(true);
    });
    // App titles render in the Transfer.
    expect(await screen.findByText("Alpha 客服")).toBeInTheDocument();
    expect(screen.getByText("Beta 工作流")).toBeInTheDocument();
  });

  it("保存 PUTs replace-all {app_ids} reflecting current bound set", async () => {
    routeFetch(["app-a"]);
    renderModal();
    // Gate on RENDER (not just the GET call firing): findByText polls until
    // both concurrent GETs have resolved + the Transfer has rendered, so
    // getTagApps' setTargetKeys(["app-a"]) has applied before we save.
    // Waiting only for the GET *call* to appear (pre-REWORK) raced the
    // response/setState → PUT body sometimes {app_ids:[]} (component was
    // correct; the test was flaky). Aligns with the :110 open case + the
    // sister BindTagsModal save test.
    await screen.findByText("Alpha 客服");

    fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));

    await waitFor(() => {
      const put = fetchMock.mock.calls.find(
        (c) =>
          String(c[0]) === `/api/v1/ncmu/admin/tags/${TAG_ID}/apps` &&
          (c[1] as RequestInit)?.method === "PUT",
      );
      expect(put).toBeTruthy();
      expect(JSON.parse((put![1] as RequestInit).body as string)).toEqual({
        app_ids: ["app-a"],
      });
    });
  });
});
