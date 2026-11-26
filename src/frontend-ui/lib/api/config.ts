/**
 * Browser API origin.
 * Empty = same host as the UI (Next.js rewrites /api/* → Cloud). That keeps
 * auth cookies first-party so phones can finish Google login.
 * NEXT_PUBLIC_API_BASE is only the rewrite target in next.config.ts.
 */
export const API_BASE = "";

/** When true, robots/sessions SSE stay on mock; when false, auth + chat hit Cloud.
 *
 * Production PHẢI bật mock một cách tường minh (`=1`) — mặc định KHÔNG mock.
 * Trước đây thiếu biến môi trường (quên set khi deploy) sẽ âm thầm rơi về
 * mock, và một app điều khiển robot chạy dữ liệu giả mà không ai biết còn
 * nguy hiểm hơn báo lỗi thẳng. Dev vẫn giữ hành vi cũ (mặc định mock, trừ khi
 * `=0`) để không phải set biến môi trường mới khi chạy `next dev`. */
export const USE_MOCK =
  process.env.NODE_ENV === "production"
    ? process.env.NEXT_PUBLIC_USE_MOCK === "1"
    : process.env.NEXT_PUBLIC_USE_MOCK !== "0";
