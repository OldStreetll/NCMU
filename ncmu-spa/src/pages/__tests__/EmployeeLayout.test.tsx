import { beforeEach, describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { EmployeeLayout } from "@/layouts/EmployeeLayout";
import { setAuth, type AuthUser } from "@/lib/auth";

const sampleEmployee: AuthUser = {
  id: "a0000001-0000-4000-8000-000000000001",
  name: "张三",
  dept_path: "/HR/招聘组",
  is_active: true,
  is_admin: false,
};

function renderLayout(initialPath = "/staff") {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <EmployeeLayout>
        <div data-testid="layout-content">CONTENT</div>
      </EmployeeLayout>
    </MemoryRouter>,
  );
}

describe("<EmployeeLayout>", () => {
  beforeEach(() => {
    sessionStorage.clear();
    setAuth("jwt-token-employee", sampleEmployee);
  });

  it("renders header with user name, employee role badge, logout button, and children", () => {
    renderLayout();
    // user name appears in the header
    expect(screen.getByText(/张三/)).toBeInTheDocument();
    // 员工 role badge — distinguishes employee vs admin layout
    expect(screen.getByText("员工")).toBeInTheDocument();
    // logout button (antd renders Button > span > text, getByRole matches accessible name)
    expect(screen.getByRole("button", { name: /退出/ })).toBeInTheDocument();
    // children render inside Content
    expect(screen.getByTestId("layout-content")).toBeInTheDocument();
  });

  it("sidebar shows 2 menu items: 我的 App + 个人 KB", () => {
    renderLayout();
    expect(screen.getByText("我的 App")).toBeInTheDocument();
    expect(screen.getByText("个人 KB")).toBeInTheDocument();
  });
});
