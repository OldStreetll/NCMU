import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { Spin } from "antd";
import LoginPage from "@/pages/LoginPage";
import { RoleGuard } from "@/components/guard/RoleGuard";
import { EmployeeLayout } from "@/layouts/EmployeeLayout";
import { AdminLayout } from "@/layouts/AdminLayout";
import { getUser, isAuthenticated } from "@/lib/auth";

// PLAN-FIX-3 F1: ChatPage stub created by TASK-29; TASK-31 overwrites.
const ChatPage = lazy(() => import("@/pages/ChatPage"));
// M-NEW-1 / C-NEW-4: AdminKbConfigsPage stub created by TASK-29; TASK-32 overwrites.
const AdminKbConfigsPage = lazy(() => import("@/pages/AdminKbConfigsPage"));
// TASK-75 (Phase 2B B3): advanced-chat (Chatflow) mode page.
const AdvancedChatPage = lazy(() => import("@/pages/AdvancedChatPage"));
// TASK-76 (Phase 2B B3): completion-mode page.
const CompletionPage = lazy(() => import("@/pages/CompletionPage"));
// TASK-77 (Phase 2B B3): workflow-mode page.
const WorkflowRunPage = lazy(() => import("@/pages/WorkflowRunPage"));
// TASK-78 (Phase 2B B3): agent-chat mode page.
const AgentChatPage = lazy(() => import("@/pages/AgentChatPage"));
// TASK-PC3-A (Phase 2C): employee Personal-KB application page (/my-kb).
const MyKbPage = lazy(() => import("@/pages/MyKbPage"));
// TASK-PC3-B (Phase 2C): admin personal-KB list + double-column detail.
const AdminPersonalKbPage = lazy(() => import("@/pages/AdminPersonalKbPage"));
const AdminApplicationDetailPage = lazy(
  () => import("@/pages/AdminApplicationDetailPage"),
);
// TASK-PE-05 (Phase 2E Batch 2): admin tags CRUD-only page (no binding UI).
const AdminTagsPage = lazy(() => import("@/pages/AdminTagsPage"));

const lazyFallback = (
  <div style={{ display: "flex", justifyContent: "center", padding: 48 }}>
    <Spin />
  </div>
);

// TASK-PE-01: root redirect routes anonymous visitors to /login, employees
// to /staff, and admins to /admin. Mirrors the LoginPage post-login policy
// so both the cold-start and post-login flows land on the same home page.
function RootRedirect() {
  if (!isAuthenticated()) return <Navigate to="/login" replace />;
  return <Navigate to={getUser()?.is_admin ? "/admin" : "/staff"} replace />;
}

// TASK-PE-01: inline placeholders for the two role-home routes that PE-03
// (StaffHomePage) and PE-04 (AdminHomePage) will replace. PE-01's contract
// is the layout + guard scaffold; the 2 home pages are explicitly carved
// out into their own batched tasks per plan §1.3 lines 121-122.
function StaffHomePlaceholder() {
  return <div data-testid="staff-home-placeholder">员工首页（PE-03 待实现）</div>;
}

function AdminHomePlaceholder() {
  return <div data-testid="admin-home-placeholder">管理后台首页（PE-04 待实现）</div>;
}

// TASK-PE-01: wrap a route element with the employee layout + employee role
// guard so every existing employee-facing page picks up the shared shell in
// one place. Keeping the wrapper local to routes.tsx (vs introducing a new
// ProtectedRoute abstraction) stays inside the PE-01 file-range contract.
function employeeRoute(node: React.ReactNode) {
  return (
    <RoleGuard requiredRole="employee">
      <EmployeeLayout>
        <Suspense fallback={lazyFallback}>{node}</Suspense>
      </EmployeeLayout>
    </RoleGuard>
  );
}

function adminRoute(node: React.ReactNode) {
  return (
    <RoleGuard requiredRole="admin">
      <AdminLayout>
        <Suspense fallback={lazyFallback}>{node}</Suspense>
      </AdminLayout>
    </RoleGuard>
  );
}

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<RootRedirect />} />
      <Route path="/login" element={<LoginPage />} />

      {/* Employee home + the 5 mode pages + Personal-KB (my-kb) all sit
          inside EmployeeLayout and require requiredRole="employee". */}
      <Route path="/staff" element={employeeRoute(<StaffHomePlaceholder />)} />
      <Route path="/chat" element={employeeRoute(<ChatPage />)} />
      <Route path="/chat/:sessionId" element={employeeRoute(<ChatPage />)} />
      <Route
        path="/apps/:appId/chatflow"
        element={employeeRoute(<AdvancedChatPage />)}
      />
      <Route
        path="/apps/:appId/completion"
        element={employeeRoute(<CompletionPage />)}
      />
      <Route
        path="/apps/:appId/workflow"
        element={employeeRoute(<WorkflowRunPage />)}
      />
      <Route path="/apps/:appId/agent" element={employeeRoute(<AgentChatPage />)} />
      <Route path="/my-kb" element={employeeRoute(<MyKbPage />)} />

      {/* Admin home + admin-only management pages all sit inside AdminLayout
          and require requiredRole="admin". /admin/kb-configs was previously
          unguarded (pre-PE-01 bug); RoleGuard now closes that gap. */}
      <Route path="/admin" element={adminRoute(<AdminHomePlaceholder />)} />
      <Route
        path="/admin/kb-configs"
        element={adminRoute(<AdminKbConfigsPage />)}
      />
      <Route
        path="/admin/personal-kb"
        element={adminRoute(<AdminPersonalKbPage />)}
      />
      <Route
        path="/admin/personal-kb/applications/:id"
        element={adminRoute(<AdminApplicationDetailPage />)}
      />
      <Route path="/admin/tags" element={adminRoute(<AdminTagsPage />)} />

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default AppRoutes;
