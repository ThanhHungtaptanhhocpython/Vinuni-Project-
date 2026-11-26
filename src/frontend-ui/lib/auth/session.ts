"use client";
/* Session — một chỗ duy nhất quản vòng đời đăng nhập.

   Auth tokens are HttpOnly cookies set by Cloud (`access_token`, `refresh_token`).
   JS only keeps a non-sensitive `has_session=1` flag for route UX. */

import { USE_MOCK } from "@/lib/api/config";
import { logoutApi } from "@/lib/api/auth";
import { useAuthStore, useRealtimeStore } from "@/stores";

const SESSION_FLAG = "has_session";

export function hasSession(): boolean {
  if (typeof document === "undefined") return false;
  return document.cookie.split("; ").some((c) => c === `${SESSION_FLAG}=1`);
}

/** Sau khi Cloud trả về callback đăng nhập thành công. */
export function markSessionStarted() {
  document.cookie = `${SESSION_FLAG}=1; path=/; max-age=86400; samesite=lax`;
}

/**
 * Đăng xuất: revoke refresh trên Cloud, xoá cờ phiên, dọn state.
 * `allDevices` → `POST /auth/logout?all_devices=true`.
 */
export async function logout(allDevices = false): Promise<void> {
  useRealtimeStore.getState().closeStream?.();
  useRealtimeStore.getState().reset();
  useAuthStore.setState({ user: null, selectedRobotId: null });

  if (!USE_MOCK) {
    await logoutApi(allDevices);
  }

  if (typeof document !== "undefined") {
    document.cookie = `${SESSION_FLAG}=; path=/; max-age=0; samesite=lax`;
  }
}
