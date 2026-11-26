/* Post-SSO landing — Cloud set HttpOnly access + refresh cookies.
   Bootstrap via /auth/me (cookies) then go to the app. */

"use client";

import { useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Loader } from "lucide-react";
import { Suspense } from "react";
import { bootstrapSession } from "@/lib/api/auth";
import { markSessionStarted } from "@/lib/auth/session";
import { useAuthStore } from "@/stores";

function CallbackBody() {
  const router = useRouter();
  const searchParams = useSearchParams();

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const next = searchParams?.get("redirect_to") ?? "/dashboard";
      const safe = next.startsWith("/") && !next.startsWith("//") ? next : "/dashboard";
      const user = await bootstrapSession();
      if (cancelled) return;
      if (!user) {
        router.replace("/login?error=AUTH_005");
        return;
      }
      useAuthStore.getState().setUser(user);
      markSessionStarted();
      router.replace(safe);
    })();
    return () => {
      cancelled = true;
    };
  }, [router, searchParams]);

  return (
    <div className="min-h-screen bg-ink-950 flex flex-col items-center justify-center gap-3 text-haze-300">
      <Loader size={22} className="animate-spin-slow" />
      <p className="text-[14px]">Đang hoàn tất đăng nhập…</p>
    </div>
  );
}

export default function AuthCallbackPage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen bg-ink-950 flex items-center justify-center">
          <Loader size={22} className="animate-spin-slow text-haze-500" />
        </div>
      }
    >
      <CallbackBody />
    </Suspense>
  );
}
