/* StatusBadge: one badge for run status, run source and schedule outcome. */

import type { RunSource, RunStatus } from "@/stores";

type BadgeSpec = { label: string; className: string };

const RUN: Record<RunStatus, BadgeSpec> = {
  RUNNING: { label: "Đang chạy", className: "bg-arc-500/15 text-arc-400" },
  WAITING_TOOL: { label: "Chờ tool", className: "bg-arc-500/15 text-arc-400" },
  COMPLETED: { label: "Hoàn thành", className: "bg-jade-400/15 text-jade-400" },
  FAILED: { label: "Thất bại", className: "bg-halt-500/15 text-halt-500" },
  CANCELLED: { label: "Đã huỷ", className: "bg-ink-700 text-haze-500" },
  /* `TIMEOUT` là trạng thái mới của develop; giữ nguyên màu develop chọn.
     GHI CHÚ LỆCH: `TOOL_STATUS_STYLE` trong `ToolCard` để TIMEOUT màu amber,
     tách khỏi FAILED đỏ. Ở đây hai cái cùng đỏ. Không tự ý đổi trong lúc gộp
     nhánh — nhưng đây là chỗ cần thống nhất một lần, có chủ đích. */
  TIMEOUT: { label: "Quá giờ", className: "bg-halt-500/15 text-halt-500" },
  /* `amber` chứ không phải `arc`: đây là trạng thái CHỜ, chưa có gì chạy. Dùng
     `arc` như RUNNING thì hai thứ khác hẳn nhau lại nhìn giống nhau, mà phân
     biệt "đang chạy" với "chờ chạy" chính là việc của trang Nhật ký. */
  SCHEDULED: { label: "Chờ chạy", className: "bg-amber-400/15 text-amber-400" },
};

const SOURCE: Record<RunSource, BadgeSpec> = {
  CHAT: { label: "Trò chuyện", className: "bg-ink-700 text-haze-300" },
  SCHEDULE: { label: "Lịch chạy", className: "bg-ink-700 text-haze-300" },
  MANUAL_RUN: { label: "Chạy tay", className: "bg-ink-700 text-haze-300" },
};

const SCHEDULE_STATE: Record<string, BadgeSpec> = {
  ENABLED: { label: "Đang bật", className: "bg-jade-400/15 text-jade-400" },
  DISABLED: { label: "Đã tắt", className: "bg-ink-700 text-haze-500" },
  AUTO_DISABLED: { label: "Tự tắt", className: "bg-amber-400/15 text-amber-400" },
  SKIPPED: { label: "Bỏ qua", className: "bg-ink-700 text-haze-500" },
  MISFIRED: { label: "Lỡ lịch", className: "bg-ink-700 text-haze-500" },
};

/* Verification Layer Foundation — TÁCH BIỆT khỏi `RUN` (execution/agent-loop
   status) ở trên. Một run có thể là `COMPLETED` (agent loop xong sạch) VÀ
   đồng thời `verification_status = UNKNOWN` — hai trục khác nhau, không được
   gộp. `NOT_STARTED`/`VERIFYING` là trạng thái trung gian hiếm khi hiện ra
   (run đã kết thúc thì verify_node cũng đã chạy xong), giữ ở đây cho đủ tập
   giá trị backend có thể trả (`agents/verification.py:VerificationStatus`). */
const VERIFICATION: Record<string, BadgeSpec> = {
  NOT_STARTED: { label: "Chưa xác minh", className: "bg-ink-700 text-haze-500" },
  VERIFYING: { label: "Đang xác minh", className: "bg-arc-500/15 text-arc-400" },
  VERIFIED_SUCCESS: { label: "Đã xác nhận", className: "bg-jade-400/15 text-jade-400" },
  VERIFIED_FAILED: { label: "Chưa đạt", className: "bg-halt-500/15 text-halt-500" },
  UNKNOWN: { label: "Không đủ bằng chứng", className: "bg-amber-400/15 text-amber-400" },
  UNAVAILABLE: { label: "Không đủ bằng chứng", className: "bg-amber-400/15 text-amber-400" },
};

interface StatusBadgeProps {
  kind: "run" | "source" | "schedule" | "verification";
  value: string;
  size?: "sm" | "md";
}

export function StatusBadge({ kind, value, size = "sm" }: StatusBadgeProps) {
  const table =
    kind === "run" ? RUN : kind === "source" ? SOURCE : kind === "schedule" ? SCHEDULE_STATE : VERIFICATION;
  const spec = (table as Record<string, BadgeSpec>)[value] ?? {
    label: value,
    className: "bg-ink-700 text-haze-500",
  };
  const sizing = size === "sm" ? "h-6 px-2.5 text-[12px]" : "h-7 px-3 text-[13px]";

  return (
    <span
      className={`inline-flex items-center shrink-0 rounded-full font-medium uppercase tracking-[0.06em] whitespace-nowrap ${sizing} ${spec.className}`}
    >
      {spec.label}
    </span>
  );
}

/* Variante "thanh/chấm màu" của bảng `RUN` ở trên — dùng cho biểu đồ thống kê
   (Tổng quan, trang robot) thay vì badge dạng viên thuốc. CÙNG một trục ngữ
   nghĩa với `RUN`, chỉ khác cách vẽ (màu đặc cho thanh xếp chồng, thay vì nền
   mờ cho badge) — nên KHÔNG thể tái dùng thẳng `className` của `RUN`, nhưng
   label/màu gốc phải khớp nó. Đây là nơi DUY NHẤT định nghĩa cặp này; trang
   Tổng quan và trang Robot cùng import từ đây thay vì tự khai lại bảng riêng. */
export const RUN_STATUS_STATS_META: Record<string, { label: string; bar: string; text: string }> = {
  COMPLETED: { label: "Hoàn thành", bar: "bg-jade-400", text: "text-jade-400" },
  FAILED: { label: "Thất bại", bar: "bg-halt-500", text: "text-halt-500" },
  TIMEOUT: { label: "Quá giờ", bar: "bg-amber-400", text: "text-amber-400" },
  CANCELLED: { label: "Đã huỷ", bar: "bg-haze-500", text: "text-haze-500" },
  RUNNING: { label: "Đang chạy", bar: "bg-arc-500", text: "text-arc-400" },
  WAITING_TOOL: { label: "Chờ tool", bar: "bg-arc-500", text: "text-arc-400" },
};
