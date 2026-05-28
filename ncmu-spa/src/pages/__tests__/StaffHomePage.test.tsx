import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { StaffHomePage } from "@/pages/StaffHomePage";
import * as api from "@/lib/api";

// TASK-PE-03: PathC (Boss 2026-05-28) — fetchApps is the new SPA wrapper
// for `GET /api/v1/ncmu/apps` added to lib/api.ts (mirrors listMyApplications
// one-liner). useQuery + QueryClientProvider were *not* added (project never
// adopted @tanstack/react-query — see useWorkflowRuns.ts:8 head comment);
// StaffHomePage uses the project's useState + useEffect + api() helper
// pattern instead. `icon` was a plan §3 hallucination — AppOut has no such
// field — so cards fall back to a mode-based emoji.
vi.mock("@/lib/api", async (importActual) => {
  const actual = await importActual<typeof import("@/lib/api")>();
  return { ...actual, fetchApps: vi.fn() };
});

const fixtures = [
  {
    id: "uuid-a",
    name: "报销助手",
    description: "财务报销助手",
    mode: "chat",
    type: "kb_qa" as const,
  },
  {
    id: "uuid-b",
    name: "报表生成",
    description: "财务报表",
    mode: "workflow",
    type: "kb_qa" as const,
  },
  {
    id: "uuid-c",
    name: "[KB] 产品手册",
    description: "产品 KB",
    mode: "chat",
    type: "kb_qa" as const,
  },
];

function renderPage() {
  return render(
    <MemoryRouter>
      <StaffHomePage />
    </MemoryRouter>,
  );
}

describe("<StaffHomePage>", () => {
  beforeEach(() => {
    vi.mocked(api.fetchApps).mockResolvedValue(fixtures);
  });

  it("默认 tab「全部」显示 3 个 App 卡片", async () => {
    renderPage();
    expect(await screen.findByText("报销助手")).toBeInTheDocument();
    expect(screen.getByText("报表生成")).toBeInTheDocument();
    expect(screen.getByText("[KB] 产品手册")).toBeInTheDocument();
  });

  it("切到「工作流 App」tab 只显示 mode=workflow", async () => {
    renderPage();
    await screen.findByText("报表生成");
    fireEvent.click(screen.getByRole("tab", { name: "工作流 App" }));
    await waitFor(() => {
      expect(screen.queryByText("报销助手")).not.toBeInTheDocument();
    });
    expect(screen.getByText("报表生成")).toBeInTheDocument();
  });

  it("切到「KB」tab 只显示 [KB] 前缀", async () => {
    renderPage();
    await screen.findByText("[KB] 产品手册");
    fireEvent.click(screen.getByRole("tab", { name: "KB" }));
    await waitFor(() => {
      expect(screen.queryByText("报表生成")).not.toBeInTheDocument();
    });
    expect(screen.getByText("[KB] 产品手册")).toBeInTheDocument();
  });

  it("Empty state 显示「这个分类下你暂无可访问 App」", async () => {
    vi.mocked(api.fetchApps).mockResolvedValue([]);
    renderPage();
    expect(
      await screen.findByText(/这个分类下你暂无可访问 App/),
    ).toBeInTheDocument();
  });

  it("Card 链接按 mode dispatch 到对应路径", async () => {
    renderPage();
    await screen.findByText("报销助手");
    // mode=chat → /chat?app_id=<id>
    expect(screen.getByText("报销助手").closest("a")).toHaveAttribute(
      "href",
      "/chat?app_id=uuid-a",
    );
    // mode=workflow → /apps/<id>/workflow
    expect(screen.getByText("报表生成").closest("a")).toHaveAttribute(
      "href",
      "/apps/uuid-b/workflow",
    );
  });
});
