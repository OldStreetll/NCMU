// TASK-32 — Renders one Dify retriever_resources entry as a blue Antd Tag
// "📄 {document_name}" with a hover Popover showing the first 200 chars of
// `content` and a 4-decimal `score`.
//
// Schema source: real Dify SSE message_end frame.metadata.retriever_resources
// (per dify-chat-sample-2026-04-25/sse-sample-happy-2026-04-25.txt). Only the
// fields actually consumed by the UI are typed; extra Dify fields (dataset_id,
// segment_position, etc.) ride along as `unknown` index lookups would.

import { Popover, Tag } from "antd";

export interface RetrieverResource {
  segment_id?: string;
  document_id?: string;
  document_name?: string;
  content?: string;
  score?: number;
  position?: number;
}

export interface RetrieverChipProps {
  resource: RetrieverResource;
}

const PREVIEW_MAX = 200;

export function RetrieverChip({ resource }: RetrieverChipProps) {
  const name = resource.document_name ?? "未命名来源";
  const rawContent = resource.content ?? "";
  const preview = rawContent.slice(0, PREVIEW_MAX);
  const truncated = rawContent.length > PREVIEW_MAX;
  const score = resource.score;

  const popoverContent = (
    <div style={{ maxWidth: 360 }}>
      <div style={{ whiteSpace: "pre-wrap", wordBreak: "break-word", marginBottom: 8 }}>
        {preview}
        {truncated ? "…" : ""}
      </div>
      {typeof score === "number" && (
        <div style={{ color: "#888", fontSize: 12 }}>score: {score.toFixed(4)}</div>
      )}
    </div>
  );

  return (
    <Popover placement="topLeft" title={name} content={popoverContent} trigger="hover">
      <Tag
        color="blue"
        data-testid="retriever-chip"
        style={{ cursor: "pointer", margin: "2px 4px 2px 0" }}
      >
        📄 {name}
      </Tag>
    </Popover>
  );
}

export default RetrieverChip;
