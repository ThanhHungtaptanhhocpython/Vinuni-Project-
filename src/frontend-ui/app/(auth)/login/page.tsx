/* P01 — Login page
   MASTER §8: NO background video — solid bg-ink-950 with hairline grid.

   Google SSO và email/password đều là đường đăng nhập thật, song song —
   không cái nào thay cái nào. Backend: POST /auth/register, /auth/login
   (auth_routes.py), cùng cơ chế cookie session với Google OIDC/dev-login. */
"use client";

import { useState, useEffect, Suspense, type SubmitEvent } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import Link from "next/link";
import { Loader, TriangleAlert } from "lucide-react";

import { errorText, toErrorObject } from "@/lib/errors";
import { hasSession, markSessionStarted } from "@/lib/auth/session";
import { googleLoginUrl, devLogin, loginWithPassword } from "@/lib/api/auth";
import { API_BASE, USE_MOCK } from "@/lib/api/config";
import { useAuthStore } from "@/stores";

function GoogleIcon() {
  return (
    <svg width={18} height={18} viewBox="0 0 48 48" aria-hidden="true">
      <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z" />
      <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z" />
      <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z" />
      <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z" />
    </svg>
  );
}

function Logo() {
  return (
    <svg width={32} height={32} viewBox="0 0 256 256" fill="none" aria-hidden="true">
      <path
        d="M 160 88 L 194 34 L 216 0 L 256 0 L 256 40 L 221.5 93.5 L 200 128 L 256 128 L 256 256 L 96 256 L 96 168 L 64.246 220 L 40 256 L 0 256 L 0 216 L 34 162 L 56 128 L 0 128 L 0 0 L 160 0 Z"
        fill="var(--paper-50)"
      />
    </svg>
  );
}

function sanitizeRedirect(raw: string | null | undefined): string {
  const value = raw ?? "/dashboard";
  if (!value.startsWith("/") || value.startsWith("//")) return "/dashboard";
  return value;
}

function LoginContent() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [googleRedirecting, setGoogleRedirecting] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [googleReady, setGoogleReady] = useState(false);
  const [statusChecked, setStatusChecked] = useState(USE_MOCK);

  const searchParams = useSearchParams();
  const router = useRouter();
  const setUser = useAuthStore((s) => s.setUser);

  const errorCode = searchParams?.get("error") ?? null;
  const urlError = errorCode
    ? errorText(toErrorObject({ code: errorCode, message: "" }))
    : null;

  const redirectTo = sanitizeRedirect(searchParams?.get("redirect_to"));

  useEffect(() => {
    if (hasSession()) router.replace(redirectTo);
  }, [router, redirectTo]);

  /* Ask the backend whether Google SSO is configured — only the server holds
     GOOGLE_CLIENT_ID. When unset in development, fall back to demo login. */
  const [devLoginAvailable, setDevLoginAvailable] = useState(false);
  useEffect(() => {
    if (USE_MOCK) return;
    let cancelled = false;
    fetch(`${API_BASE}/api/v1/status`)
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (cancelled || !data) return;
        const configured = Boolean(data.google_sso_configured);
        setGoogleReady(configured);
        setDevLoginAvailable(data.app_env === "development" && !configured);
        setStatusChecked(true);
      })
      .catch(() => {
        if (!cancelled) setStatusChecked(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleGoogleLogin = () => {
    setGoogleRedirecting(true);
    if (USE_MOCK || devLoginAvailable) {
      void (async () => {
        try {
          const user = await devLogin();
          setUser(user);
          markSessionStarted();
          router.push(redirectTo);
        } catch (err) {
          setFormError(errorText(toErrorObject(err)));
          setGoogleRedirecting(false);
        }
      })();
      return;
    }
    window.location.href = googleLoginUrl(redirectTo);
  };

  const handleEmailLogin = async (e: SubmitEvent) => {
    e.preventDefault();
    setFormError(null);
    setSubmitting(true);
    try {
      const user = await loginWithPassword({ email, password });
      setUser(user);
      markSessionStarted();
      router.push(redirectTo);
    } catch (err) {
      setFormError(errorText(toErrorObject(err)));
    } finally {
      setSubmitting(false);
    }
  };

  const displayError = formError ?? urlError;
  const showEmailForm = true;

  return (
    <div
      className="min-h-screen bg-ink-950 flex items-center justify-center px-4"
      style={{
        backgroundImage:
          "linear-gradient(var(--line) 1px, transparent 1px), linear-gradient(90deg, var(--line) 1px, transparent 1px)",
        backgroundSize: "32px 32px",
      }}
    >
      <div className="panel w-full max-w-[380px] p-8 flex flex-col items-center gap-6">
        <Logo />

        <h1 className="title text-paper-50 text-center">Đăng nhập</h1>

        <p className="body-text text-haze-300 text-center">
          Dùng tài khoản Google hoặc email để tiếp tục.
        </p>

        {displayError && (
          <div className="w-full rounded-lg bg-halt-500/12 border border-halt-500/40 px-3 py-2.5 text-[14px] text-halt-500 flex items-start gap-2">
            <TriangleAlert size={16} className="mt-0.5 shrink-0" />
            <span>{displayError}</span>
          </div>
        )}

        {!USE_MOCK && statusChecked && !googleReady && !devLoginAvailable && (
          <div className="w-full rounded-lg bg-halt-500/12 border border-halt-500/40 px-3 py-2.5 text-[14px] text-halt-500 flex items-start gap-2">
            <TriangleAlert size={16} className="mt-0.5 shrink-0" />
            <span>
              Google SSO chưa cấu hình. Điền GOOGLE_CLIENT_ID / SECRET trong{" "}
              <code className="text-[13px]">src/cloud-agent/.env</code> và đăng
              ký redirect URI{" "}
              <code className="text-[13px]">
                http://localhost:3000/api/v1/auth/google/callback
              </code>
              .
            </span>
          </div>
        )}

        {showEmailForm && (
          <>
            <form onSubmit={handleEmailLogin} className="w-full flex flex-col gap-3">
              <label className="block">
                <span className="text-[14px] text-paper-50 mb-1 block">Email</span>
                <input
                  type="email"
                  required
                  autoComplete="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="w-full h-10 rounded-lg bg-ink-900 border border-[var(--line)] px-3 text-[15px] text-paper-50 placeholder:text-haze-500 focus-ring"
                  placeholder="lab@example.io"
                />
              </label>

              <label className="block">
                <span className="text-[14px] text-paper-50 mb-1 block">Mật khẩu</span>
                <input
                  type="password"
                  required
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="w-full h-10 rounded-lg bg-ink-900 border border-[var(--line)] px-3 text-[15px] text-paper-50 placeholder:text-haze-500 focus-ring"
                  placeholder="••••••••"
                />
              </label>

              <button
                type="submit"
                disabled={submitting || googleRedirecting}
                className="w-full h-11 rounded-lg bg-arc-500 text-paper-50 text-[15px] font-medium hover:bg-arc-hover transition-colors duration-[120ms] focus-ring disabled:opacity-60 disabled:cursor-not-allowed cursor-pointer inline-flex items-center justify-center gap-2"
              >
                {submitting ? (
                  <>
                    <Loader size={18} className="animate-spin-slow" />
                    <span>Đang đăng nhập…</span>
                  </>
                ) : (
                  "Đăng nhập"
                )}
              </button>
            </form>

            <div className="w-full flex items-center gap-3">
              <div className="flex-1 h-px bg-[var(--line)]" />
              <span className="text-[13px] text-haze-500">hoặc</span>
              <div className="flex-1 h-px bg-[var(--line)]" />
            </div>
          </>
        )}

        <button
          type="button"
          onClick={handleGoogleLogin}
          disabled={
            googleRedirecting ||
            submitting ||
            (!USE_MOCK && statusChecked && !googleReady && !devLoginAvailable)
          }
          className="w-full h-11 rounded-lg bg-paper-50 text-ink-900 text-[15px] font-medium inline-flex items-center justify-center gap-3 hover:opacity-90 transition-opacity duration-[120ms] focus-ring disabled:opacity-60 disabled:cursor-not-allowed cursor-pointer"
        >
          {googleRedirecting ? (
            <>
              <Loader size={18} className="animate-spin-slow" />
              <span>Đang chuyển hướng…</span>
            </>
          ) : (
            <>
              <GoogleIcon />
              <span>{devLoginAvailable ? "Đăng nhập demo" : "Đăng nhập bằng Google"}</span>
            </>
          )}
        </button>

        {showEmailForm && (
          <p className="text-[14px] text-haze-300 text-center">
            Chưa có tài khoản?{" "}
            <Link
              href={`/register?redirect_to=${encodeURIComponent(redirectTo)}`}
              className="text-arc-400 hover:underline"
            >
              Đăng ký
            </Link>
          </p>
        )}

        <p className="eyebrow text-center !text-[11px] !tracking-[0.04em] !normal-case">
          Bằng việc tiếp tục, bạn đồng ý với Điều khoản sử dụng.
        </p>
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense
      fallback={
        <div
          className="min-h-screen bg-ink-950 flex items-center justify-center px-4"
          style={{
            backgroundImage:
              "linear-gradient(var(--line) 1px, transparent 1px), linear-gradient(90deg, var(--line) 1px, transparent 1px)",
            backgroundSize: "32px 32px",
          }}
        >
          <Loader size={24} className="animate-spin-slow text-haze-500" />
        </div>
      }
    >
      <LoginContent />
    </Suspense>
  );
}
