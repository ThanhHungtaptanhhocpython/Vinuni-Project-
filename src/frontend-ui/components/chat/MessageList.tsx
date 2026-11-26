"use client";
/* MessageList — P04 §3
   Auto-scrolls to the bottom on new content, except when the user has scrolled
   more than 100px up — then it holds position and offers a jump button.
   Bubbles are memoised by id + content so telemetry updates elsewhere don't
   re-render the whole conversation (MASTER §16). */

import { useRef, useEffect, useState, useCallback, memo } from "react";
import { ArrowDown } from "lucide-react";
import type { Message } from "@/stores";
import { MessageBubble } from "./MessageBubble";
import { ThinkingLine } from "./ThinkingLine";
import { EmptyState } from "@/components/common/EmptyState";
import { Skeleton } from "@/components/common/Skeleton";
import { useRealtimeStore } from "@/stores";
import { formatTimelineDivider, isSameDay } from "@/lib/utils";

/* Dải phân cách giờ/ngày kiểu Messenger — hiện giữa hội thoại, không phải
 * trên từng tin nhắn. Ngưỡng "lâu lâu" của Messenger không phải khoảng cách
 * TỪNG CẶP tin nhắn liên tiếp, mà là khoảng cách kể từ dải phân cách GẦN
 * NHẤT — một cụm tin nhắn dồn dập nhắn cách nhau vài phút một suốt hai giờ
 * thì vẫn phải chia ra vài mốc, chứ không phải một cụm 5 phút rồi im bặt.
 * Sang ngày khác thì LUÔN chia, bất kể khoảng cách — nếu không, tin nhắn lúc
 * 23:58 và 00:02 hôm sau đứng cạnh nhau mà không ai biết đã sang ngày mới. */
const DIVIDER_GAP_MS = 20 * 60 * 1000;

function withDividers(messages: Message[]): { message: Message; dividerLabel: string | null }[] {
  let lastStampIso: string | null = null;
  return messages.map((message) => {
    const gapTooLarge =
      lastStampIso !== null && new Date(message.created_at).getTime() - new Date(lastStampIso).getTime() > DIVIDER_GAP_MS;
    const showDivider = lastStampIso === null || gapTooLarge || !isSameDay(message.created_at, lastStampIso);
    const dividerLabel = showDivider ? formatTimelineDivider(message.created_at, lastStampIso) : null;
    if (showDivider) lastStampIso = message.created_at;
    return { message, dividerLabel };
  });
}

/* So sánh THAM CHIẾU, không liệt kê từng trường.
 *
 * Bản cũ liệt kê `id`/`content`/`send_status`/`tool_calls` — và quên
 * `agent_steps`. Lúc chốt chặng, `sealAgentSteps` ghi `{...msg, agent_steps}`
 * giữ nguyên cả bốn trường đó, nên comparator trả `true`, React bỏ render, và
 * chặng vừa chốt KHÔNG BAO GIỜ hiện ra. Cùng lúc `runId → null` xoá khối live:
 * chạy xong là trace biến mất sạch.
 *
 * Mọi chỗ sửa message đều tạo object mới bằng spread — không nơi nào mutate tại
 * chỗ — nên so sánh tham chiếu vừa đúng vừa chặt hơn, và không mục ruỗng khi
 * `Message` mọc thêm trường. Đó chính là cách lỗi trên sinh ra. */
const MemoBubble = memo(
  MessageBubble,
  (prev, next) =>
    prev.message === next.message &&
    prev.liveBlocks === next.liveBlocks &&
    prev.showCameraLink === next.showCameraLink &&
    prev.onRetry === next.onRetry &&
    prev.onFeedback === next.onFeedback,
);

interface MessageListProps {
  messages: Message[];
  loading?: boolean;
  onRetry?: (message: Message) => void;
  onFeedback?: (message: Message, feedback: "like" | "dislike" | null) => void;
}

export function MessageList({ messages, loading, onRetry, onFeedback }: MessageListProps) {
  const runId = useRealtimeStore((s) => s.runId);
  const anchorId = useRealtimeStore((s) => s.runAnchorMessageId);
  const runBlocks = useRealtimeStore((s) => s.runBlocks);
  const pendingRun = useRealtimeStore((s) => s.pendingRun);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [pinnedToBottom, setPinnedToBottom] = useState(true);

  const scrollToBottom = useCallback(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, []);

  // Follow new content only while the user is at the bottom (P04 §3).
  // `runBlocks` trong dependency: nếu người dùng đang nhìn lượt chạy còn diễn
  // ra, mỗi khối mới (chữ hoặc tool) nới chiều cao bong bóng mà KHÔNG đổi
  // `messages` — thiếu dependency này thì auto-scroll âm thầm ngừng theo ngay
  // trong lúc trace đang mở, dù `pinnedToBottom` vẫn `true`.
  useEffect(() => {
    if (pinnedToBottom) scrollToBottom();
  }, [messages, runBlocks, pendingRun, pinnedToBottom, scrollToBottom]);

  const handleScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    setPinnedToBottom(distanceFromBottom <= 100);
  }, []);

  if (loading) {
    return (
      <div className="flex-1 min-h-0 px-5 py-4 space-y-4 overflow-hidden">
        {[0, 1, 2].map((i) => (
          <div key={i} className={i % 2 === 1 ? "ml-auto max-w-[76%]" : "max-w-[86%]"}>
            <Skeleton className="h-16 rounded-2xl" />
          </div>
        ))}
      </div>
    );
  }

  const isEmpty = messages.length === 0 && !pendingRun;

  /* Chỉ báo chờ hiện khi lượt chạy vừa bấm gửi (`pendingRun`, C4) HOẶC lượt
     chạy đã mở (`runId`) nhưng chưa có block nào tới — biến mất NGAY khi
     token hoặc tool đầu tiên tới (`pendingRun`/`runBlocks` cùng cập nhật
     trong một action ở store, không có khung hình nào cả hai đều rỗng mà
     dòng này vẫn ẩn). */
  const showThinking = pendingRun || (runId !== null && runBlocks.length === 0);

  const rows = withDividers(messages);

  return (
    <div className="relative flex-1 min-h-0">
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="absolute inset-0 overflow-y-auto px-5 py-4 space-y-4"
        aria-live="polite"
        aria-label="Cuộc trò chuyện với agent"
      >
        {isEmpty ? (
          <div className="h-full flex items-center justify-center">
            <EmptyState
              className="!bg-transparent !border-0"
              message="Chưa có tin nhắn nào. Gõ lệnh bên dưới để agent bắt đầu quan sát và thực thi."
            />
          </div>
        ) : (
          <>
            {rows.map(({ message: msg, dividerLabel }) => (
              <div key={msg.id}>
                {dividerLabel && (
                  // `mb-3` chứ không phải `py-*` đều hai bên: khoảng cách với
                  // TIN NHẮN PHÍA TRƯỚC đã có `space-y-4` của khung cha lo,
                  // thứ đang thiếu là khoảng cách xuống bong bóng NGAY DƯỚI —
                  // trước đây chỉ 4px nên dải phân cách dính sát vào chữ.
                  <div className="flex justify-center pt-1 mb-3" aria-hidden="true">
                    <span className="eyebrow text-haze-500">{dividerLabel}</span>
                  </div>
                )}
                <MemoBubble
                  message={msg}
                  /* Dòng thời gian đang chạy chỉ chảy vào ĐÚNG bong bóng anchor
                     của lượt này. Truyền cho mọi bong bóng thì lượt cũ cũng
                     hiện lại trace của lượt mới. */
                  liveBlocks={runId !== null && msg.id === anchorId ? runBlocks : undefined}
                  showCameraLink
                  onRetry={onRetry}
                  onFeedback={onFeedback}
                />
              </div>
            ))}

            {showThinking && (
              <div className="max-w-[86%]">
                <ThinkingLine />
              </div>
            )}
          </>
        )}
      </div>

      {/* Jump-to-latest — shown while auto-scroll is paused. Solid panel, never
          glass (MASTER §9.2). */}
      {!pinnedToBottom && !isEmpty && (
        <button
          type="button"
          onClick={() => {
            scrollToBottom();
            setPinnedToBottom(true);
          }}
          className="panel absolute bottom-4 left-1/2 -translate-x-1/2 px-4 py-2 text-[14px] font-medium text-paper-50 inline-flex items-center gap-1.5 z-10 focus-ring cursor-pointer hover:border-[var(--line-strong)]"
        >
          <ArrowDown size={14} />
          Tin nhắn mới
        </button>
      )}
    </div>
  );
}
