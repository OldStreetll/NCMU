// TASK-LOGIN-2 — DingtalkCallbackPage 回调落地页 wiring tests.
//
// Covers Pattern B step ③: read code+state from the URL → exchange via
// GET /api/v1/ncmu/auth/dingtalk/callback → setAuth + role-based navigate on
// success; message.error + navigate("/login") (no setAuth) on ApiError; and
// the missing-params guard.
//
// Mock strategy (same as LoginPage.test): mock the GLOBAL fetch with real
// Response objects so the real api() + setAuth run end-to-end (守 SOP 10).
// `setAuth` writes sessionStorage, so AC#2's "setAuth was called" is asserted
// by reading the real ncmu_jwt key back (tests/setup.ts clears it afterEach).
// `useNavigate` is the only react-router export we stub (to read the target);
// `useSearchParams` stays REAL, fed by MemoryRouter initialEntries.

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
import { StrictMode } from "react";
import { render, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { message } from "antd";
import DingtalkCallbackPage from "@/pages/DingtalkCallbackPage";
import { getJwt, getUser } from "@/lib/auth";

// ----- react-router partial mock: only useNavigate is stubbed -------------- #
const navigateMock = vi.fn();
vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return { ...actual, useNavigate: () => navigateMock };
});

// ----- jsdom polyfills antd 5 relies on ----------------------------------- #
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

const fetchMock = vi.fn();
// antd 5 message.error is an overloaded singleton method; mirror the
// AppKbContentPanel.test typing (MockInstance<typeof message.error> +
// `() => never` returner, assignable by parameter contravariance).
let errorSpy: MockInstance<typeof message.error>;

beforeEach(() => {
  fetchMock.mockReset();
  navigateMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  errorSpy = vi.spyOn(message, "error").mockImplementation(() => ({}) as never);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// ----- fixtures ----------------------------------------------------------- #
const ADMIN_USER = {
  id: "a0000001-0000-4000-8000-000000000001",
  name: "管理员",
  dept_path: "/技术",
  is_active: true,
  is_admin: true,
};

const STAFF_USER = {
  id: "b0000001-0000-4000-8000-000000000002",
  name: "员工",
  dept_path: "/业务",
  is_active: true,
  is_admin: false,
};

function renderAt(search: string) {
  return render(
    <MemoryRouter initialEntries={[`/auth/dingtalk/callback${search}`]}>
      <DingtalkCallbackPage />
    </MemoryRouter>,
  );
}

// REWORK-LOGIN-2-INDEP: render inside <StrictMode> so React 18 dev-mode
// double-invokes mount→cleanup→mount. This reproduces the swallowed-success
// bug the 12 base tests (no StrictMode wrapper) could not catch.
function renderAtStrict(search: string) {
  return render(
    <StrictMode>
      <MemoryRouter initialEntries={[`/auth/dingtalk/callback${search}`]}>
        <DingtalkCallbackPage />
      </MemoryRouter>
    </StrictMode>,
  );
}

// ----- tests -------------------------------------------------------------- #

describe("DingtalkCallbackPage — success path (AC#2)", () => {
  it("exchanges code+state and GETs /api/v1/ncmu/auth/dingtalk/callback with both params", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(200, {
        jwt: "ding-jwt-staff",
        user: STAFF_USER,
        expires_at: "2026-06-04T00:00:00Z",
      }),
    );
    renderAt("?code=CODE123&state=STATE456");

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [url, init] = fetchMock.mock.calls[0];
    const u = new URL(String(url), "http://localhost");
    expect(u.pathname).toBe("/api/v1/ncmu/auth/dingtalk/callback");
    expect(u.searchParams.get("code")).toBe("CODE123");
    expect(u.searchParams.get("state")).toBe("STATE456");
    expect((init as RequestInit).method).toBe("GET");
    // skipAuth: no JWT before login → no Authorization header.
    expect(
      ((init as RequestInit).headers as Record<string, string>)["Authorization"],
    ).toBeUndefined();
  });

  it("stores the JWT via setAuth and navigates an employee to /staff", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(200, {
        jwt: "ding-jwt-staff",
        user: STAFF_USER,
        expires_at: "2026-06-04T00:00:00Z",
      }),
    );
    renderAt("?code=CODE123&state=STATE456");

    await waitFor(() =>
      expect(navigateMock).toHaveBeenCalledWith("/staff", { replace: true }),
    );
    // setAuth actually ran (real impl → sessionStorage).
    expect(getJwt()).toBe("ding-jwt-staff");
    expect(getUser()?.id).toBe(STAFF_USER.id);
    expect(errorSpy).not.toHaveBeenCalled();
  });

  it("navigates an admin to /admin", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(200, {
        jwt: "ding-jwt-admin",
        user: ADMIN_USER,
        expires_at: "2026-06-04T00:00:00Z",
      }),
    );
    renderAt("?code=C&state=S");

    await waitFor(() =>
      expect(navigateMock).toHaveBeenCalledWith("/admin", { replace: true }),
    );
    expect(getJwt()).toBe("ding-jwt-admin");
  });

  // REWORK-LOGIN-2-INDEP — Important #1 regression guard. Under StrictMode the
  // exchangedRef one-shot guard must keep the callback fetch to exactly ONE
  // (single-use OAuth code → no double exchange) AND the success result must
  // still be adopted (setAuth + navigate), not swallowed by a stale cancelled
  // flag. Pre-fix this test stuck on Spin and timed out the navigate waitFor.
  it("StrictMode double-mount: fires exactly one fetch and still setAuth+navigate", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(200, {
        jwt: "ding-jwt-staff",
        user: STAFF_USER,
        expires_at: "2026-06-04T00:00:00Z",
      }),
    );
    renderAtStrict("?code=CODE123&state=STATE456");

    await waitFor(() =>
      expect(navigateMock).toHaveBeenCalledWith("/staff", { replace: true }),
    );
    // Single-use code: the dev double-mount must NOT trigger a second exchange.
    expect(fetchMock).toHaveBeenCalledTimes(1);
    // Success was adopted (real setAuth → sessionStorage), not swallowed.
    expect(getJwt()).toBe("ding-jwt-staff");
    expect(getUser()?.id).toBe(STAFF_USER.id);
  });
});

describe("DingtalkCallbackPage — error path (AC#3)", () => {
  it("403 未开通 (code 1322) → message.error + navigate('/login'), no setAuth", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(403, {
        detail: { code: 1322, message: "用户未开通钉钉登录" },
      }),
    );
    renderAt("?code=C&state=S");

    await waitFor(() => expect(errorSpy).toHaveBeenCalled());
    expect(navigateMock).toHaveBeenCalledWith("/login", { replace: true });
    // No session was established.
    expect(getJwt()).toBeNull();
    expect(getUser()).toBeNull();
    expect(String(errorSpy.mock.calls[0][0])).toContain("403");
  });

  it("502 上游 (code 1323) → message.error + navigate('/login'), no setAuth", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(502, {
        detail: { code: 1323, message: "上游钉钉不可用" },
      }),
    );
    renderAt("?code=C&state=S");

    await waitFor(() => expect(errorSpy).toHaveBeenCalled());
    expect(navigateMock).toHaveBeenCalledWith("/login", { replace: true });
    expect(getJwt()).toBeNull();
    expect(String(errorSpy.mock.calls[0][0])).toContain("502");
  });
});

describe("DingtalkCallbackPage — missing-params guard", () => {
  it("missing code → no fetch, message.error + navigate('/login')", async () => {
    renderAt("?state=ONLYSTATE");

    await waitFor(() => expect(navigateMock).toHaveBeenCalledWith("/login", { replace: true }));
    expect(fetchMock).not.toHaveBeenCalled();
    expect(errorSpy).toHaveBeenCalled();
    expect(getJwt()).toBeNull();
  });

  it("missing state → no fetch, message.error + navigate('/login')", async () => {
    renderAt("?code=ONLYCODE");

    await waitFor(() => expect(navigateMock).toHaveBeenCalledWith("/login", { replace: true }));
    expect(fetchMock).not.toHaveBeenCalled();
    expect(errorSpy).toHaveBeenCalled();
  });
});
