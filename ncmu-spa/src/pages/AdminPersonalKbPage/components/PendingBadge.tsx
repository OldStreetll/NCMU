// TASK-PC3-B — PendingBadge: top-of-page alert summarizing outstanding work.
//
// r2 INDEP C1 moved this from a Sidebar badge to a per-page Alert; r3 INDEP-2
// I-INDEP2-3 path-b means the count comes from the full client-side list (no
// extra fetch). When pending+in_progress sum to zero the alert renders nothing
// so the page header stays clean.

import { Alert } from "antd";
import type { ApplicationOut } from "@/lib/api";

interface PendingBadgeProps {
  applications: ApplicationOut[];
}

export function PendingBadge({ applications }: PendingBadgeProps) {
  const pendingCount = applications.filter((a) => a.status === "pending").length;
  const inProgressCount = applications.filter(
    (a) => a.status === "in_progress",
  ).length;
  const total = pendingCount + inProgressCount;
  if (total === 0) return null;
  return (
    <Alert
      data-testid="admin-pending-badge"
      type="info"
      showIcon
      message={`当前待办 ${total} 个（pending: ${pendingCount} / in_progress: ${inProgressCount}）`}
      style={{ marginBottom: 16 }}
    />
  );
}

export default PendingBadge;
