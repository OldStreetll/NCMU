import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { getUser, isAuthenticated } from "@/lib/auth";

interface RequireAuthProps {
  children?: ReactNode;
  // TASK-PC2-E: opt-in admin guard. When true, an authenticated but
  // non-admin user is redirected to "/" instead of seeing the children.
  // Source-of-truth is the backend dev-login response (mirrored into
  // sessionStorage); the SPA never decodes the JWT to derive this.
  requireAdmin?: boolean;
}

export function RequireAuth({ children, requireAdmin }: RequireAuthProps) {
  const location = useLocation();
  if (!isAuthenticated()) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  if (requireAdmin && !getUser()?.is_admin) {
    return <Navigate to="/" replace />;
  }
  return <>{children}</>;
}

export default RequireAuth;
