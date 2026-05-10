import { render, screen } from "@testing-library/react";
import { beforeAll, describe, expect, it } from "vitest";
import { NodeTraceViewer } from "@/components/workflow/NodeTraceViewer";
import type { NcmuSseEvent } from "@/lib/sse-types";

// Antd 5 Empty/Tag/Typography pull in Grid useBreakpoint → matchMedia.
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

const RUN_ID = "00000000-0000-0000-0000-000000000001";

describe("NodeTraceViewer", () => {
  it("AC#5 (a) empty nodeTrace renders placeholder", () => {
    render(<NodeTraceViewer nodeTrace={[]} />);

    expect(screen.getByTestId("node-trace-empty")).toBeTruthy();
    expect(screen.getByText("暂无节点")).toBeTruthy();
    expect(screen.queryByTestId("node-trace-list")).toBeNull();
  });

  it("AC#5 (b) 1 node_started + 1 node_finished renders 2 rows", () => {
    const events: NcmuSseEvent[] = [
      {
        event_type: "node_started",
        run_id: RUN_ID,
        timestamp: "2026-05-10T00:00:00Z",
        data: {
          node_id: "node-A",
          node_type: "llm",
          title: "调用大模型",
          inputs: { prompt: "hi" },
        },
      },
      {
        event_type: "node_finished",
        run_id: RUN_ID,
        timestamp: "2026-05-10T00:00:01Z",
        data: {
          node_id: "node-A",
          node_type: "llm",
          status: "succeeded",
          outputs: { text: "hello" },
          elapsed_ms: 1234,
        },
      },
    ];

    render(<NodeTraceViewer nodeTrace={events} />);

    const rows = screen.getAllByTestId("node-trace-row");
    expect(rows).toHaveLength(2);
    expect(rows[0].getAttribute("data-event-type")).toBe("node_started");
    expect(rows[1].getAttribute("data-event-type")).toBe("node_finished");
    expect(screen.getByText("调用大模型")).toBeTruthy();
    expect(screen.getByText("succeeded")).toBeTruthy();
    expect(screen.getByText("1234ms")).toBeTruthy();
  });

  it("AC#5 (c) status='failed' renders red indicator", () => {
    const events: NcmuSseEvent[] = [
      {
        event_type: "node_finished",
        run_id: RUN_ID,
        timestamp: "2026-05-10T00:00:02Z",
        data: {
          node_id: "node-B",
          node_type: "tool",
          status: "failed",
          outputs: {},
          error: "连接超时",
        },
      },
    ];

    render(<NodeTraceViewer nodeTrace={events} />);

    const failedTag = screen.getByTestId("node-status-failed");
    expect(failedTag.textContent).toContain("failed");
    // Antd preset color="red" yields the .ant-tag-red class.
    expect(failedTag.className).toMatch(/ant-tag-red/);

    const row = screen.getByTestId("node-trace-row");
    expect(row.getAttribute("data-status")).toBe("failed");

    expect(screen.getByTestId("node-error-msg").textContent).toContain(
      "连接超时",
    );
  });

  it("non-node event types are skipped silently (defensive filter)", () => {
    const events: NcmuSseEvent[] = [
      {
        event_type: "ping",
        run_id: RUN_ID,
        timestamp: "2026-05-10T00:00:03Z",
        data: {},
      },
      {
        event_type: "agent_thought",
        run_id: RUN_ID,
        timestamp: "2026-05-10T00:00:04Z",
        data: { thought: "think" },
      },
      {
        event_type: "node_started",
        run_id: RUN_ID,
        timestamp: "2026-05-10T00:00:05Z",
        data: { node_id: "node-C", node_type: "code", inputs: {} },
      },
    ];

    render(<NodeTraceViewer nodeTrace={events} />);

    const rows = screen.getAllByTestId("node-trace-row");
    expect(rows).toHaveLength(1);
    expect(rows[0].getAttribute("data-event-type")).toBe("node_started");
  });
});
