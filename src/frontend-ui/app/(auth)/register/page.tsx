"use client";

import { useState, useEffect, Suspense, type SubmitEvent } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import Link from "next/link";
import { Loader, TriangleAlert } from "lucide-react";

import { errorText, toErrorObject } from "@/lib/errors";
import { hasSession, markSessionStarted } from "@/lib/auth/session";
import { registerWithPassword } from "@/lib/api/auth";
import { useAuthStore } from "@/stores";

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

function RegisterContent() {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const searchParams = useSearchParams();
  const router = useRouter();
  const setUser = useAuthStore((s) => s.setUser);

  const redirectTo = sanitizeRedirect(searchParams?.get("redirect_to"));

  useEffect(() => {
    if (hasSession()) router.replace(redirectTo);
  }, [router, redirectTo]);

  const handleRegister = async (e: SubmitEvent) => {
    e.preventDefault();
    setFormError(null);

    if (password !== confirmPassword) {
      setFormError("Mật khẩu xác nhận không khớp.");
      return;
    }
    if (password.length < 8) {
      setFormError("Mật khẩu phải có ít nhất 8 ký tự.");
      return;
    }

    setSubmitting(true);
    try {
      const user = await registerWithPassword({ name, email, password });
      setUser(user);
      markSessionStarted();
      router.push(redirectTo);
    } catch (err) {
      setFormError(errorText(toErrorObject(err)));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      className="min-h-screen bg-ink-950 flex items-center justify-center px-4 py-8"
      style={{
        backgroundImage:
          "linear-gradient(var(--line) 1px, transparent 1px), linear-gradient(90deg, var(--line) 1px, transparent 1px)",
        backgroundSize: "32px 32px",
      }}
    >
      <div className="panel w-full max-w-[380px] p-8 flex flex-col items-center gap-6">
        <Logo />

        <h1 className="title text-paper-50 text-center">Tạo tài khoản</h1>

        <p className="body-text text-haze-300 text-center">
          Đăng ký bằng email để sử dụng bảng điều khiển robot.
        </p>

        {formError && (
          <div className="w-full rounded-lg bg-halt-500/12 border border-halt-500/40 px-3 py-2.5 text-[14px] text-halt-500 flex items-start gap-2">
            <TriangleAlert size={16} className="mt-0.5 shrink-0" />
            <span>{formError}</span>
          </div>
        )}

        <form onSubmit={handleRegister} className="w-full flex flex-col gap-3">
          <label className="block">
            <span className="text-[14px] text-paper-50 mb-1 block">Họ tên</span>
            <input
              type="text"
              required
              autoComplete="name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full h-10 rounded-lg bg-ink-900 border border-[var(--line)] px-3 text-[15px] text-paper-50 placeholder:text-haze-500 focus-ring"
              placeholder="Phòng Lab"
            />
          </label>

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
              autoComplete="new-password"
              minLength={8}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full h-10 rounded-lg bg-ink-900 border border-[var(--line)] px-3 text-[15px] text-paper-50 placeholder:text-haze-500 focus-ring"
              placeholder="Ít nhất 8 ký tự"
            />
          </label>

          <label className="block">
            <span className="text-[14px] text-paper-50 mb-1 block">Xác nhận mật khẩu</span>
            <input
              type="password"
              required
              autoComplete="new-password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              className="w-full h-10 rounded-lg bg-ink-900 border border-[var(--line)] px-3 text-[15px] text-paper-50 placeholder:text-haze-500 focus-ring"
              placeholder="Nhập lại mật khẩu"
            />
          </label>

          <button
            type="submit"
            disabled={submitting}
            className="w-full h-11 rounded-lg bg-arc-500 text-paper-50 text-[15px] font-medium hover:bg-arc-hover transition-colors duration-[120ms] focus-ring disabled:opacity-60 disabled:cursor-not-allowed cursor-pointer inline-flex items-center justify-center gap-2"
          >
            {submitting ? (
              <>
                <Loader size={18} className="animate-spin-slow" />
                <span>Đang tạo tài khoản…</span>
              </>
            ) : (
              "Đăng ký"
            )}
          </button>
        </form>

        <p className="text-[14px] text-haze-300 text-center">
          Đã có tài khoản?{" "}
          <Link
            href={`/login?redirect_to=${encodeURIComponent(redirectTo)}`}
            className="text-arc-400 hover:underline"
          >
            Đăng nhập
          </Link>
        </p>
      </div>
    </div>
  );
}

export default function RegisterPage() {
  return (
    <Suspense
      fallback={
        <div
          className="min-h-screen bg-ink-950 flex items-center justify-center px-4 py-8"
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
      <RegisterContent />
    </Suspense>
  );
}
