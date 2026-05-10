import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import {
  DynamicInputForm,
  type ParameterSchema,
} from "@/components/workflow/DynamicInputForm";

// Antd 5 Form pulls in the Grid useBreakpoint hook which calls
// window.matchMedia. jsdom doesn't ship it; polyfill once like the
// SessionList / RetrieverChip tests do.
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

afterEach(() => {
  vi.restoreAllMocks();
});

const sampleParameters: ParameterSchema[] = [
  { variable: "topic", label: "主题", type: "text", required: true },
  { variable: "summary", label: "摘要", type: "paragraph" },
  { variable: "tone", label: "语气", type: "select", options: ["formal", "casual"] },
];

describe("<DynamicInputForm>", () => {
  it("AC#4(a) — renders one form-item label per parameter (3 fields)", () => {
    const { container } = render(
      <DynamicInputForm parameters={sampleParameters} onSubmit={vi.fn()} />,
    );
    // 3 field labels — Form.Item for the submit button has no label, so
    // counting `.ant-form-item-label` is a precise field-count probe.
    expect(container.querySelectorAll(".ant-form-item-label")).toHaveLength(3);
    expect(screen.getByText("主题")).toBeInTheDocument();
    expect(screen.getByText("摘要")).toBeInTheDocument();
    expect(screen.getByText("语气")).toBeInTheDocument();
  });

  it("AC#4(b) — required field empty → onSubmit not invoked, validation message shown", async () => {
    const onSubmit = vi.fn();
    render(<DynamicInputForm parameters={sampleParameters} onSubmit={onSubmit} />);
    // Antd 5 inserts a thin space between two CJK chars in button text → "提 交".
    fireEvent.click(screen.getByRole("button", { name: /^提\s?交$/ }));
    await waitFor(() => {
      expect(screen.getByText(/主题必填/)).toBeInTheDocument();
    });
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("AC#4(c) — fill values and submit → onSubmit receives values dict", async () => {
    const onSubmit = vi.fn();
    const { container } = render(
      <DynamicInputForm parameters={sampleParameters} onSubmit={onSubmit} />,
    );
    // Antd 5 wires `id={name}` on the rendered `<input>` so a CSS id selector
    // is the most stable handle (avoids label-text association quirks across
    // Form.Item internals + Select hidden inputs).
    const topic = container.querySelector("#topic") as HTMLInputElement;
    const summary = container.querySelector("#summary") as HTMLTextAreaElement;
    expect(topic).not.toBeNull();
    expect(summary).not.toBeNull();
    fireEvent.change(topic, { target: { value: "Q3 复盘" } });
    fireEvent.change(summary, { target: { value: "本季度三大亮点" } });
    fireEvent.click(screen.getByRole("button", { name: /^提\s?交$/ }));
    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledTimes(1);
    });
    const submittedValues = onSubmit.mock.calls[0]![0] as Record<string, unknown>;
    expect(submittedValues).toMatchObject({
      topic: "Q3 复盘",
      summary: "本季度三大亮点",
    });
  });
});
