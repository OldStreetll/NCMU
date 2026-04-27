import { getJwt } from "@/lib/auth";

// E-1 修订：BASE = /api/v1/ncmu — Phase 0 已用 /api/* 反代 Dify，必须避开。
const BASE = "/api/v1/ncmu";

export class ApiError extends Error {
  status: number;
  code: number | undefined;
  constructor(status: number, code: number | undefined, message: string) {
    super(message);
    this.status = status;
    this.code = code;
    this.name = "ApiError";
  }
}

export async function api<T = unknown>(
  path: string,
  init: RequestInit = {},
  opts: { skipAuth?: boolean } = {},
): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((init.headers as Record<string, string>) ?? {}),
  };
  if (!opts.skipAuth) {
    const jwt = getJwt();
    if (!jwt) throw new ApiError(401, undefined, "no jwt");
    headers["Authorization"] = `Bearer ${jwt}`;
  }
  const resp = await fetch(`${BASE}${path}`, { ...init, headers });
  if (!resp.ok) {
    let body: { code?: number; message?: string } = {};
    try {
      body = await resp.json();
    } catch {
      // non-json response (eg. nginx 502 HTML); fall through with statusText
    }
    // dev-login + dev/users return 404 with detail={code,message}; FastAPI
    // wraps it as `detail: {...}` not flat — handle both shapes.
    const detail = (body as unknown as { detail?: { code?: number; message?: string } }).detail;
    const code = detail?.code ?? body.code;
    const message = detail?.message ?? body.message ?? resp.statusText;
    throw new ApiError(resp.status, code, message);
  }
  return resp.json() as Promise<T>;
}

export { BASE as API_BASE };
