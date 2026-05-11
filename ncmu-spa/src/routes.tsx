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
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default AppRoutes;
