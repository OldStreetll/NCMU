import { beforeEach, describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { RoleGuard } from "@/components/guard/RoleGuard";
import { setAuth, type AuthUser } from "@/lib/auth";

const sampleEmployee: AuthUser = {
  id: "a0000001-0000-4000-8000-000000000001",
  name: "张三",
  dept_path: "/HR/招聘组",
  is_active: true,
  is_admin: false,
};

const sampleAdmin: AuthUser = { ...sampleEmployee, name: "管理员", is_admin: true };

// Helper to surface current location.pathname + location.search from inside
// a route, so we can assert redirect targets (incl. the returnTo query).
function LocationProbe() {
  const location = useLocation();
  return (
    <div data-testid="location-probe">
      {location.pathname}
      {location.search}
    </div>
  );
}

function renderAt(initialPath: string, requiredRole: "admin" | "employee" = "admin") {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route
          path="/login"
          element={
            <div>
              login-page
              <LocationProbe />
            </div>
          }
        />
        <Route
          path="/staff"
          element={
            <div>
              staff-home
              <LocationProbe />
            </div>
          }
        />
        <Route
          path="/admin"
          element={
            <RoleGuard requiredRole={requiredRole}>
              <div data-testid="admin-content">admin-content</div>
            </RoleGuard>
          }
        />
      </Routes>
    </MemoryRouter>,
  );
}

describe("<RoleGuard>", () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it("employee 撞 admin 路由 → redirect /staff (no admin content rendered)", () => {
    setAuth("jwt-token-employee", sampleEmployee);
    renderAt("/admin", "admin");
    expect(screen.getByText("staff-home")).toBeInTheDocument();
    expect(screen.queryByTestId("admin-content")).toBeNull();
    expect(screen.getByTestId("location-probe").textContent).toBe("/staff");
  });

  it("admin 撞 admin 路由 → render children", () => {
    setAuth("jwt-token-admin", sampleAdmin);
    renderAt("/admin", "admin");
    expect(screen.getByTestId("admin-content")).toBeInTheDocument();
    expect(screen.queryByText("staff-home")).toBeNull();
    expect(screen.queryByText("login-page")).toBeNull();
  });

  it("未登录 撞 admin 路由 → redirect /login?returnTo=%2Fadmin", () => {
    renderAt("/admin", "admin");
    expect(screen.getByText("login-page")).toBeInTheDocument();
    expect(screen.queryByTestId("admin-content")).toBeNull();
    // returnTo is URL-encoded `/admin` → `%2Fadmin`
    expect(screen.getByTestId("location-probe").textContent).toBe(
      "/login?returnTo=%2Fadmin",
    );
  });
});
