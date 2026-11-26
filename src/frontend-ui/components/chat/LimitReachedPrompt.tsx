"use client";
/* LimitReachedPrompt — agent tự dừng vì chạm giới hạn an toàn của một lượt
   (AGENT_011: vòng lặp/tool call; AGENT_020: quá thời gian tối đa). Lượt
   chạy ĐÃ KẾT THÚC lúc panel này hiện ra — không có quyết định nào đang treo
   ở backend như `HumanInLoopPanel` thật (không có `requestId` để gửi lại).

   Nhưng về bản chất người dùng vẫn đang được hỏi ĐÚNG MỘT câu: "nhiệm vụ có
   thể chưa xong, tiếp tục hay dừng ở đây?" — nên trình bày giống ngôn ngữ
   HITL (icon, tiêu đề, nút Dừng đỏ / Tiếp tục xanh) thay vì một banner lỗi
   "báo xong rồi thôi" với đúng một nút đóng.

   CỐ Ý nằm trong LUỒNG TÀI LIỆU BÌNH THƯỜNG — không `absolute` như
   `HumanInLoopPanel` — đúng chỗ banner lỗi cũ vẫn đứng, ngay trên ô gõ chat.
   Cao hơn banner cũ (có thêm hàng nút) thì chỉ đẩy ô gõ xuống một chút, không
   bao giờ đè lên câu trả lời của agent phía trên. */

import { OctagonX, ChevronRight, TriangleAlert } from "lucide-react";

interface LimitReachedPromptProps {
  message: string;
  onContinue: () => void;
  onStop: () => void;
}

export function LimitReachedPrompt({ message, onContinue, onStop }: LimitReachedPromptProps) {
  return (
    <div
      role="alert"
      className="mx-3 mb-2 composer-shell rounded-2xl px-4 py-3.5"
      aria-labelledby="limit-reached-title"
    >
      <div className="flex items-center gap-2 mb-1.5">
        <TriangleAlert size={16} className="text-amber-400 shrink-0" />
        <h2 id="limit-reached-title" className="title text-paper-50">
          Đã dừng vì chạm giới hạn an toàn
        </h2>
      </div>
      <p className="body-text text-haze-300">{message}</p>
      <div className="mt-3 flex justify-end gap-2">
        <button
          type="button"
          onClick={onStop}
          className="h-9 px-4 rounded-lg bg-halt-600 text-white text-[14px] font-medium hover:bg-halt-700 transition-colors duration-[120ms] focus-ring cursor-pointer flex items-center gap-1.5"
        >
          <OctagonX size={13} />
          Dừng
        </button>
        <button
          type="button"
          onClick={onContinue}
          className="btn-primary focus-ring flex items-center gap-1.5"
        >
          <ChevronRight size={14} />
          Tiếp tục
        </button>
      </div>
    </div>
  );
}
