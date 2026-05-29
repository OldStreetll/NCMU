// TASK-PE-06 — AdminUsersPage wiring tests.
//
// Covers wire shape (URL + method + body field names) against backend
// admin/users/routes.py + key UI flows (list render / search / include
// inactive / create + toast / edit / deactivate Popconfirm). Real Response
// objects in fetch mocks (守 SOP 10 in `feedback_tdd_mock_vs_real_api`).
//
// Tests live alongside PE-01's layout/guard tests in src/pages/__tests__/
// (task file scope literal). The page-test convention elsewhere in this
// repo is tests/pages/; vitest discovery picks up both.

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
import { MemoryRouter } from "react-router-dom";
import AdminUsersPage from "@/pages/AdminUsersPage";
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

// ----- fetch mock helpers ------------------------------------------------- #
function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function emptyResponse(status: number): Response {
  return new Response(null, { status });
}

const fetchMock = vi.fn();
const clipboardWriteText = vi.fn().mockResolvedValue(undefined);

beforeEach(() => {
  fetchMock.mockReset();
  clipboardWriteText.mockClear();
  vi.stubGlobal("fetch", fetchMock);
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: clipboardWriteText },
  });
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

// ----- fixtures ---------------------------------------------------------- #
const USER_ZHANGSAN = {
  id: "a0000001-0000-4000-8000-000000000001",
  name: "张三",
  dingtalk_userid: null,
  dept_path: "/HR/招聘组",
  is_active: true,
  is_admin: true,
  tags: [],
};

const USER_LISI = {
  id: "a0000001-0000-4000-8000-000000000002",
  name: "李四",
  dingtalk_userid: "dingtalk-lisi-002",
  dept_path: "/技术/后端",
  is_active: true,
  is_admin: false,
  tags: [],
};

const USER_INACTIVE = {
  id: "deadbeef-0000-4000-8000-000000000099",
  name: "已停用",
  dingtalk_userid: null,
  dept_path: "/HR/离职组",
  is_active: false,
  is_admin: false,
  tags: [],
};

function renderPage() {
  return render(
    <MemoryRouter>
      <AdminUsersPage />
    </MemoryRouter>,
  );
}

function typeInto(input: HTMLElement, value: string) {
  fireEvent.change(input, { target: { value } });
}

// ----- tests ------------------------------------------------------------- #

describe("AdminUsersPage — wire shape", () => {
  it("loads /api/v1/ncmu/admin/users on mount with default params", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(200, { total: 2, items: [USER_ZHANGSAN, USER_LISI] }),
    );
    renderPage();
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toMatch(
      /^\/api\/v1\/ncmu\/admin\/users(\?|$)/,
    );
    expect((init as RequestInit).method ?? "GET").toBe("GET");
    expect(
      ((init as RequestInit).headers as Record<string, string>)["Authorization"],
    ).toBe("Bearer test-jwt-admin");
    const u = new URL(String(url), "http://localhost");
    const includeInactive = u.searchParams.get("include_inactive");
    expect(includeInactive === null || includeInactive === "false").toBe(true);
  });

  it("renders user rows from list response", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(200, { total: 2, items: [USER_ZHANGSAN, USER_LISI] }),
    );
    renderPage();
    expect(await screen.findByText("张三")).toBeInTheDocument();
    expect(screen.getByText("李四")).toBeInTheDocument();
    expect(screen.getByText("dingtalk-lisi-002")).toBeInTheDocument();
  });
});

describe("AdminUsersPage — search + toggle", () => {
  it("submitting search box triggers refetch with ?search=…", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(200, { total: 0, items: [] }),
    );
    renderPage();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    const searchInput = screen.getByPlaceholderText(/搜索/);
    typeInto(searchInput, "张");
    fireEvent.keyDown(searchInput, { key: "Enter", code: "Enter" });

    await waitFor(() => {
      const lastCall = fetchMock.mock.calls[fetchMock.mock.calls.length - 1];
      expect(String(lastCall[0])).toMatch(/[?&]search=%E5%BC%A0/);
    });
  });

  it('toggling "显示已停用" refetches with include_inactive=true', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(200, {
        total: 3,
        items: [USER_ZHANGSAN, USER_LISI, USER_INACTIVE],
      }),
    );
    renderPage();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    // antd Switch uses role="switch"
    const switchEl = screen.getByRole("switch", { name: /显示已停用/ });
    fireEvent.click(switchEl);

    await waitFor(() => {
      const lastCall = fetchMock.mock.calls[fetchMock.mock.calls.length - 1];
      expect(String(lastCall[0])).toMatch(/[?&]include_inactive=true/);
    });
  });
});

describe("AdminUsersPage — create flow", () => {
  it('opens modal on "新建用户" button and POSTs name to /admin/users', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(200, { total: 0, items: [] }))
      .mockResolvedValueOnce(
        jsonResponse(201, {
          ...USER_LISI,
          id: "abcdef12-0000-4000-8000-000000000abc",
          name: "新人甲",
          dingtalk_userid: null,
          dept_path: null,
        }),
      )
      .mockResolvedValueOnce(jsonResponse(200, { total: 1, items: [] }));

    renderPage();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole("button", { name: /新建用户/ }));
    const modal = await screen.findByRole("dialog");
    const nameInput = within(modal).getByLabelText(/姓名/);
    typeInto(nameInput, "新人甲");
    fireEvent.click(within(modal).getByRole("button", { name: /创\s*建/ }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    const postCall = fetchMock.mock.calls[1];
    expect(String(postCall[0])).toBe("/api/v1/ncmu/admin/users");
    expect((postCall[1] as RequestInit).method).toBe("POST");
    const body = JSON.parse((postCall[1] as RequestInit).body as string);
    expect(body.name).toBe("新人甲");
  });

  it("shows toast containing new user_id + dev-login hint after create", async () => {
    const newId = "abcdef12-0000-4000-8000-000000000abc";
    fetchMock
      .mockResolvedValueOnce(jsonResponse(200, { total: 0, items: [] }))
      .mockResolvedValueOnce(
        jsonResponse(201, {
          ...USER_LISI,
          id: newId,
          name: "新人乙",
          dingtalk_userid: null,
          dept_path: null,
        }),
      )
      .mockResolvedValueOnce(jsonResponse(200, { total: 1, items: [] }));

    renderPage();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: /新建用户/ }));
    const modal = await screen.findByRole("dialog");
    typeInto(within(modal).getByLabelText(/姓名/), "新人乙");
    fireEvent.click(within(modal).getByRole("button", { name: /创\s*建/ }));

    const toast = await screen.findByText((content) =>
      content.includes(newId) && content.includes("dev-login"),
    );
    expect(toast).toBeInTheDocument();
  });

  it("surfaces 409 duplicate-dingtalk error in modal alert", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(200, { total: 0, items: [] }))
      .mockResolvedValueOnce(
        jsonResponse(409, {
          detail: { code: 1010, message: "dingtalk_userid already exists" },
        }),
      );

    renderPage();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: /新建用户/ }));
    const modal = await screen.findByRole("dialog");
    typeInto(within(modal).getByLabelText(/姓名/), "撞钉钉");
    typeInto(within(modal).getByLabelText(/钉钉/), "dingtalk-existing");
    fireEvent.click(within(modal).getByRole("button", { name: /创\s*建/ }));

    await waitFor(() => {
      expect(
        within(modal).queryByText(/dingtalk_userid already exists/i),
      ).toBeInTheDocument();
    });
  });
});

describe("AdminUsersPage — edit + deactivate", () => {
  it("PATCHes name on save in edit modal", async () => {
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse(200, { total: 1, items: [USER_LISI] }),
      )
      .mockResolvedValueOnce(jsonResponse(200, { ...USER_LISI, name: "李四改" }))
      .mockResolvedValueOnce(
        jsonResponse(200, { total: 1, items: [{ ...USER_LISI, name: "李四改" }] }),
      );

    renderPage();
    await screen.findByText("李四");

    fireEvent.click(screen.getByRole("button", { name: /编\s*辑/ }));
    const modal = await screen.findByRole("dialog");
    const nameInput = within(modal).getByLabelText(/姓名/) as HTMLInputElement;
    expect(nameInput.value).toBe("李四");
    typeInto(nameInput, "李四改");
    fireEvent.click(within(modal).getByRole("button", { name: /保\s*存/ }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    const patchCall = fetchMock.mock.calls[1];
    expect(String(patchCall[0])).toBe(
      `/api/v1/ncmu/admin/users/${USER_LISI.id}`,
    );
    expect((patchCall[1] as RequestInit).method).toBe("PATCH");
    const body = JSON.parse((patchCall[1] as RequestInit).body as string);
    expect(body.name).toBe("李四改");
  });

  it("PATCH excludes unchanged fields when only name is edited", async () => {
    // REWORK-PE-06-INDEP #4 — editing only name must yield a PATCH body
    // containing solely { name: ... }; dingtalk_userid / dept_path /
    // is_active stay out of the wire request because they didn't change.
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse(200, { total: 1, items: [USER_LISI] }),
      )
      .mockResolvedValueOnce(jsonResponse(200, { ...USER_LISI, name: "李四改" }))
      .mockResolvedValueOnce(
        jsonResponse(200, { total: 1, items: [{ ...USER_LISI, name: "李四改" }] }),
      );

    renderPage();
    await screen.findByText("李四");

    fireEvent.click(screen.getByRole("button", { name: /编\s*辑/ }));
    const modal = await screen.findByRole("dialog");
    const nameInput = within(modal).getByLabelText(/姓名/) as HTMLInputElement;
    typeInto(nameInput, "李四改");
    fireEvent.click(within(modal).getByRole("button", { name: /保\s*存/ }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    const patchCall = fetchMock.mock.calls[1];
    const body = JSON.parse((patchCall[1] as RequestInit).body as string);
    // Field-level assertion (not toMatchObject): exactly one key, only name.
    expect(Object.keys(body).sort()).toEqual(["name"]);
    expect(body.name).toBe("李四改");
    expect(body).not.toHaveProperty("dingtalk_userid");
    expect(body).not.toHaveProperty("dept_path");
    expect(body).not.toHaveProperty("is_active");
  });

  // REWORK-PE-06-INDEP-2 #2 — parameterized coverage for the other three
  // diff paths (dingtalk_userid / dept_path / is_active). Each case
  // touches exactly one field and asserts that the PATCH body contains
  // solely that key with the new value —守 字段级 (not toMatchObject).
  it.each([
    {
      label: "dingtalk_userid",
      newValue: "dingtalk-lisi-NEW",
      expectedBody: { dingtalk_userid: "dingtalk-lisi-NEW" },
    },
    {
      label: "dept_path",
      newValue: "/技术/前端",
      expectedBody: { dept_path: "/技术/前端" },
    },
    {
      label: "is_active",
      newValue: false,
      expectedBody: { is_active: false },
    },
  ])(
    "PATCH excludes unchanged fields when only $label is edited",
    async ({ label, newValue, expectedBody }) => {
      fetchMock
        .mockResolvedValueOnce(
          jsonResponse(200, { total: 1, items: [USER_LISI] }),
        )
        .mockResolvedValueOnce(jsonResponse(200, USER_LISI))
        .mockResolvedValueOnce(
          jsonResponse(200, { total: 1, items: [USER_LISI] }),
        );

      renderPage();
      await screen.findByText("李四");

      fireEvent.click(screen.getByRole("button", { name: /编\s*辑/ }));
      const modal = await screen.findByRole("dialog");

      if (label === "is_active") {
        // Modal hosts a single Switch (status); top-toolbar "显示已停用"
        // is outside the modal so within(modal) scope keeps us correct.
        const switchEl = within(modal).getByRole("switch");
        fireEvent.click(switchEl);
      } else {
        // Form item labels: "钉钉 userid" / "部门路径"
        const labelPattern =
          label === "dingtalk_userid" ? /钉钉 userid/ : /部门路径/;
        const input = within(modal).getByLabelText(
          labelPattern,
        ) as HTMLInputElement;
        typeInto(input, newValue as string);
      }
      fireEvent.click(within(modal).getByRole("button", { name: /保\s*存/ }));

      await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
      const patchCall = fetchMock.mock.calls[1];
      expect(String(patchCall[0])).toBe(
        `/api/v1/ncmu/admin/users/${USER_LISI.id}`,
      );
      expect((patchCall[1] as RequestInit).method).toBe("PATCH");
      const body = JSON.parse((patchCall[1] as RequestInit).body as string);
      // Exactly one key — sole field changed (守 SOP 字段级 assert).
      expect(Object.keys(body).sort()).toEqual(Object.keys(expectedBody));
      expect(body).toEqual(expectedBody);
      // Cross-check the other three fields are absent.
      const otherKeys = ["name", "dingtalk_userid", "dept_path", "is_active"]
        .filter((k) => !(k in expectedBody));
      for (const k of otherKeys) {
        expect(body).not.toHaveProperty(k);
      }
    },
  );

  it("copy UUID button calls navigator.clipboard.writeText", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(200, { total: 1, items: [USER_LISI] }),
    );
    renderPage();
    await screen.findByText("李四");
    fireEvent.click(screen.getByRole("button", { name: /编\s*辑/ }));
    await screen.findByRole("dialog");
    fireEvent.click(screen.getByRole("button", { name: /复\s*制.*UUID/i }));
    expect(clipboardWriteText).toHaveBeenCalledWith(USER_LISI.id);
  });

  it("DELETE on Popconfirm confirm sends DELETE /admin/users/{id}", async () => {
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse(200, { total: 1, items: [USER_LISI] }),
      )
      .mockResolvedValueOnce(emptyResponse(204))
      .mockResolvedValueOnce(jsonResponse(200, { total: 0, items: [] }));

    renderPage();
    await screen.findByText("李四");

    fireEvent.click(screen.getByRole("button", { name: /停\s*用/ }));
    const confirmBtn = await screen.findByRole("button", { name: /确\s*定/ });
    fireEvent.click(confirmBtn);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    const deleteCall = fetchMock.mock.calls[1];
    expect(String(deleteCall[0])).toBe(
      `/api/v1/ncmu/admin/users/${USER_LISI.id}`,
    );
    expect((deleteCall[1] as RequestInit).method).toBe("DELETE");
  });
});

describe("AdminUsersPage — pagination", () => {
  it("page change triggers refetch with page= query", async () => {
    const rows = Array.from({ length: 51 }, (_, i) => ({
      ...USER_LISI,
      id: `aa00${String(i).padStart(4, "0")}-0000-4000-8000-000000000001`.slice(0, 36),
      name: `用户${i + 1}`,
      dingtalk_userid: `dt-${i + 1}`,
    }));
    fetchMock
      .mockResolvedValueOnce(jsonResponse(200, { total: 51, items: rows.slice(0, 50) }))
      .mockResolvedValueOnce(jsonResponse(200, { total: 51, items: rows.slice(50) }));

    renderPage();
    await screen.findByText("用户1");

    const page2Btn = await screen.findByTitle("2");
    await act(async () => {
      fireEvent.click(page2Btn);
    });

    await waitFor(() => {
      const lastCall = fetchMock.mock.calls[fetchMock.mock.calls.length - 1];
      expect(String(lastCall[0])).toMatch(/[?&]page=2/);
    });
  });
});
