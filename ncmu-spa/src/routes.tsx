import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { Spin } from "antd";
import LoginPage from "@/pages/LoginPage";
// TASK-LOGIN-2: DingTalk OAuth callback landing page — a pre-auth public
// route (like /login) with no layout/guard. Direct-imported (not lazy) to
// match LoginPage: both are part of the unauthenticated entry flow.
import DingtalkCallbackPage from "@/pages/DingtalkCallbackPage";
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
// TASK-PE-03 (Phase 2E): employee home /staff — Apps by-mode 5-tab landing.
const StaffHomePage = lazy(() => import("@/pages/StaffHomePage"));
// TASK-PC3-B (Phase 2C): admin personal-KB list + double-column detail.
const AdminPersonalKbPage = lazy(() => import("@/pages/AdminPersonalKbPage"));
const AdminApplicationDetailPage = lazy(
  () => import("@/pages/AdminApplicationDetailPage"),
);
// TASK-PE-04 (Phase 2E): admin landing page replacing PE-01's placeholder.
const AdminHomePage = lazy(() => import("@/pages/AdminHomePage"));
// TASK-PE-06 (Phase 2E Batch 2): admin user management CRUD. AdminLayout
// already advertises `/admin/users` in its Sider; this route closes the
// previously dead link.
const AdminUsersPage = lazy(() => import("@/pages/AdminUsersPage"));
// TASK-PE-05 (Phase 2E Batch 2): admin tags CRUD-only page (no binding UI).
const AdminTagsPage = lazy(() => import("@/pages/AdminTagsPage"));
// TASK-PE-07 (Phase 2E Batch 3): admin App 同步管理页（list + 立即同步 +
// is_active 切换）. Sync is poll-only (PE-07 spike: Dify v1.13.3 has no
// outbound webhook).
const AdminAppsPage = lazy(() => import("@/pages/AdminAppsPage"));
// TASK-PE-10 (Phase 2E): admin DSL export page (multi-select + ZIP download).
const AdminDslExportPage = lazy(() => import("@/pages/AdminDslExportPage"));
// TASK-PE-11 (Phase 2E Batch 4): admin DSL import page (YAML/ZIP drag-upload).
const AdminDslImportPage = lazy(() => import("@/pages/AdminDslImportPage"));

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
      {/* TASK-LOGIN-2: DingTalk OAuth return URL. Public + guard-free (same
          level as /login) — the user isn't authenticated until this page
          completes the code+state exchange and stores the JWT. */}
      <Route
        path="/auth/dingtalk/callback"
        element={<DingtalkCallbackPage />}
      />

      {/* Employee home + the 5 mode pages + Personal-KB (my-kb) all sit
          inside EmployeeLayout and require requiredRole="employee". */}
      <Route path="/staff" element={employeeRoute(<StaffHomePage />)} />
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
      <Route path="/admin" element={adminRoute(<AdminHomePage />)} />
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
      <Route path="/admin/users" element={adminRoute(<AdminUsersPage />)} />
      <Route path="/admin/tags" element={adminRoute(<AdminTagsPage />)} />
      <Route path="/admin/apps" element={adminRoute(<AdminAppsPage />)} />
      <Route
        path="/admin/apps/dsl-export"
        element={adminRoute(<AdminDslExportPage />)}
      />
      <Route
        path="/admin/apps/dsl-import"
        element={adminRoute(<AdminDslImportPage />)}
      />

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default AppRoutes;
