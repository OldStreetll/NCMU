// TASK-78 (Phase 2B B3) ★N3 — App mode → route path helper.
//
// Phase 2B introduces 4 new app modes beyond Phase 1 chat: advanced-chat
// (Chatflow) / completion / workflow / agent-chat. The App list (spec §8 #2,
// INDEP-PHASE2B revisit backlog — not built in Phase 2B) is expected to call
// this helper to map `app.mode` → router path. Phase 2B 内部仅 helper +
// unit test 落地；AppList 组件 phase 3 兑现。
//
// Mapping is the inverse of the routes.tsx Route table:
//   chat          → "/chat"                      (Phase 1, default KB; appId
//                                                 not surfaced in path)
//   advanced-chat → "/apps/${appId}/chatflow"    (TASK-75)
//   completion    → "/apps/${appId}/completion"  (TASK-76)
//   workflow      → "/apps/${appId}/workflow"    (TASK-77)
//   agent-chat    → "/apps/${appId}/agent"       (TASK-78)
//   <unknown>     → "/chat"                      (safe fallback per
//                                                 ★H-NEW-2 / PLAN-FIX-3)

export type AppMode =
  | "chat"
  | "advanced-chat"
  | "completion"
  | "workflow"
  | "agent-chat";

export function routeForMode(mode: string, appId: string): string {
  switch (mode) {
    case "chat":
      return "/chat";
    case "advanced-chat":
      return `/apps/${appId}/chatflow`;
    case "completion":
      return `/apps/${appId}/completion`;
    case "workflow":
      return `/apps/${appId}/workflow`;
    case "agent-chat":
      return `/apps/${appId}/agent`;
    default:
      return "/chat";
  }
}
