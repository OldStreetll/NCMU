import { render, screen } from "@testing-library/react";
import { beforeAll, describe, expect, it } from "vitest";
import { AgentThoughtTimeline } from "@/components/workflow/AgentThoughtTimeline";
import type { AgentThoughtData } from "@/lib/sse-types";

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

describe("AgentThoughtTimeline", () => {
  it("(a) empty thoughts renders placeholder", () => {
    render(<AgentThoughtTimeline thoughts={[]} />);

    expect(screen.getByTestId("agent-thought-empty")).toBeTruthy();
    expect(screen.getByText("暂无思考")).toBeTruthy();
    expect(screen.queryByTestId("agent-thought-list")).toBeNull();
  });

  it("(b) 2+ thoughts render 2 timeline rows", () => {
    const thoughts: AgentThoughtData[] = [
      { thought: "先查知识库" },
      { thought: "然后总结答案" },
      { thought: "最后输出" },
    ];

    render(<AgentThoughtTimeline thoughts={thoughts} />);

    const rows = screen.getAllByTestId("agent-thought-row");
    expect(rows).toHaveLength(3);
    expect(screen.getByText("先查知识库")).toBeTruthy();
    expect(screen.getByText("然后总结答案")).toBeTruthy();
    expect(screen.getByText("最后输出")).toBeTruthy();
  });

  it("(c) tool_name + observation fields render when present", () => {
    const thoughts: AgentThoughtData[] = [
      {
        thought: "需要查询天气",
        tool_name: "weather_api",
        observation: "今日多云转晴",
      },
    ];

    render(<AgentThoughtTimeline thoughts={thoughts} />);

    const tool = screen.getByTestId("agent-thought-tool");
    expect(tool.textContent).toContain("weather_api");
    expect(tool.className).toMatch(/ant-tag-purple/);

    const obs = screen.getByTestId("agent-thought-observation");
    expect(obs.textContent).toContain("今日多云转晴");
  });

  it("(d) thoughts without tool_name / observation only render thought text", () => {
    const thoughts: AgentThoughtData[] = [{ thought: "纯思考无工具" }];

    render(<AgentThoughtTimeline thoughts={thoughts} />);

    expect(screen.getByText("纯思考无工具")).toBeTruthy();
    expect(screen.queryByTestId("agent-thought-tool")).toBeNull();
    expect(screen.queryByTestId("agent-thought-observation")).toBeNull();
  });
});
