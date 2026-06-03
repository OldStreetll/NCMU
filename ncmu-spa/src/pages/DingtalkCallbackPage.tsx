import { useEffect, useRef } from "react";
import { Spin, Typography, message } from "antd";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, ApiError } from "@/lib/api";
import { setAuth, type AuthUser } from "@/lib/auth";
import { zh } from "@/locales/zh";

// TASK-LOGIN-2 Pattern B step ③: DingTalk redirects the browser back here
// (DINGTALK_LOGIN_REDIRECT_URI points at this route) with ?code=&state= in
// the query. We exchange them for a session via the LOGIN-1 callback
// endpoint, store the JWT, and land the user on their home page by role
// (is_admin → /admin | /staff).
//
// Wire shape (LOGIN-1 contract, byte-identical to DevLoginResponse):
//   GET /auth/dingtalk/callback?code=&state=  → 200 { jwt, user, expires_at }
// The state cookie set during step ① rides along automatically (same-origin
// fetch sends it), so we forward only code + state from the URL.
type DingtalkCallbackResponse = {
  jwt: string;
  user: AuthUser;
  expires_at: string;
};

export default function DingtalkCallbackPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  // The OAuth `code` is single-use — exchanging it twice fails upstream. So
  // `exchangedRef` is the SOLE one-shot guard: it persists across React 18
  // StrictMode's dev double-mount (useRef survives remount), so the second
  // mount's effect early-returns and only ONE callback fetch is ever sent.
  //
  // REWORK-LOGIN-2-INDEP: a prior `cancelled` closure flag also suppressed
  // setAuth/navigate. Under StrictMode that swallowed the success path — the
  // first mount's cleanup set cancelled=true, the second mount never refetched
  // (ref guard), so the in-flight resolve hit `if (cancelled) return` and the
  // user stuck on the Spin with no session. With `exchangedRef` guaranteeing
  // exactly one fetch, the result is adopted unconditionally (no cancelled
  // gate) — StrictMode-safe.
  const exchangedRef = useRef(false);

  useEffect(() => {
    if (exchangedRef.current) return;
    exchangedRef.current = true;

    const code = searchParams.get("code");
    const state = searchParams.get("state");

    // Defensive: DingTalk should always return both, but a hand-typed or
    // truncated redirect could miss them. Fail closed back to /login rather
    // than send an empty exchange the backend would 400 anyway.
    if (!code || !state) {
      message.error(zh.login.callbackMissingParams);
      navigate("/login", { replace: true });
      return;
    }

    const qs = new URLSearchParams({ code, state }).toString();
    api<DingtalkCallbackResponse>(
      `/auth/dingtalk/callback?${qs}`,
      { method: "GET" },
      { skipAuth: true },
    )
      .then((resp) => {
        setAuth(resp.jwt, resp.user);
        // Land by role. No returnTo: the DingTalk full-page redirect drops
        // the SPA's location.search, so the round-trip only ever returns
        // ?code=&state= — a returnTo deep-link can't survive it.
        navigate(resp.user.is_admin ? "/admin" : "/staff", { replace: true });
      })
      .catch((e: unknown) => {
        const detail =
          e instanceof ApiError ? `${e.status} ${e.message}` : String(e);
        message.error(`${zh.login.callbackFailed}: ${detail}`);
        navigate("/login", { replace: true });
      });
    // searchParams/navigate are stable for this one-shot exchange; the
    // exchangedRef guard makes any re-run a no-op regardless.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 16,
        paddingTop: 160,
      }}
    >
      <Spin size="large" />
      <Typography.Text type="secondary">
        {zh.login.callbackLoading}
      </Typography.Text>
    </div>
  );
}
