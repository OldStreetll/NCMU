import { beforeEach, describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AdminLayout } from "@/layouts/AdminLayout";
import { setAuth, type AuthUser } from "@/lib/auth";

const sampleAdmin: AuthUser = {
  id: "a0000099-0000-4000-8000-000000000099",
  name: "管理员",
  dept_path: "/IT/管理",
  is_active: true,
  is_admin: true,
};

function renderLayout(initialPath = "/admin") {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <AdminLayout>
        <div data-testid="layout-content">CONTENT</div>
      </AdminLayout>
    </MemoryRouter>,
  );
}

describe("<AdminLayout>", () => {
  beforeEach(() => {
    sessionStorage.clear();
    setAuth("jwt-token-admin", sampleAdmin);
  });

  it("renders header with admin role badge, user name, logout button, and children", () => {
    renderLayout();
    expect(screen.getByText(/管理员/)).toBeInTheDocument();
    // admin role badge distinguishes admin vs employee layout
    expect(screen.getByText("admin")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /退出/ })).toBeInTheDocument();
    expect(screen.getByTestId("layout-content")).toBeInTheDocument();
  });

  it("sidebar shows 8 internal menu items + 2 external links (Dify + FastGPT)", () => {
    renderLayout();
    // 8 internal menu labels per plan §8 line 350-359
    expect(screen.getByText("首页")).toBeInTheDocument();
    expect(screen.getByText("标签管理")).toBeInTheDocument();
    expect(screen.getByText("用户管理")).toBeInTheDocument();
    expect(screen.getByText("App 管理")).toBeInTheDocument();
    expect(screen.getByText("外部 KB")).toBeInTheDocument();
    expect(screen.getByText("个人 KB")).toBeInTheDocument();
    expect(screen.getByText("DSL 导入")).toBeInTheDocument();
    expect(screen.getByText("DSL 导出")).toBeInTheDocument();
    // 2 external links open in new tab (target="_blank")
    const difyLink = screen.getByRole("link", { name: /跳 Dify/ });
    const fastGptLink = screen.getByRole("link", { name: /跳 FastGPT/ });
    expect(difyLink).toHaveAttribute("target", "_blank");
    expect(fastGptLink).toHaveAttribute("target", "_blank");
  });
});
