"use client";
/* ThinkingLine — chỉ báo chờ hiện NGAY khoảnh khắc bấm gửi (C3/C4).
 *
 * Trước đây khoảng trống đầu tiên (round-trip HTTP của `POST /chat/start`,
 * rồi tới `run.started`, rồi tới token đầu) không có gì cả — người dùng nhìn
 * màn hình trống. Dòng này lấp đúng khoảng đó: `pendingRun` bật đồng bộ ngay
 * đầu `startRun` (`useSessionStream.ts`), tắt ngay khi khối ĐẦU TIÊN
 * (`token.delta` hoặc `tool.started`) của lượt chạy tới — không bao giờ
 * chồng lên nội dung thật (`MessageList` quyết định điều kiện hiện/ẩn).
 *
 * Nhãn lấy từ `agentStatus` (tập đóng §13.2, `STAGE_LABEL_VI` qua store) khi
 * backend đã phát được sự kiện đầu tiên; trước đó là "Đang suy nghĩ…" — không
 * suy diễn gì thêm ngoài hai nguồn này. */

import { useState } from "react";
import { useTicker } from "@/hooks/useTicker";
import { formatDurationMs } from "@/lib/utils";
import { useRealtimeStore } from "@/stores";

export function ThinkingLine() {
  const agentStatus = useRealtimeStore((s) => s.agentStatus);
  // Mốc bắt đầu tính ĐÚNG một lần khi dòng này xuất hiện lần đầu — component
  // chỉ được mount trong lúc `pendingRun` (hoặc chưa có block nào), nên mốc
  // này khớp với đúng khoảnh khắc người dùng bấm gửi.
  const [startedAt] = useState(() => Date.now());
  const now = useTicker(true);
  const elapsed = now > startedAt ? now - startedAt : 0;

  return (
    <div className="flex items-center gap-2 px-1 py-0.5 text-[14px] text-haze-300">
      <span className="flex gap-0.5" aria-hidden="true">
        <span className="w-1.5 h-1.5 rounded-full bg-arc-400 animate-pulse-dot" />
        <span className="w-1.5 h-1.5 rounded-full bg-arc-400 animate-pulse-dot [animation-delay:150ms]" />
        <span className="w-1.5 h-1.5 rounded-full bg-arc-400 animate-pulse-dot [animation-delay:300ms]" />
      </span>
      <span className="flex-1">{agentStatus ?? "Đang suy nghĩ…"}</span>
      <span className="data text-[12px] text-haze-500">{formatDurationMs(elapsed)}</span>
    </div>
  );
}
