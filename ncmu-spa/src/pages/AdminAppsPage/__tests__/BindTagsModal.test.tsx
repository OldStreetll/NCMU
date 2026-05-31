// TASK-PE-08 — BindTagsModal (app→tags, reverse) wiring tests.
//
// Wire shape against admin/apps/routes.py: on open GETs the full tag list
// (useAdminTags) + the app's bound tags (GET /admin/apps/{id}/tags); on
// 保存 PUTs {tag_ids:[...]} to /admin/apps/{id}/tags. Symmetric to
// BindAppsModal. Real Response mocks; /保\s*存/ for antd CJK autospace.

import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { BindTagsModal } from "@/pages/AdminAppsPage/BindTagsModal";
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

const TAG_TECH = {
  id: "11111111-1111-4000-8000-000000000001",
  name: "技术",
  description: null,
  app_count: 0,
  user_count: 0,
};
const TAG_HR = {
  id: "11111111-1111-4000-8000-000000000002",
  name: "人事",
  description: null,
  app_count: 0,
  user_count: 0,
};
const APP_ID = "app-a";

function routeFetch(boundTagIds: string[]) {
  fetchMock.mockImplementation((url: string) => {
    const u = String(url);
    if (u.includes("/admin/tags") && !u.includes("/apps")) {
      return Promise.resolve(jsonResponse(200, [TAG_TECH, TAG_HR]));
    }
    if (u.includes(`/admin/apps/${APP_ID}/tags`)) {
      return Promise.resolve(
        jsonResponse(200, { dify_app_id: APP_ID, tag_ids: boundTagIds }),
      );
    }
    return Promise.resolve(jsonResponse(200, {}));
  });
}

function renderModal() {
  return render(
    <MemoryRouter>
      <BindTagsModal
        appId={APP_ID}
        appName="Alpha 客服"
        open
        onClose={() => {}}
        onSaved={() => {}}
      />
    </MemoryRouter>,
  );
}

describe("BindTagsModal — wire shape", () => {
  it("on open: GETs full tag list + app's bound tags", async () => {
    routeFetch([TAG_TECH.id]);
    renderModal();
    await waitFor(() => {
      const urls = fetchMock.mock.calls.map((c) => String(c[0]));
      expect(urls.some((u) => u === "/api/v1/ncmu/admin/tags")).toBe(true);
      expect(urls.some((u) => u === `/api/v1/ncmu/admin/apps/${APP_ID}/tags`)).toBe(true);
    });
    expect(await screen.findByText("技术")).toBeInTheDocument();
    expect(screen.getByText("人事")).toBeInTheDocument();
  });

  it("保存 PUTs replace-all {tag_ids} reflecting current bound set", async () => {
    routeFetch([TAG_TECH.id]);
    renderModal();
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          (c) => String(c[0]) === `/api/v1/ncmu/admin/apps/${APP_ID}/tags`,
        ),
      ).toBe(true),
    );

    fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));

    await waitFor(() => {
      const put = fetchMock.mock.calls.find(
        (c) =>
          String(c[0]) === `/api/v1/ncmu/admin/apps/${APP_ID}/tags` &&
          (c[1] as RequestInit)?.method === "PUT",
      );
      expect(put).toBeTruthy();
      expect(JSON.parse((put![1] as RequestInit).body as string)).toEqual({
        tag_ids: [TAG_TECH.id],
      });
    });
  });
});
