// TASK-LOGIN-2 — LoginPage 钉钉扫码登录按钮 wiring tests.
//
// Covers the Pattern B step ① flow: clicking「钉钉扫码登录」issues
// GET /api/v1/ncmu/auth/dingtalk/login and full-page-redirects the browser
// to the returned authorize_url; a failed fetch surfaces message.error and
// does NOT navigate away.
//
// Mock strategy: mock the GLOBAL fetch with real Response objects (守 SOP 10
// in `feedback_tdd_mock_vs_real_api` — same convention as AdminUsersPage.test).
// This exercises the real api() helper end-to-end (BASE prefix + error
// envelope parsing) rather than stubbing api() and trusting a hand-rolled
// shape. window.location is replaced with a writable stub so the redirect
// assignment is observable (and doesn't trip jsdom's "navigation not
// implemented").

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
import { message } from "antd";
import LoginPage from "@/pages/LoginPage";

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

// Replace window.location with a writable stub so we can read href back
// after the redirect without jsdom throwing "Not implemented: navigation".
let locationStub: { href: string };

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  locationStub = { href: "" };
  Object.defineProperty(window, "location", {
    configurable: true,
    writable: true,
    value: locationStub as unknown as Location,
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function renderPage() {
  return render(
    <MemoryRouter>
      <LoginPage />
    </MemoryRouter>,
  );
}

// LoginPage's mount effect GETs /dev/users — give it an empty list so the
// dev-login Select renders without noise. Tests that care about the DingTalk
// call read the LAST fetch call, not the [0] mount call.
function mockDevUsersEmpty() {
  fetchMock.mockResolvedValueOnce(jsonResponse(200, []));
}

// ----- tests -------------------------------------------------------------- #

describe("LoginPage — 钉钉扫码登录 button (AC#1)", () => {
  it("renders the 钉钉扫码登录 button", async () => {
    mockDevUsersEmpty();
    renderPage();
    expect(
      await screen.findByRole("button", { name: /钉钉扫码登录/ }),
    ).toBeInTheDocument();
  });

  it("clicking issues GET /api/v1/ncmu/auth/dingtalk/login (skipAuth → no Authorization header)", async () => {
    mockDevUsersEmpty();
    fetchMock.mockResolvedValueOnce(
      jsonResponse(200, {
        authorize_url: "https://login.dingtalk.com/oauth2/auth?state=abc",
        state: "abc",
      }),
    );
    renderPage();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    fireEvent.click(
      await screen.findByRole("button", { name: /钉钉扫码登录/ }),
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const [url, init] = fetchMock.mock.calls[1];
    expect(String(url)).toBe("/api/v1/ncmu/auth/dingtalk/login");
    expect((init as RequestInit).method).toBe("GET");
    // skipAuth: no JWT exists → api() must NOT attach an Authorization header.
    const headers = (init as RequestInit).headers as Record<string, string>;
    expect(headers["Authorization"]).toBeUndefined();
  });

  it("full-page redirects the browser to authorize_url on success", async () => {
    const authorizeUrl =
      "https://login.dingtalk.com/oauth2/auth?redirect_uri=cb&state=xyz";
    mockDevUsersEmpty();
    fetchMock.mockResolvedValueOnce(
      jsonResponse(200, { authorize_url: authorizeUrl, state: "xyz" }),
    );
    renderPage();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    fireEvent.click(
      await screen.findByRole("button", { name: /钉钉扫码登录/ }),
    );

    await waitFor(() => expect(locationStub.href).toBe(authorizeUrl));
  });

  it("surfaces message.error and does NOT redirect when /login fails", async () => {
    const errorSpy = vi
      .spyOn(message, "error")
      .mockImplementation(() => ({}) as never);
    mockDevUsersEmpty();
    fetchMock.mockResolvedValueOnce(
      jsonResponse(502, {
        detail: { code: 1323, message: "上游钉钉不可用" },
      }),
    );
    renderPage();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    fireEvent.click(
      await screen.findByRole("button", { name: /钉钉扫码登录/ }),
    );

    await waitFor(() => expect(errorSpy).toHaveBeenCalled());
    // Error path must not navigate the browser away.
    expect(locationStub.href).toBe("");
    const msg = String(errorSpy.mock.calls[0][0]);
    expect(msg).toContain("502");
  });
});
