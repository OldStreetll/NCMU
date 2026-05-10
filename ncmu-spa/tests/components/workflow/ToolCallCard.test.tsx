import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ToolCallCard } from "@/components/workflow/ToolCallCard";
import type { ToolCallData } from "@/lib/sse-types";

const baseInput: Record<string, unknown> = { query: "What's the weather in 西安?" };

const callingFixture: ToolCallData = {
  tool_name: "weather_lookup",
  tool_input: baseInput,
  status: "calling",
};

const completedFixture: ToolCallData = {
  tool_name: "weather_lookup",
  tool_input: baseInput,
  tool_output: { temp_c: 22, sky: "晴" },
  status: "completed",
};

const failedFixture: ToolCallData = {
  tool_name: "weather_lookup",
  tool_input: baseInput,
  tool_output: undefined,
  status: "failed",
};

describe("ToolCallCard", () => {
  it("(a) snapshot — completed tool call renders title + status tag + input + output", () => {
    const { asFragment } = render(<ToolCallCard toolCall={completedFixture} />);
    expect(asFragment()).toMatchSnapshot();
    // tool_name shows in title; status tag visible; output block rendered
    expect(screen.getByText("weather_lookup")).toBeInTheDocument();
    expect(screen.getByTestId("tool-call-status-tag")).toBeInTheDocument();
    expect(screen.getByTestId("tool-call-output")).toBeInTheDocument();
  });

  it("(b) status='calling' shows Spin instead of status Tag", () => {
    render(<ToolCallCard toolCall={callingFixture} />);
    expect(screen.getByTestId("tool-call-spinner")).toBeInTheDocument();
    // Tag is NOT rendered while spinning
    expect(screen.queryByTestId("tool-call-status-tag")).toBeNull();
  });

  it("(c) status='failed' renders red error tag (color=error)", () => {
    render(<ToolCallCard toolCall={failedFixture} />);
    const tag = screen.getByTestId("tool-call-status-tag");
    expect(tag).toBeInTheDocument();
    // antd Tag with color="error" applies the `ant-tag-error` class — that's
    // the public API for "red error indicator". Match against className list.
    expect(tag.className).toContain("ant-tag-error");
    // Spinner must NOT render in non-calling states
    expect(screen.queryByTestId("tool-call-spinner")).toBeNull();
  });

  it("(d) tool_output undefined → output block omitted", () => {
    // failedFixture has tool_output undefined; output testid should be absent
    render(<ToolCallCard toolCall={failedFixture} />);
    expect(screen.queryByTestId("tool-call-output")).toBeNull();
    // Input is still rendered
    expect(screen.getByTestId("tool-call-input")).toBeInTheDocument();
  });

  it("(e) data-status attribute mirrors prop for downstream styling hooks", () => {
    const { rerender } = render(<ToolCallCard toolCall={callingFixture} />);
    expect(screen.getByTestId("tool-call-card").getAttribute("data-status")).toBe("calling");
    rerender(<ToolCallCard toolCall={completedFixture} />);
    expect(screen.getByTestId("tool-call-card").getAttribute("data-status")).toBe("completed");
  });
});
