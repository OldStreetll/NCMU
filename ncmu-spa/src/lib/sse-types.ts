// NCMU 统一 SSE 事件 TS 类型 — 与 backend `ncmu_backend.schemas.sse_events`
// 严格字面对齐（TASK-68 commit e6a1bc1 引入 5 sub-schemas + envelope）。
//
// 本文件由 TASK-70 (B1) 计划创建，但 B1 实际只交付 TASK-70a；TASK-70a 期间为
// 解 ChatWindow 类型可用问题，把 NcmuSseEvent 等 envelope/sub-schema 类型 inline
// 进了 `@/lib/streamChat`（path B / 见该文件 line 48-57 注释）。本任务（TASK-70b-1）
// 补做 TASK-70 基础设施段，按 plan §修法 1352-1415 字面在此重新声明 canonical
// 类型，新建的 workflow 共享原子件 + hook 全部 import from "@/lib/sse-types"。
//
// TASK-C 维度 32 (2026-05-14): streamChat.ts 的 inline duplicate 段已删 / 改
// 为从此文件 re-export；ChatWindow 已切 import → sse-types — B-NEW-32 闭环。

export type SseEventType =
  | "node_started"
  | "node_finished"
  | "workflow_finished"
  | "agent_thought"
  | "tool_call"
  | "ping"
  | "error";

// TASK-C (B-NEW-32) 维度 32 schema 单源: ``streamChat.ts`` previously declared
// the same Literal under the name ``NcmuSseEventType``. Re-export under both
// names so historical imports keep compiling against the canonical source
// (sse-types) without forcing a rename across callers.
export type NcmuSseEventType = SseEventType;

export interface NodeStartedData {
  node_id: string;
  node_type: string;
  title?: string;
  inputs: Record<string, unknown>;
}

export interface NodeFinishedData {
  node_id: string;
  node_type: string;
  // ★H2 (PLAN-FIX-2)：与 backend NodeFinishedData Literal 对齐（spike §3.7 简化
  // 为 4 终态：成功 / 失败 / 用户中止 / 异常）。partial-succeeded → succeeded。
  status: "succeeded" | "failed" | "stopped" | "exception";
  outputs: Record<string, unknown>;
  elapsed_ms?: number;
  error?: string;
}

export interface WorkflowFinishedData {
  // ★H2：与 backend WorkflowFinishedData Literal 对齐（4 终态）。
  status: "succeeded" | "failed" | "stopped" | "exception";
  outputs: Record<string, unknown>;
  total_elapsed_ms?: number;
  error?: string;
}

export interface AgentThoughtData {
  thought: string;
  observation?: string;
  tool_name?: string;
}

export interface ToolCallData {
  tool_name: string;
  tool_input: Record<string, unknown>;
  tool_output?: unknown;
  status: "calling" | "completed" | "failed";
}

export type NcmuSseEventData =
  | NodeStartedData
  | NodeFinishedData
  | WorkflowFinishedData
  | AgentThoughtData
  | ToolCallData
  | Record<string, unknown>;

// envelope — backend `routes.py:96+109` serialize 整个 envelope 到 SSE `data:`
// 行；frontend SSE parser 收到的就是这个 shape。
export interface NcmuSseEvent {
  event_type: SseEventType;
  run_id: string; // UUID
  timestamp: string; // ISO 8601
  data: NcmuSseEventData;
}
