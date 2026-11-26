/* MessageStatusLine — Execution ⊥ Verification, tách biệt, đặt NGOÀI bong
   bóng chữ (xem lý do ở `MessageBubble.tsx`, gần chỗ dùng component này).

   Tái dùng `StatusBadge` (đã có sẵn bảng màu/nhãn cho cả hai trục) thay vì
   tự khai một bảng nhãn thứ hai cho cùng các giá trị backend — một nguồn
   duy nhất cho "COMPLETED nghĩa là gì" trên toàn app. */

import type { MessageRun } from "@/stores";
import { StatusBadge } from "./StatusBadge";

interface MessageStatusLineProps {
  run: MessageRun;
}

export function MessageStatusLine({ run }: MessageStatusLineProps) {
  /* `outcome === "no_action_taken"` — tín hiệu THẬT từ backend (P1.2), đúng
     cho cả hội thoại thuần lẫn mọi lượt zero-tool-call khác: không có gì để
     báo cáo ở tầng thực thi/xác minh. KHÔNG suy đoán từ nội dung câu trả lời. */
  if (run.outcome === "no_action_taken") return null;

  /* `verification_status` chỉ khác `null` khi `verify_node` thực sự chạy
     (P0.7, Verification Layer Foundation) — vắng mặt nghĩa là không áp dụng
     (không có take_action), không phải "chưa xác minh". Ẩn hẳn dòng
     Verification thay vì hiện một trạng thái không có thật. */
  const showVerification = run.verification_status != null;

  return (
    <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1">
      <div className="flex items-center gap-1.5">
        <span className="eyebrow text-haze-500">Thực thi</span>
        <StatusBadge kind="run" value={run.status} />
      </div>
      {showVerification && (
        <div className="flex items-center gap-1.5">
          <span className="eyebrow text-haze-500">Xác minh</span>
          <StatusBadge kind="verification" value={run.verification_status as string} />
        </div>
      )}
    </div>
  );
}
