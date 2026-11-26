import { API_BASE, USE_MOCK } from "./config";
import { apiClient, refreshAccessToken } from "./client";
import type { User } from "@/stores";

export interface AuthUser {
  id: string;
  email: string;
  name: string;
  picture_url?: string;
  timezone: string;
  locale?: string;
  created_at?: string;
  stats?: Record<string, number>;
}

export interface TokenPayload {
  token_type: string;
  expires_in: number;
  user: AuthUser;
}

export function mapAuthUser(u: AuthUser): User {
  return {
    id: u.id,
    name: u.name || u.email,
    email: u.email,
    avatar_url: u.picture_url ?? null,
    timezone: u.timezone || "Asia/Ho_Chi_Minh",
  };
}

/** Full URL for Google OIDC start (browser navigation via UI origin rewrite). */
export function googleLoginUrl(redirectTo = "/dashboard"): string {
  let safe = redirectTo;
  if (!safe.startsWith("/") || safe.startsWith("//")) safe = "/dashboard";
  // Land on /auth/callback; Cloud already set HttpOnly access + refresh cookies.
  const callback = `/auth/callback?redirect_to=${encodeURIComponent(safe)}`;
  return `${API_BASE}/api/v1/auth/google/login?redirect_to=${encodeURIComponent(callback)}`;
}

export async function fetchMe(): Promise<User> {
  const data = await apiClient.get<AuthUser>("/api/v1/auth/me");
  return mapAuthUser(data);
}

/** Ensure HttpOnly access cookie (via refresh) then load profile. */
export async function bootstrapSession(): Promise<User | null> {
  try {
    return await fetchMe();
  } catch {
    const ok = await refreshAccessToken();
    if (!ok) return null;
    try {
      return await fetchMe();
    } catch {
      return null;
    }
  }
}

/** Development only — Cloud POST /auth/dev/login. */
export async function devLogin(email = "dev@example.com", name = "Dev User"): Promise<User> {
  if (USE_MOCK) {
    const mockUser: User = {
      id: "user-mock-001",
      name,
      email,
      avatar_url: null,
      timezone: "Asia/Ho_Chi_Minh",
    };
    return mockUser;
  }
  const data = await apiClient.post<TokenPayload>(
    "/api/v1/auth/dev/login",
    { email, name },
    { auth: false },
  );
  return mapAuthUser(data.user);
}

/** Email/password — song song với Google OIDC (POST /auth/register). */
export async function registerWithPassword(input: {
  email: string;
  password: string;
  name: string;
}): Promise<User> {
  const data = await apiClient.post<TokenPayload>("/api/v1/auth/register", input, {
    auth: false,
  });
  return mapAuthUser(data.user);
}

/** Email/password login (POST /auth/login). */
export async function loginWithPassword(input: {
  email: string;
  password: string;
}): Promise<User> {
  const data = await apiClient.post<TokenPayload>("/api/v1/auth/login", input, {
    auth: false,
  });
  return mapAuthUser(data.user);
}

export async function updateProfile(data: {
  name?: string;
  timezone?: string;
  locale?: string;
}): Promise<User> {
  const updated = await apiClient.patch<AuthUser>("/api/v1/auth/me", data);
  return mapAuthUser(updated);
}

export async function logoutApi(allDevices = false): Promise<void> {
  try {
    await apiClient.post(
      `/api/v1/auth/logout${allDevices ? "?all_devices=true" : ""}`,
      undefined,
      { skipRefreshRetry: true },
    );
  } catch {
    /* still clear client state */
  }
}
