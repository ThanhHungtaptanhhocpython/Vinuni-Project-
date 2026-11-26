import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

/** Chỉ chấp nhận path nội bộ tương đối — chặn open-redirect qua `redirect_to`.
    `/dashboard` hợp lệ, `//evil.com` và `https://evil.com` thì không: cả hai
    đều là cách trình duyệt hiểu thành điều hướng ra ngoài site dù bắt đầu bằng
    ký tự trông giống path. */
function safeRedirectPath(value: string | null): string {
  if (!value) return "/dashboard";
  if (!value.startsWith("/") || value.startsWith("//") || value.includes("://")) {
    return "/dashboard";
  }
  return value;
}

export function proxy(request: NextRequest) {
  const hasSession = request.cookies.has("has_session");
  const path = request.nextUrl.pathname;

  // Nếu đã đăng nhập mà vào trang login/register thì điều hướng về dashboard
  if (hasSession && (path === "/login" || path === "/register")) {
    const redirectTo = safeRedirectPath(request.nextUrl.searchParams.get("redirect_to"));
    return NextResponse.redirect(new URL(redirectTo, request.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/login", "/register"],
};
