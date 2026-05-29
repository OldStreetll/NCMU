// TASK-PE-05 — AdminTagsPage wiring tests.
//
// Covers wire shape (URL + method + body field names) against backend
// admin/tags/routes.py + key UI flows (list render / create + toast /
// edit + diff-send / delete Popconfirm). Real Response objects in fetch
// mocks (守 SOP 10 in `feedback_tdd_mock_vs_real_api`).
//
// antd v5 仅 2 CJK 字符按钮 autoInsertSpaceInButton 默认开启 — "创建"
// renders as "创 建" — every getByRole({name: /…/}) regex 含 \s* 容错
// (守 feedback_antd_v5_cjk_button_space_pitfall).
//
// Tests live alongside PE-01's layout tests in src/pages/__tests__/ —
// PE-03/04/06 placed AdminUsersPage.test / StaffHomePage.test similarly.

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
  within,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import AdminTagsPage from "@/pages/AdminTagsPage";
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

// ----- fixtures ---------------------------------------------------------- #
const TAG_TECH = {
  id: "11111111-1111-4000-8000-000000000001",
  name: "技术",
  description: "技术领域相关 App",
  app_count: 3,
  user_count: 5,
};

const TAG_HR = {
  id: "11111111-1111-4000-8000-000000000002",
  name: "人事",
  description: null,
  app_count: 0,
  user_count: 0,
};

function renderPage() {
  return render(
    <MemoryRouter>
      <AdminTagsPage />
    </MemoryRouter>,
  );
}

function typeInto(input: HTMLElement, value: string) {
  fireEvent.change(input, { target: { value } });
}

// ----- tests ------------------------------------------------------------- #

describe("AdminTagsPage — wire shape", () => {
  it("loads /api/v1/ncmu/admin/tags on mount with Bearer auth", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, [TAG_TECH, TAG_HR]));
    renderPage();
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[0];
    // Path is exact-match — no query string on the v1 endpoint.
    expect(String(url)).toBe("/api/v1/ncmu/admin/tags");
    expect((init as RequestInit).method ?? "GET").toBe("GET");
    expect(
      ((init as RequestInit).headers as Record<string, string>)[
        "Authorization"
      ],
    ).toBe("Bearer test-jwt-admin");
  });

  it("renders tag rows with name/description/app_count/user_count", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, [TAG_TECH, TAG_HR]));
    renderPage();
    expect(await screen.findByText("技术")).toBeInTheDocument();
    expect(screen.getByText("人事")).toBeInTheDocument();
    expect(screen.getByText("技术领域相关 App")).toBeInTheDocument();
    // app_count + user_count rendered as plain numbers
    expect(screen.getByText("3")).toBeInTheDocument(); // TAG_TECH.app_count
    expect(screen.getByText("5")).toBeInTheDocument(); // TAG_TECH.user_count
  });
});

describe("AdminTagsPage — create flow", () => {
  it("opens modal on 新建标签 button and POSTs name to /admin/tags", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(200, []))
      .mockResolvedValueOnce(
        jsonResponse(201, {
          id: "abcdef12-0000-4000-8000-000000000abc",
          name: "新标签",
          description: null,
          app_count: 0,
          user_count: 0,
        }),
      )
      .mockResolvedValueOnce(jsonResponse(200, []));

    renderPage();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole("button", { name: /新\s*建\s*标\s*签/ }));
    const modal = await screen.findByRole("dialog");
    const nameInput = within(modal).getByLabelText(/名称/);
    typeInto(nameInput, "新标签");
    // antd v5 CJK button autoInsertSpace 陷阱 — \s* 容错
    fireEvent.click(within(modal).getByRole("button", { name: /创\s*建/ }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    const postCall = fetchMock.mock.calls[1];
    expect(String(postCall[0])).toBe("/api/v1/ncmu/admin/tags");
    expect((postCall[1] as RequestInit).method).toBe("POST");
    const body = JSON.parse((postCall[1] as RequestInit).body as string);
    // Field-level shape: name set; description null (form unset → null)
    expect(body.name).toBe("新标签");
    expect(body.description).toBeNull();
  });

  it("shows success toast with created tag name after 201", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(200, []))
      .mockResolvedValueOnce(
        jsonResponse(201, {
          id: "abcdef12-0000-4000-8000-000000000abc",
          name: "客服",
          description: "客服团队",
          app_count: 0,
          user_count: 0,
        }),
      )
      .mockResolvedValueOnce(jsonResponse(200, []));

    renderPage();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: /新\s*建\s*标\s*签/ }));
    const modal = await screen.findByRole("dialog");
    typeInto(within(modal).getByLabelText(/名称/), "客服");
    fireEvent.click(within(modal).getByRole("button", { name: /创\s*建/ }));

    // antd message.success renders as plain DOM text — find by partial match
    const toast = await screen.findByText(/已创建.*客服/);
    expect(toast).toBeInTheDocument();
  });

  it("surfaces 409 duplicate-name error (code 1012) in modal alert", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(200, []))
      .mockResolvedValueOnce(
        jsonResponse(409, {
          detail: { code: 1012, message: "tag '技术' already exists" },
        }),
      );

    renderPage();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: /新\s*建\s*标\s*签/ }));
    const modal = await screen.findByRole("dialog");
    typeInto(within(modal).getByLabelText(/名称/), "技术");
    fireEvent.click(within(modal).getByRole("button", { name: /创\s*建/ }));

    await waitFor(() => {
      expect(
        within(modal).queryByText(/already exists/i),
      ).toBeInTheDocument();
    });
  });
});

describe("AdminTagsPage — edit + diff-send", () => {
  it("opens edit modal with prefilled name and PATCHes on save", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(200, [TAG_TECH]))
      .mockResolvedValueOnce(jsonResponse(200, { ...TAG_TECH, name: "技术改" }))
      .mockResolvedValueOnce(
        jsonResponse(200, [{ ...TAG_TECH, name: "技术改" }]),
      );

    renderPage();
    await screen.findByText("技术");

    fireEvent.click(screen.getByRole("button", { name: /编\s*辑/ }));
    const modal = await screen.findByRole("dialog");
    const nameInput = within(modal).getByLabelText(/名称/) as HTMLInputElement;
    expect(nameInput.value).toBe("技术");
    typeInto(nameInput, "技术改");
    fireEvent.click(within(modal).getByRole("button", { name: /保\s*存/ }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    const patchCall = fetchMock.mock.calls[1];
    expect(String(patchCall[0])).toBe(
      `/api/v1/ncmu/admin/tags/${TAG_TECH.id}`,
    );
    expect((patchCall[1] as RequestInit).method).toBe("PATCH");
    const body = JSON.parse((patchCall[1] as RequestInit).body as string);
    expect(body.name).toBe("技术改");
  });

  it("PATCH excludes unchanged fields when only name is edited", async () => {
    // 守 PE-06 REWORK-INDEP #4 — editing only name yields a PATCH body
    // with exactly { name }; description stays out because it didn't
    // change. Backend ``exclude_unset=True`` won't touch it.
    fetchMock
      .mockResolvedValueOnce(jsonResponse(200, [TAG_TECH]))
      .mockResolvedValueOnce(jsonResponse(200, { ...TAG_TECH, name: "技术改" }))
      .mockResolvedValueOnce(
        jsonResponse(200, [{ ...TAG_TECH, name: "技术改" }]),
      );

    renderPage();
    await screen.findByText("技术");

    fireEvent.click(screen.getByRole("button", { name: /编\s*辑/ }));
    const modal = await screen.findByRole("dialog");
    typeInto(within(modal).getByLabelText(/名称/), "技术改");
    fireEvent.click(within(modal).getByRole("button", { name: /保\s*存/ }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    const patchCall = fetchMock.mock.calls[1];
    const body = JSON.parse((patchCall[1] as RequestInit).body as string);
    // Field-level: exactly one key (守 SOP 字段级 assert).
    expect(Object.keys(body).sort()).toEqual(["name"]);
    expect(body.name).toBe("技术改");
    expect(body).not.toHaveProperty("description");
  });

  // 守 PE-06 REWORK-INDEP-2 #2 — parameterized coverage for the
  // description diff path; isolates the RHS ``||`` symmetry that handles
  // both null and empty-string the same way.
  it.each([
    {
      label: "description from non-null to new value",
      seed: { ...TAG_TECH },
      newValue: "新的描述",
      expectedBody: { description: "新的描述" },
    },
    {
      label: "description from null to a value",
      seed: { ...TAG_HR }, // TAG_HR.description = null
      newValue: "新加描述",
      expectedBody: { description: "新加描述" },
    },
  ])(
    "PATCH excludes unchanged name when only $label",
    async ({ seed, newValue, expectedBody }) => {
      fetchMock
        .mockResolvedValueOnce(jsonResponse(200, [seed]))
        .mockResolvedValueOnce(
          jsonResponse(200, { ...seed, description: newValue }),
        )
        .mockResolvedValueOnce(jsonResponse(200, [seed]));

      renderPage();
      await screen.findByText(seed.name);

      fireEvent.click(screen.getByRole("button", { name: /编\s*辑/ }));
      const modal = await screen.findByRole("dialog");
      const descInput = within(modal).getByLabelText(
        /描述/,
      ) as HTMLTextAreaElement;
      typeInto(descInput, newValue);
      fireEvent.click(within(modal).getByRole("button", { name: /保\s*存/ }));

      await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
      const patchCall = fetchMock.mock.calls[1];
      expect(String(patchCall[0])).toBe(
        `/api/v1/ncmu/admin/tags/${seed.id}`,
      );
      expect((patchCall[1] as RequestInit).method).toBe("PATCH");
      const body = JSON.parse((patchCall[1] as RequestInit).body as string);
      expect(Object.keys(body).sort()).toEqual(Object.keys(expectedBody));
      expect(body).toEqual(expectedBody);
      // Cross-check the other field is absent.
      expect(body).not.toHaveProperty("name");
    },
  );

  it("EditModal 0-change save shows 无变更 toast and skips PATCH wire", async () => {
    // 守 PE-06 EditModal early-return — clicking 保存 without touching any
    // field must NOT fire a wire call; the diff-send guard short-circuits.
    fetchMock.mockResolvedValueOnce(jsonResponse(200, [TAG_TECH]));

    renderPage();
    await screen.findByText("技术");

    fireEvent.click(screen.getByRole("button", { name: /编\s*辑/ }));
    await screen.findByRole("dialog");
    // Click 保存 without changing anything — diff-send guard fires.
    fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));

    const toast = await screen.findByText(/无变更/);
    expect(toast).toBeInTheDocument();
    // Only the initial list call — no PATCH was sent.
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("AdminTagsPage — delete + cascade verbiage", () => {
  it("Popconfirm description surfaces app_count + user_count cascade preview", async () => {
    // 守 plan §3 PE-05 Step 4 字面"将解除 X 个 App 与 Y 个用户的绑定"
    // — admin must see the cascade blast radius before confirming.
    fetchMock.mockResolvedValueOnce(jsonResponse(200, [TAG_TECH]));

    renderPage();
    await screen.findByText("技术");
    fireEvent.click(screen.getByRole("button", { name: /删\s*除/ }));

    // Popconfirm description contains the counts from TAG_TECH (3/5).
    const cascadeHint = await screen.findByText(
      /将解除\s*3\s*个\s*App\s*与\s*5\s*个用户的绑定/,
    );
    expect(cascadeHint).toBeInTheDocument();
  });

  it("confirming delete sends DELETE /admin/tags/{id} + refetches", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(200, [TAG_TECH]))
      .mockResolvedValueOnce(emptyResponse(204))
      .mockResolvedValueOnce(jsonResponse(200, []));

    renderPage();
    await screen.findByText("技术");

    fireEvent.click(screen.getByRole("button", { name: /删\s*除/ }));
    const confirmBtn = await screen.findByRole("button", { name: /确\s*定/ });
    fireEvent.click(confirmBtn);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    const deleteCall = fetchMock.mock.calls[1];
    expect(String(deleteCall[0])).toBe(
      `/api/v1/ncmu/admin/tags/${TAG_TECH.id}`,
    );
    expect((deleteCall[1] as RequestInit).method).toBe("DELETE");
  });

  it("delete 404 surfaces toast error (code 1013)", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(200, [TAG_TECH]))
      .mockResolvedValueOnce(
        jsonResponse(404, {
          detail: { code: 1013, message: "tag not found" },
        }),
      );

    renderPage();
    await screen.findByText("技术");

    fireEvent.click(screen.getByRole("button", { name: /删\s*除/ }));
    const confirmBtn = await screen.findByRole("button", { name: /确\s*定/ });
    fireEvent.click(confirmBtn);

    const toast = await screen.findByText(/tag not found/i);
    expect(toast).toBeInTheDocument();
  });
});
