import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { Spin } from "antd";
import LoginPage from "@/pages/LoginPage";
import { RequireAuth } from "@/components/RequireAuth";

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
// TASK-PC3-B (Phase 2C): admin personal-KB list + double-column detail.
const AdminPersonalKbPage = lazy(() => import("@/pages/AdminPersonalKbPage"));
const AdminApplicationDetailPage = lazy(
  () => import("@/pages/AdminApplicationDetailPage"),
);

const lazyFallback = (
  <div style={{ display: "flex", justifyContent: "center", padding: 48 }}>
    <Spin />
  </div>
);

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/chat" replace />} />
      <Route path="/login" element={<LoginPage />} />
      <Route
        path="/chat"
        element={
          <RequireAuth>
            <Suspense fallback={lazyFallback}>
              <ChatPage />
            </Suspense>
          </RequireAuth>
        }
      />
      <Route
        path="/chat/:sessionId"
        element={
          <RequireAuth>
            <Suspense fallback={lazyFallback}>
              <ChatPage />
            </Suspense>
          </RequireAuth>
        }
      />
      <Route
        path="/admin/kb-configs"
        element={
          <Suspense fallback={lazyFallback}>
            <AdminKbConfigsPage />
          </Suspense>
        }
      />
      <Route
        path="/apps/:appId/chatflow"
        element={
          <RequireAuth>
            <Suspense fallback={lazyFallback}>
              <AdvancedChatPage />
            </Suspense>
          </RequireAuth>
        }
      />
      <Route
        path="/apps/:appId/completion"
        element={
          <RequireAuth>
            <Suspense fallback={lazyFallback}>
              <CompletionPage />
            </Suspense>
          </RequireAuth>
        }
      />
      <Route
        path="/apps/:appId/workflow"
        element={
          <RequireAuth>
            <Suspense fallback={lazyFallback}>
              <WorkflowRunPage />
            </Suspense>
          </RequireAuth>
        }
      />
      <Route
        path="/apps/:appId/agent"
        element={
          <RequireAuth>
            <Suspense fallback={lazyFallback}>
              <AgentChatPage />
            </Suspense>
          </RequireAuth>
        }
      />
      {/* TASK-PC3-B: admin guard via inline RequireAuth wrap (matches the
          6 existing routes above). The nested-Route layout pattern would need
          RequireAuth to render <Outlet/> instead of children — leaving the
          PC2-E shared component alone keeps the change surface tight. */}
      <Route
        path="/admin/personal-kb"
        element={
          <RequireAuth requireAdmin>
            <Suspense fallback={lazyFallback}>
              <AdminPersonalKbPage />
            </Suspense>
          </RequireAuth>
        }
      />
      <Route
        path="/admin/personal-kb/applications/:id"
        element={
          <RequireAuth requireAdmin>
            <Suspense fallback={lazyFallback}>
              <AdminApplicationDetailPage />
            </Suspense>
          </RequireAuth>
        }
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default AppRoutes;
