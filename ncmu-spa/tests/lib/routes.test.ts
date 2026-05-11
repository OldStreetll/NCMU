// TASK-78 ★N3 — routeForMode helper unit tests.
//
// Plan AC#4 字面：5 mode 映射 + unknown 兜底 = 6 cases。

import { describe, expect, it } from "vitest";
import { routeForMode } from "@/lib/routes";

describe("routeForMode (TASK-78 ★N3)", () => {
  const appId = "test-app-id";

  it("chat → /chat (Phase 1 path; appId not surfaced)", () => {
    expect(routeForMode("chat", appId)).toBe("/chat");
  });

  it("advanced-chat → /apps/${appId}/chatflow (TASK-75)", () => {
    expect(routeForMode("advanced-chat", appId)).toBe(
      `/apps/${appId}/chatflow`,
    );
  });

  it("completion → /apps/${appId}/completion (TASK-76)", () => {
    expect(routeForMode("completion", appId)).toBe(
      `/apps/${appId}/completion`,
    );
  });

  it("workflow → /apps/${appId}/workflow (TASK-77)", () => {
    expect(routeForMode("workflow", appId)).toBe(
      `/apps/${appId}/workflow`,
    );
  });

  it("agent-chat → /apps/${appId}/agent (TASK-78)", () => {
    expect(routeForMode("agent-chat", appId)).toBe(`/apps/${appId}/agent`);
  });

  it("unknown mode → /chat fallback (★H-NEW-2 / PLAN-FIX-3)", () => {
    expect(routeForMode("totally-unknown-mode", appId)).toBe("/chat");
  });
});
