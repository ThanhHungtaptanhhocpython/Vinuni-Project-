import type { NextConfig } from "next";

// WebRTC signaling host — không hardcode default, thiếu env thì bỏ hẳn rewrite
// thay vì đoán URL (khớp cách lib/webrtc/edge.ts xử lý EDGE_VLA_URL).
const STREAM_BASE = (process.env.NEXT_PUBLIC_STREAM_BASE ?? "").replace(/\/$/, "");

// Use 127.0.0.1 (not localhost) so Node does not prefer IPv6 ::1 when the API
// is only reachable on IPv4 — avoids ECONNREFUSED ::1 + ETIMEDOUT 127.0.0.1.
const API_PROXY = (
  process.env.NEXT_PUBLIC_API_BASE ?? process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000"
).replace(/\/$/, "");

const nextConfig: NextConfig = {
  /* TẮT gzip của Next. Bắt buộc, không phải tối ưu.
   *
   * Next nén mọi phản hồi khi client gửi `Accept-Encoding: gzip` — và trình
   * duyệt luôn gửi. Với một luồng SSE, bộ nén giữ dữ liệu trong bộ đệm cho tới
   * lúc luồng đóng, nên toàn bộ sự kiện của một lượt chạy đổ xuống cùng lúc ở
   * cuối. Agent trace vì thế hiện "một phát" thay vì hiện dần, dù backend nhả
   * đúng nhịp.
   *
   * Đo được, cùng một lượt chạy: `fetch` thẳng backend nhận sự kiện ở 0.02s rồi
   * 1.28s; qua Next thì mọi sự kiện đến ở 5.58s. Ép `Accept-Encoding: identity`
   * phía client là hết — nhưng trình duyệt CẤM đặt header đó từ `fetch`. Đặt
   * `Content-Encoding: identity` trên phản hồi cũng không ăn: Next vẫn nén đè.
   * Công tắc này là chỗ duy nhất tắt được.
   *
   * Đánh đổi chấp nhận được: nén tài nguyên tĩnh là việc của tầng hosting
   * (Vercel, nginx, CDN) trong môi trường thật, và ở đó SSE phải được cấu hình
   * `no-transform` riêng. Mất chút băng thông ở dev, đổi lấy giao diện nói đúng
   * sự thật về thời điểm mọi việc xảy ra.
   */
  output: "standalone",
  compress: false,

  turbopack: {
    root: __dirname,
  },

  // Browser stays on the UI origin. Auth cookies are first-party (phones/Safari).
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${API_PROXY}/api/:path*`,
      },
    ];
  },
};

// `next dev` only — ignored by `next build` / `next start`. Without this, an
// ngrok/cloudflare tunnel serves HTML but no JS, so buttons look fine and do nothing.
if (process.env.NODE_ENV !== "production") {
  nextConfig.allowedDevOrigins = [
    "*.ngrok-free.app",
    "*.ngrok.io",
    "*.trycloudflare.com",
  ];
}

export default nextConfig;
