// sessionStorage-backed auth state. Phase 1 dev profile only — Phase 2
// DingTalk integration will replace dev-login with the real flow but keep
// this same surface (getJwt / getUser / clearAuth) so consumers don't move.

export type AuthUser = {
  id: string;
  name: string;
  dept_path?: string | null;
  is_active: boolean;
};

const JWT_KEY = "ncmu_jwt";
const USER_KEY = "ncmu_user";

export function setAuth(jwt: string, user: AuthUser): void {
  sessionStorage.setItem(JWT_KEY, jwt);
  sessionStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function getJwt(): string | null {
  return sessionStorage.getItem(JWT_KEY);
}

export function getUser(): AuthUser | null {
  const raw = sessionStorage.getItem(USER_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as AuthUser;
  } catch {
    return null;
  }
}

export function isAuthenticated(): boolean {
  return getJwt() !== null && getUser() !== null;
}

export function clearAuth(): void {
  sessionStorage.removeItem(JWT_KEY);
  sessionStorage.removeItem(USER_KEY);
}
