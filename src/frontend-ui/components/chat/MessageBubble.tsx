"use client";
/* MessageBubble — P04 §3
   User: right, arc-500. Agent: left, ink-800 + hairline.
   Tool cards nest inside the assistant bubble, in conversation order
   (MASTER §13.1) — never in their own bubble, never batched at the end. */

import { RotateCw, ThumbsDown, ThumbsUp } from "lucide-react";
import type { Message, TraceBlock } from "@/stores";
import { TraceFlow, legacyBlocksOf } from "./TraceFlow";

interface MessageBubbleProps {
  message: Message;
  /** Dòng thời gian phẳng ĐANG CHẠY (`store.runBlocks`), khi bong bóng này là
      anchor của lượt chạy hiện tại. CÓ giá trị (kể cả mảng rỗng) nghĩa là lượt
      chạy chưa xong; `undefined` thì đọc `message.blocks` đã chốt (hoặc dựng
      lại từ lịch sử — xem `legacyBlocksOf`). Một prop quyết định cả nguồn dữ
      liệu lẫn trạng thái chạy, nên không thể lệch nhau — cùng nguyên tắc
      `liveSteps` cũ của `AgentTrace`. */
  liveBlocks?: TraceBlock[];
  showCameraLink?: boolean;
  onRetry?: (message: Message) => void;
  /** Bấm nút đang bật để bỏ chọn — gọi lại với `null`. Chỉ có ý nghĩa với
      tin nhắn assistant, và chỉ hiện khi câu trả lời đã có chữ (không có gì
      để đánh giá trong lúc còn đang stream). */
  onFeedback?: (message: Message, feedback: "like" | "dislike" | null) => void;
}

export function MessageBubble({
  message,
  liveBlocks,
  showCameraLink,
  onRetry,
  onFeedback,
}: MessageBubbleProps) {
  const isUser = message.role === "user";
  const blocks = liveBlocks ?? message.blocks ?? legacyBlocksOf(message.content, message.tool_calls);

  /* Bong bóng anchor được mở RỖNG ngay từ `run.started`. Nếu lượt chạy hỏng
     trước khi có bất cứ thứ gì, đừng để lại một ô trống lơ lửng — điều kiện
     giờ CHỈ còn "không có block nào" (C5): mọi thứ người dùng cần biết (chữ
     agent nói, hành động đã làm) giờ nằm hết trong `blocks`, nên không cần
     kiểm riêng `content`/tool call nhìn thấy được như bản `AgentTrace` cũ. */
  if (!isUser && blocks.length === 0) return null;
  const failed = message.send_status === "failed";
  const absoluteTime = new Date(message.created_at).toLocaleString("vi-VN");

  return (
    <div className={`group flex flex-col ${isUser ? "items-end" : "items-start"}`}>
      <div
        className={`${
          isUser
            ? `max-w-[76%] bg-arc-500 text-white rounded-2xl rounded-br-md px-4 py-3 ${failed ? "opacity-70" : ""}`
            /* Không còn khung/nền cho bong bóng agent — kiểu Claude: chữ agent
               nói và dòng thời gian trace nằm thẳng trên nền trang, không
               phải trong một thẻ tối riêng. Chỉ tin nhắn NGƯỜI DÙNG mới cần
               khung để tách khỏi nền (bên phải, không lẫn với trace/chữ agent
               phía trên nó). */
            : "max-w-[86%] text-paper-50 py-1"
        } text-[15px] leading-[1.5] break-words`}
      >
        {/* Người dùng chỉ có content thô (không có trace) — vẫn render qua
            nhánh riêng vì `TraceFlow` là dòng thời gian của AGENT, gắn dữ
            liệu trace vào bong bóng người dùng là sai chỗ (`!isUser` là hàng
            rào cố ý trùng với điều kiện ở `fetchMessages`). */}
        {isUser ? (
          <div>{message.content}</div>
        ) : (
          <TraceFlow blocks={blocks} showCameraLink={showCameraLink} />
        )}

        {/* Không có chân "HOÀN THÀNH · Xem trace" TRONG bong bóng — lý do gốc
            vẫn đúng: một badge kiểu đó đặt ngay dưới câu "Robot đã nhặt thành
            công chiếc cốc" đọc ra thành xác nhận VIỆC KIA đã xong, trong khi
            lượt chạy hoàn thành không có nghĩa cánh tay đã làm được gì.

            `MessageStatusLine` bên dưới (NGOÀI khối này, cùng chỗ với mốc giờ)
            không lặp lại lỗi đó: nó tách rõ hai trục — "Thực thi" (lượt chạy
            có lỗi kỹ thuật không) và "Xác minh" (kết quả vật lý có được xác
            nhận không) — không bao giờ gộp thành một badge, và không đứng
            ngay dưới chữ tường thuật nên không đọc nhầm thành xác nhận nó. */}
      </div>

      {/* Failed send — inline retry, never an auto-retry (P04 §6) */}
      {isUser && failed && (
        <div className="mt-1 flex items-center gap-2 text-[13px] text-halt-500">
          <span>Gửi thất bại.</span>
          <button
            type="button"
            onClick={() => onRetry?.(message)}
            className="inline-flex items-center gap-1 underline hover:no-underline focus-ring rounded cursor-pointer"
          >
            <RotateCw size={12} />
            Gửi lại
          </button>
        </div>
      )}

      {/* Timestamp + like/dislike — cùng một hàng, chỉ hiện khi hover/focus
          để không rợp chữ giữa hội thoại. Like/dislike chỉ gắn vào câu trả
          lời ĐÃ có chữ: đánh giá một bong bóng còn đang stream dở là đánh
          giá một câu chưa nói xong.

          Đã bấm chọn like/dislike rồi thì CẢ HÀNG đứng yên hiện luôn, không
          chỉ riêng khối nút — bấm xong rồi mốc giờ biến mất chỉ còn trơ hai
          icon không có gì đứng cạnh trông lạc lõng, và người dùng cũng có
          lý do chính đáng để muốn biết mình đã đánh giá câu trả lời lúc
          nào. */}
      <div
        className={`mt-1 flex items-center gap-2 transition-opacity duration-[120ms] ${
          message.feedback ? "opacity-100" : "opacity-0 group-hover:opacity-100 group-focus-within:opacity-100"
        }`}
      >
        <time dateTime={message.created_at} title={absoluteTime} className="eyebrow">
          {new Date(message.created_at).toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit" })}
        </time>

        {!isUser && message.content && onFeedback && (
          <div className="flex items-center gap-0.5">
            <button
              type="button"
              onClick={() => onFeedback(message, message.feedback === "like" ? null : "like")}
              aria-label="Câu trả lời hữu ích"
              aria-pressed={message.feedback === "like"}
              title="Câu trả lời hữu ích"
              className={`p-1 rounded transition-colors duration-[120ms] focus-ring cursor-pointer ${
                message.feedback === "like"
                  ? "text-arc-500"
                  : "text-haze-500 hover:text-paper-50"
              }`}
            >
              <ThumbsUp size={13} fill={message.feedback === "like" ? "currentColor" : "none"} />
            </button>
            <button
              type="button"
              onClick={() => onFeedback(message, message.feedback === "dislike" ? null : "dislike")}
              aria-label="Câu trả lời chưa hữu ích"
              aria-pressed={message.feedback === "dislike"}
              title="Câu trả lời chưa hữu ích"
              className={`p-1 rounded transition-colors duration-[120ms] focus-ring cursor-pointer ${
                message.feedback === "dislike"
                  ? "text-halt-500"
                  : "text-haze-500 hover:text-paper-50"
              }`}
            >
              <ThumbsDown size={13} fill={message.feedback === "dislike" ? "currentColor" : "none"} />
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
