import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { getUser, isAuthenticated } from "@/lib/auth";

export type Role = "admin" | "employee";

export interface RoleGuardProps {
  requiredRole: Role;
  children: ReactNode;
}

// TASK-PE-01: role-based route guard. Mirrors the module-level auth pattern
// used by RequireAuth.tsx (no useAuth hook). Source-of-truth is the
// sessionStorage-backed AuthUser.is_admin (matches PC2-E r3 I-INDEP2-1).
//
// Behavior:
// - unauthenticated  → /login?returnTo=<current path>
// - role mismatch    → /admin (admin user) or /staff (employee user)
// - role match       → render children
export function RoleGuard({ requiredRole, children }: RoleGuardProps) {
  const location = useLocation();
  if (!isAuthenticated()) {
    const returnTo = encodeURIComponent(location.pathname);
    return <Navigate to={`/login?returnTo=${returnTo}`} replace />;
  }
  const actualRole: Role = getUser()?.is_admin ? "admin" : "employee";
  if (actualRole !== requiredRole) {
    const fallback = actualRole === "admin" ? "/admin" : "/staff";
    return <Navigate to={fallback} replace />;
  }
  return <>{children}</>;
}

export default RoleGuard;
