import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { RetrieverChip, type RetrieverResource } from "@/components/RetrieverChip";

// Antd 5 Popover pulls in the Grid useBreakpoint hook which calls
// window.matchMedia. jsdom doesn't ship it; polyfill once like the
// SessionList tests do.
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

describe("RetrieverChip", () => {
  it("AC#1+#3 renders blue tag with 📄 emoji + document_name", () => {
    const resource: RetrieverResource = {
      segment_id: "seg-1",
      document_id: "doc-1",
      document_name: "员工手册.pdf",
      content: "短内容",
      score: 0.9123,
    };
    render(<RetrieverChip resource={resource} />);

    const tag = screen.getByTestId("retriever-chip");
    expect(tag.textContent).toContain("📄");
    expect(tag.textContent).toContain("员工手册.pdf");
    // Antd Tag with color="blue" sets the ant-tag-blue class.
    expect(tag.className).toMatch(/ant-tag-blue/);
  });

  it("AC#2 hover trigger reveals popover with content preview + 4-decimal score", async () => {
    const resource: RetrieverResource = {
      segment_id: "seg-2",
      document_name: "出差报销规定.pdf",
      content: "短内容预览段落",
      score: 0.8765432,
    };
    render(<RetrieverChip resource={resource} />);

    const tag = screen.getByTestId("retriever-chip");
    // Antd Popover with trigger="hover" reacts to mouseenter on the trigger.
    fireEvent.mouseEnter(tag);

    await waitFor(() => {
      expect(screen.getByText("出差报销规定.pdf")).toBeTruthy();
    });
    expect(screen.getByText("短内容预览段落")).toBeTruthy();
    // score must render with exactly 4 decimals — toFixed(4) of 0.8765432 = 0.8765
    expect(screen.getByText(/score:\s*0\.8765/)).toBeTruthy();
  });

  it("AC#2.b long content (>200 chars) is truncated with trailing ellipsis", async () => {
    const longContent = "甲".repeat(250);
    const resource: RetrieverResource = {
      document_name: "长文档.pdf",
      content: longContent,
      score: 0.5,
    };
    render(<RetrieverChip resource={resource} />);

    fireEvent.mouseEnter(screen.getByTestId("retriever-chip"));

    // Popover renders into document.body via Antd's portal. Wait for it.
    await waitFor(() => {
      expect(screen.getByText("长文档.pdf")).toBeTruthy();
    });

    // Truncation contract:
    //   (1) the popover MUST contain "<200 甲><…>" — i.e. exactly 200 chars
    //       of preview followed by the ellipsis sentinel
    //   (2) the popover MUST NOT contain a 201-char run of 甲 — that would
    //       prove the truncation didn't happen
    const bodyText = document.body.textContent ?? "";
    expect(bodyText).toContain("甲".repeat(200) + "…");
    expect(bodyText.includes("甲".repeat(201))).toBe(false);
  });
});
