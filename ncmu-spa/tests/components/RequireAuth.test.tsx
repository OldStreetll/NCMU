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
};

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/login" element={<div>login-page</div>} />
        <Route
          path="/chat"
          element={
            <RequireAuth>
              <div>chat-page-protected</div>
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
});
