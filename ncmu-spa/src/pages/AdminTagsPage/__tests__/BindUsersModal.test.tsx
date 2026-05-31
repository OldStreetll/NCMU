// TASK-PE-09 — BindUsersModal (tag→users) wiring tests.
//
// Wire shape against admin/tags/routes.py: on open GETs the full user list
// (useAdminUsers → GET /admin/users?...) + the tag's bound users (GET
// /admin/tags/{id}/users); on 保存 PUTs {user_ids:[...]} to
// /admin/tags/{id}/users. Reverse-roles mirror of BindTagsModal.test.tsx
// (app→tags). Real Response mocks; /保\s*存/ for antd CJK autospace.

import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { BindUsersModal } from "@/pages/AdminTagsPage/BindUsersModal";
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

const USER_ZHANG = {
  id: "22222222-2222-4000-8000-000000000001",
  name: "张三",
  dingtalk_userid: "dt-zhang",
  dept_path: null,
  is_active: true,
  is_admin: true,
  tags: [],
};
const USER_LI = {
  id: "22222222-2222-4000-8000-000000000002",
  name: "李四",
  dingtalk_userid: "dt-li",
  dept_path: null,
  is_active: true,
  is_admin: false,
  tags: [],
};
const TAG_ID = "11111111-1111-4000-8000-000000000001";

function routeFetch(boundUserIds: string[]) {
  fetchMock.mockImplementation((url: string) => {
    const u = String(url);
    if (u.includes(`/admin/tags/${TAG_ID}/users`)) {
      return Promise.resolve(
        jsonResponse(200, { tag_id: TAG_ID, user_ids: boundUserIds }),
      );
    }
    if (u.includes("/admin/users")) {
      return Promise.resolve(
        jsonResponse(200, { total: 2, items: [USER_ZHANG, USER_LI] }),
      );
    }
    return Promise.resolve(jsonResponse(200, {}));
  });
}

function renderModal() {
  return render(
    <MemoryRouter>
      <BindUsersModal
        tagId={TAG_ID}
        tagName="技术"
        open
        onClose={() => {}}
        onSaved={() => {}}
      />
    </MemoryRouter>,
  );
}

describe("BindUsersModal — wire shape", () => {
  it("on open: GETs full user list + tag's bound users", async () => {
    routeFetch([USER_ZHANG.id]);
    renderModal();
    await waitFor(() => {
      const urls = fetchMock.mock.calls.map((c) => String(c[0]));
      expect(urls.some((u) => u.includes("/api/v1/ncmu/admin/users"))).toBe(true);
      expect(
        urls.some((u) => u === `/api/v1/ncmu/admin/tags/${TAG_ID}/users`),
      ).toBe(true);
    });
    expect(await screen.findByText("张三")).toBeInTheDocument();
    expect(screen.getByText("李四")).toBeInTheDocument();
  });

  it("保存 PUTs replace-all {user_ids} reflecting current bound set", async () => {
    routeFetch([USER_ZHANG.id]);
    renderModal();
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          (c) => String(c[0]) === `/api/v1/ncmu/admin/tags/${TAG_ID}/users`,
        ),
      ).toBe(true),
    );

    fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));

    await waitFor(() => {
      const put = fetchMock.mock.calls.find(
        (c) =>
          String(c[0]) === `/api/v1/ncmu/admin/tags/${TAG_ID}/users` &&
          (c[1] as RequestInit)?.method === "PUT",
      );
      expect(put).toBeTruthy();
      expect(JSON.parse((put![1] as RequestInit).body as string)).toEqual({
        user_ids: [USER_ZHANG.id],
      });
    });
  });
});
