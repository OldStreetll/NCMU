import { beforeEach, describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { RequireAuth } from "@/components/RequireAuth";
import { setAuth, type AuthUser } from "@/lib/auth";

const sampleUser: AuthUser = {
  id: "a0000001-0000-4000-8000-000000000001",
  name: "张三",
  dept_path: "/HR/招聘组",
  is_active: true,
  is_admin: false,
};

const adminUser: AuthUser = { ...sampleUser, is_admin: true };

function renderAt(path: string, requireAdmin = false) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/login" element={<div>login-page</div>} />
        <Route path="/" element={<div>home-page</div>} />
        <Route
          path="/chat"
          element={
            <RequireAuth>
              <div>chat-page-protected</div>
            </RequireAuth>
          }
        />
        <Route
          path="/admin"
          element={
            <RequireAuth requireAdmin={requireAdmin}>
              <div data-testid="admin-page-protected">admin-page-protected</div>
            </RequireAuth>
          }
        />
      </Routes>
    </MemoryRouter>,
  );
}

describe("<RequireAuth>", () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it("redirects unauthenticated visit to /login", () => {
    renderAt("/chat");
    expect(screen.getByText("login-page")).toBeInTheDocument();
    expect(screen.queryByText("chat-page-protected")).toBeNull();
  });

  it("renders children when authenticated", () => {
    setAuth("jwt-token-abc", sampleUser);
    renderAt("/chat");
    expect(screen.getByText("chat-page-protected")).toBeInTheDocument();
    expect(screen.queryByText("login-page")).toBeNull();
  });

  // TASK-PC2-E AC#4 — requireAdmin prop adds an admin-only gate on top
  // of the existing authenticated check. r3 I-INDEP2-2: source-of-truth
  // is the sessionStorage-backed AuthUser.is_admin (the SPA does not
  // decode the JWT to derive admin status).

  it("requireAdmin=true + admin user → renders children", () => {
    setAuth("jwt-token-admin", adminUser);
    renderAt("/admin", true);
    expect(screen.getByTestId("admin-page-protected")).toBeInTheDocument();
    expect(screen.queryByText("home-page")).toBeNull();
  });

  it("requireAdmin=true + non-admin user → redirects to / (no admin children rendered)", () => {
    setAuth("jwt-token-abc", sampleUser);
    renderAt("/admin", true);
    expect(screen.getByText("home-page")).toBeInTheDocument();
    // DOM 反断言：non-admin 路径下 admin-* testid 必须 0 渲染（守
    // feedback_status_enum_cross_stack_sync 反证范式）。
    expect(screen.queryByTestId("admin-page-protected")).toBeNull();
  });

  it("requireAdmin defaulted (omitted) → does not consult is_admin (non-admin still renders)", () => {
    setAuth("jwt-token-abc", sampleUser);
    renderAt("/admin", false);
    expect(screen.getByTestId("admin-page-protected")).toBeInTheDocument();
  });

  it("requireAdmin=true + unauthenticated → still redirects to /login (auth check runs first)", () => {
    renderAt("/admin", true);
    expect(screen.getByText("login-page")).toBeInTheDocument();
    expect(screen.queryByTestId("admin-page-protected")).toBeNull();
  });
});
